package migration

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"

	"github.com/lib/pq"

	gomigrate "github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/postgres"
	_ "github.com/golang-migrate/migrate/v4/source/file"
)

const (
	RequiredVersion = 4
	VersionTable    = "schema_migrations"
)

var (
	ErrSchemaNotMigrated = errors.New("database schema is not migrated")
	ErrSchemaDirty       = errors.New("database schema migration is dirty")
	ErrSchemaOutdated    = errors.New("database schema migration version is outdated")
)

type Options struct {
	DatabaseURL    string
	MigrationsPath string
}

type VersionState struct {
	Initialized bool
	Version     uint
	Dirty       bool
	Required    uint
}

func Up(opts Options) error {
	m, err := newMigrator(opts)
	if err != nil {
		return err
	}
	defer closeMigrator(m)

	if err := m.Up(); err != nil && !errors.Is(err, gomigrate.ErrNoChange) {
		return err
	}
	return nil
}

func Down(opts Options, steps int) error {
	if steps <= 0 {
		return fmt.Errorf("down steps must be greater than zero")
	}
	m, err := newMigrator(opts)
	if err != nil {
		return err
	}
	defer closeMigrator(m)

	if err := m.Steps(-steps); err != nil && !errors.Is(err, gomigrate.ErrNoChange) {
		return err
	}
	return nil
}

func Version(opts Options) (VersionState, error) {
	m, err := newMigrator(opts)
	if err != nil {
		return VersionState{}, err
	}
	defer closeMigrator(m)

	version, dirty, err := m.Version()
	if errors.Is(err, gomigrate.ErrNilVersion) {
		return VersionState{Required: RequiredVersion}, nil
	}
	if err != nil {
		return VersionState{}, err
	}
	return VersionState{
		Initialized: true,
		Version:     version,
		Dirty:       dirty,
		Required:    RequiredVersion,
	}, nil
}

func Baseline(ctx context.Context, opts Options, version uint) error {
	if version == 0 {
		return fmt.Errorf("baseline version must be greater than zero")
	}

	db, err := sql.Open("postgres", opts.DatabaseURL)
	if err != nil {
		return err
	}
	defer db.Close()

	if err := db.PingContext(ctx); err != nil {
		return err
	}
	if err := ValidateBaselineSchema(ctx, db); err != nil {
		return err
	}

	m, err := newMigrator(opts)
	if err != nil {
		return err
	}
	defer closeMigrator(m)

	return m.Force(int(version))
}

func CheckRequiredVersion(ctx context.Context, db *sql.DB) error {
	state, err := CurrentVersion(ctx, db)
	if err != nil {
		return err
	}
	if !state.Initialized {
		return fmt.Errorf("%w; run .\\scripts\\dev.ps1 -Task migrate-up or baseline an existing compatible database", ErrSchemaNotMigrated)
	}
	if state.Dirty {
		return fmt.Errorf("%w at version %d; repair migration state before starting gateway", ErrSchemaDirty, state.Version)
	}
	if state.Version < RequiredVersion {
		return fmt.Errorf("%w: current=%d required=%d; run migrations before starting gateway", ErrSchemaOutdated, state.Version, RequiredVersion)
	}
	return nil
}

func CurrentVersion(ctx context.Context, db *sql.DB) (VersionState, error) {
	state := VersionState{Required: RequiredVersion}
	var version int64
	row := db.QueryRowContext(ctx, "SELECT version, dirty FROM "+VersionTable+" LIMIT 1")
	if err := row.Scan(&version, &state.Dirty); err != nil {
		if errors.Is(err, sql.ErrNoRows) || isUndefinedTable(err) {
			return state, nil
		}
		return VersionState{}, err
	}
	if version < 0 {
		return VersionState{}, fmt.Errorf("invalid migration version: %d", version)
	}
	state.Initialized = true
	state.Version = uint(version)
	return state, nil
}

func ValidateBaselineSchema(ctx context.Context, db *sql.DB) error {
	requiredColumns := map[string][]string{
		"tasks": {
			"id", "user_id", "prompt", "status", "error", "replay_of_task_id", "metadata", "created_at", "updated_at",
		},
		"task_events": {
			"id", "task_id", "event_type", "message", "token", "trace_id", "schema_version", "event_name", "payload", "emitted_at_unix_ms", "created_at",
		},
		"dead_letter_tasks": {
			"task_id", "reason", "attempts", "created_at", "updated_at",
		},
		"auth_users": {
			"username", "password_hash", "role", "created_at", "updated_at",
		},
		"auth_sessions": {
			"token", "username", "role", "expires_at", "created_at",
		},
		"tool_policies": {
			"id", "role_allow", "approval_required", "disabled_tools", "version", "updated_at", "updated_by", "description",
		},
	}

	var missing []string
	for table, columns := range requiredColumns {
		for _, column := range columns {
			ok, err := hasColumn(ctx, db, table, column)
			if err != nil {
				return err
			}
			if !ok {
				missing = append(missing, table+"."+column)
			}
		}
	}

	for _, index := range []string{
		"idx_task_events_task_id_id",
		"idx_tasks_user_conversation_created",
		"idx_tasks_replay_of_created",
		"idx_auth_sessions_username",
		"idx_auth_sessions_expires_at",
	} {
		ok, err := hasIndex(ctx, db, index)
		if err != nil {
			return err
		}
		if !ok {
			missing = append(missing, "index."+index)
		}
	}

	if len(missing) > 0 {
		sort.Strings(missing)
		return fmt.Errorf("baseline refused: existing schema does not match required version %d; missing %s", RequiredVersion, strings.Join(missing, ", "))
	}
	return nil
}

func Create(migrationsPath, name string) ([]string, error) {
	normalizedName, err := normalizeMigrationName(name)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(migrationsPath, 0o755); err != nil {
		return nil, err
	}

	nextVersion, err := nextMigrationVersion(migrationsPath)
	if err != nil {
		return nil, err
	}

	base := fmt.Sprintf("%06d_%s", nextVersion, normalizedName)
	paths := []string{
		filepath.Join(migrationsPath, base+".up.sql"),
		filepath.Join(migrationsPath, base+".down.sql"),
	}
	contents := []string{
		"-- Write forward migration SQL here.\n",
		"-- Write rollback SQL here. Document any data loss before dropping columns or tables.\n",
	}
	for i, path := range paths {
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err != nil {
			return nil, err
		}
		if _, err := file.WriteString(contents[i]); err != nil {
			_ = file.Close()
			return nil, err
		}
		if err := file.Close(); err != nil {
			return nil, err
		}
	}
	return paths, nil
}

func FileSourceURL(path string) (string, error) {
	if strings.TrimSpace(path) == "" {
		return "", fmt.Errorf("migrations path is required")
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	slashPath := filepath.ToSlash(abs)
	if runtime.GOOS == "windows" && len(slashPath) >= 2 && slashPath[1] == ':' {
		slashPath = "/" + slashPath
	}
	return (&url.URL{Scheme: "file", Path: slashPath}).String(), nil
}

func newMigrator(opts Options) (*gomigrate.Migrate, error) {
	if strings.TrimSpace(opts.DatabaseURL) == "" {
		return nil, fmt.Errorf("database URL is required")
	}
	sourceURL, err := FileSourceURL(opts.MigrationsPath)
	if err != nil {
		return nil, err
	}
	return gomigrate.New(sourceURL, opts.DatabaseURL)
}

func closeMigrator(m *gomigrate.Migrate) {
	sourceErr, databaseErr := m.Close()
	if sourceErr != nil || databaseErr != nil {
		// Close errors do not change migration outcome and cannot be handled by callers here.
		return
	}
}

func hasColumn(ctx context.Context, db *sql.DB, tableName string, columnName string) (bool, error) {
	var exists bool
	err := db.QueryRowContext(
		ctx,
		`SELECT EXISTS (
		   SELECT 1
		   FROM information_schema.columns
		   WHERE table_schema = current_schema()
		     AND table_name = $1
		     AND column_name = $2
		 )`,
		tableName,
		columnName,
	).Scan(&exists)
	return exists, err
}

func hasIndex(ctx context.Context, db *sql.DB, indexName string) (bool, error) {
	var exists bool
	err := db.QueryRowContext(
		ctx,
		`SELECT EXISTS (
		   SELECT 1
		   FROM pg_indexes
		   WHERE schemaname = current_schema()
		     AND indexname = $1
		 )`,
		indexName,
	).Scan(&exists)
	return exists, err
}

func isUndefinedTable(err error) bool {
	var pqErr *pq.Error
	return errors.As(err, &pqErr) && pqErr.Code == "42P01"
}

func normalizeMigrationName(name string) (string, error) {
	name = strings.ToLower(strings.TrimSpace(name))
	if name == "" {
		return "", fmt.Errorf("migration name is required")
	}
	name = strings.ReplaceAll(name, "-", "_")
	name = strings.ReplaceAll(name, " ", "_")
	valid := regexp.MustCompile(`^[a-z0-9_]+$`)
	if !valid.MatchString(name) {
		return "", fmt.Errorf("migration name must contain only letters, digits, underscore, dash, or space")
	}
	return name, nil
}

func nextMigrationVersion(path string) (int, error) {
	entries, err := os.ReadDir(path)
	if err != nil {
		return 0, err
	}
	versionPattern := regexp.MustCompile(`^(\d+)_.*\.(up|down)\.sql$`)
	maxVersion := 0
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		match := versionPattern.FindStringSubmatch(entry.Name())
		if len(match) != 3 {
			continue
		}
		var version int
		if _, err := fmt.Sscanf(match[1], "%d", &version); err != nil {
			return 0, err
		}
		if version > maxVersion {
			maxVersion = version
		}
	}
	return maxVersion + 1, nil
}
