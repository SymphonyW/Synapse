package migration_test

import (
	"context"
	"database/sql"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/lib/pq"
	"github.com/synapse/synapse/services/gateway-go/internal/migration"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
)

func TestCreateRejectsUnsafeMigrationName(t *testing.T) {
	t.Parallel()

	_, err := migration.Create(t.TempDir(), "../bad")
	if err == nil {
		t.Fatal("expected unsafe migration name to fail")
	}
}

func TestPostgresMigrationLifecycle(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	databaseURL := isolatedPostgresSchemaURL(t, ctx)
	migrationsPath := gatewayMigrationsPath(t)
	opts := migration.Options{DatabaseURL: databaseURL, MigrationsPath: migrationsPath}

	db := openTestDB(t, databaseURL)
	defer db.Close()

	if _, err := store.NewPostgres(ctx, databaseURL); err == nil {
		t.Fatal("expected NewPostgres to reject an unmigrated schema")
	} else if !strings.Contains(err.Error(), "database schema is not migrated") {
		t.Fatalf("expected clear unmigrated schema error, got %v", err)
	}

	if err := migration.Up(opts); err != nil {
		t.Fatalf("migrate up: %v", err)
	}

	state, err := migration.CurrentVersion(ctx, db)
	if err != nil {
		t.Fatalf("current version: %v", err)
	}
	if !state.Initialized || state.Version != migration.RequiredVersion || state.Dirty {
		t.Fatalf("unexpected version state: %+v", state)
	}

	if err := insertTask(ctx, db, "task-1"); err != nil {
		t.Fatalf("insert task before repeat up: %v", err)
	}
	if err := migration.Up(opts); err != nil {
		t.Fatalf("repeat migrate up: %v", err)
	}
	if got := countTasks(ctx, t, db); got != 1 {
		t.Fatalf("repeat migrate up changed data, task count=%d", got)
	}

	postgresStore, err := store.NewPostgres(ctx, databaseURL)
	if err != nil {
		t.Fatalf("NewPostgres after migrate up: %v", err)
	}
	_ = postgresStore.Close()

	if err := migration.Down(opts, 1); err != nil {
		t.Fatalf("migrate down one step: %v", err)
	}
	state, err = migration.CurrentVersion(ctx, db)
	if err != nil {
		t.Fatalf("current version after down: %v", err)
	}
	if state.Version != migration.RequiredVersion-1 || state.Dirty {
		t.Fatalf("unexpected version after down: %+v", state)
	}
	if hasColumn(ctx, t, db, "tasks", "execution_owner") {
		t.Fatal("expected execution lease column to be removed after down")
	}

	if err := migration.Up(opts); err != nil {
		t.Fatalf("upgrade old version back to current: %v", err)
	}
}

func TestPostgresBaselineExistingEnsureSchemaDatabase(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	databaseURL := isolatedPostgresSchemaURL(t, ctx)
	migrationsPath := gatewayMigrationsPath(t)
	opts := migration.Options{DatabaseURL: databaseURL, MigrationsPath: migrationsPath}

	db := openTestDB(t, databaseURL)
	defer db.Close()

	if err := createEnsureSchemaEquivalent(ctx, db); err != nil {
		t.Fatalf("create ensureSchema equivalent: %v", err)
	}

	if err := migration.Baseline(ctx, opts, 4); err != nil {
		t.Fatalf("baseline compatible schema: %v", err)
	}
	state, err := migration.CurrentVersion(ctx, db)
	if err != nil {
		t.Fatalf("current version after baseline: %v", err)
	}
	if !state.Initialized || state.Version != 4 || state.Dirty {
		t.Fatalf("unexpected baseline state: %+v", state)
	}

	if _, err := store.NewPostgres(ctx, databaseURL); err == nil {
		t.Fatal("expected NewPostgres to reject baseline v4 before v5 migration")
	}

	if err := migration.Up(opts); err != nil {
		t.Fatalf("migrate up after baseline: %v", err)
	}

	postgresStore, err := store.NewPostgres(ctx, databaseURL)
	if err != nil {
		t.Fatalf("NewPostgres after baseline and migrate up: %v", err)
	}
	_ = postgresStore.Close()
}

func TestPostgresBaselineRefusesIncompleteSchema(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	databaseURL := isolatedPostgresSchemaURL(t, ctx)
	migrationsPath := gatewayMigrationsPath(t)
	db := openTestDB(t, databaseURL)
	defer db.Close()

	if _, err := db.ExecContext(ctx, `CREATE TABLE tasks (id TEXT PRIMARY KEY)`); err != nil {
		t.Fatalf("create incomplete schema: %v", err)
	}

	err := migration.Baseline(ctx, migration.Options{DatabaseURL: databaseURL, MigrationsPath: migrationsPath}, migration.RequiredVersion)
	if err == nil {
		t.Fatal("expected baseline to reject incomplete schema")
	}
	if !strings.Contains(err.Error(), "baseline refused") {
		t.Fatalf("expected baseline refused error, got %v", err)
	}
}

func isolatedPostgresSchemaURL(t *testing.T, ctx context.Context) string {
	t.Helper()

	rawURL := strings.TrimSpace(getenv("SYNAPSE_TEST_DATABASE_URL"))
	if rawURL == "" {
		t.Skip("SYNAPSE_TEST_DATABASE_URL is not set")
	}

	adminDB := openTestDB(t, rawURL)

	schemaName := "synapse_migration_test_" + strings.ReplaceAll(uuid.NewString(), "-", "_")
	if _, err := adminDB.ExecContext(ctx, "CREATE SCHEMA "+pq.QuoteIdentifier(schemaName)); err != nil {
		t.Fatalf("create test schema: %v", err)
	}
	t.Cleanup(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_, _ = adminDB.ExecContext(cleanupCtx, "DROP SCHEMA IF EXISTS "+pq.QuoteIdentifier(schemaName)+" CASCADE")
		_ = adminDB.Close()
	})

	parsed, err := url.Parse(rawURL)
	if err != nil {
		t.Fatalf("parse database URL: %v", err)
	}
	query := parsed.Query()
	query.Set("search_path", schemaName)
	parsed.RawQuery = query.Encode()
	return parsed.String()
}

func gatewayMigrationsPath(t *testing.T) string {
	t.Helper()

	path, err := filepath.Abs("../../migrations")
	if err != nil {
		t.Fatalf("resolve migrations path: %v", err)
	}
	return path
}

func openTestDB(t *testing.T, databaseURL string) *sql.DB {
	t.Helper()

	db, err := sql.Open("postgres", databaseURL)
	if err != nil {
		t.Fatalf("open postgres: %v", err)
	}
	if err := db.Ping(); err != nil {
		_ = db.Close()
		t.Fatalf("ping postgres: %v", err)
	}
	return db
}

func insertTask(ctx context.Context, db *sql.DB, id string) error {
	_, err := db.ExecContext(
		ctx,
		`INSERT INTO tasks (id, user_id, prompt, status, error, metadata, created_at, updated_at)
		 VALUES ($1, 'user-1', 'prompt', 'queued', '', '{}'::jsonb, NOW(), NOW())`,
		id,
	)
	return err
}

func countTasks(ctx context.Context, t *testing.T, db *sql.DB) int {
	t.Helper()

	var count int
	if err := db.QueryRowContext(ctx, `SELECT COUNT(*) FROM tasks`).Scan(&count); err != nil {
		t.Fatalf("count tasks: %v", err)
	}
	return count
}

func hasColumn(ctx context.Context, t *testing.T, db *sql.DB, table string, column string) bool {
	t.Helper()

	var exists bool
	if err := db.QueryRowContext(
		ctx,
		`SELECT EXISTS (
		   SELECT 1
		   FROM information_schema.columns
		   WHERE table_schema = current_schema()
		     AND table_name = $1
		     AND column_name = $2
		 )`,
		table,
		column,
	).Scan(&exists); err != nil {
		t.Fatalf("check column %s.%s: %v", table, column, err)
	}
	return exists
}

func createEnsureSchemaEquivalent(ctx context.Context, db *sql.DB) error {
	_, err := db.ExecContext(
		ctx,
		`CREATE TABLE tasks (
		  id TEXT PRIMARY KEY,
		  user_id TEXT NOT NULL,
		  prompt TEXT NOT NULL,
		  status TEXT NOT NULL,
		  error TEXT NOT NULL DEFAULT '',
		  replay_of_task_id TEXT NULL REFERENCES tasks(id) ON DELETE SET NULL,
		  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
		  created_at TIMESTAMPTZ NOT NULL,
		  updated_at TIMESTAMPTZ NOT NULL
		);

		CREATE TABLE task_events (
		  id BIGSERIAL PRIMARY KEY,
		  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
		  event_type TEXT NOT NULL,
		  message TEXT NOT NULL DEFAULT '',
		  token TEXT NOT NULL DEFAULT '',
		  trace_id TEXT NOT NULL DEFAULT '',
		  schema_version TEXT NOT NULL DEFAULT '',
		  event_name TEXT NOT NULL DEFAULT '',
		  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
		  emitted_at_unix_ms BIGINT NOT NULL,
		  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);

		CREATE TABLE dead_letter_tasks (
		  task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
		  reason TEXT NOT NULL,
		  attempts INTEGER NOT NULL,
		  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
		  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);

		CREATE TABLE auth_users (
		  username TEXT PRIMARY KEY,
		  password_hash TEXT NOT NULL,
		  role TEXT NOT NULL,
		  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
		  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);

		CREATE TABLE auth_sessions (
		  token TEXT PRIMARY KEY,
		  username TEXT NOT NULL REFERENCES auth_users(username) ON DELETE CASCADE,
		  role TEXT NOT NULL,
		  expires_at TIMESTAMPTZ NOT NULL,
		  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
		);

		CREATE TABLE tool_policies (
		  id TEXT PRIMARY KEY,
		  role_allow JSONB NOT NULL DEFAULT '{}'::jsonb,
		  approval_required JSONB NOT NULL DEFAULT '[]'::jsonb,
		  disabled_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
		  version BIGINT NOT NULL DEFAULT 0,
		  updated_at TIMESTAMPTZ NOT NULL,
		  updated_by TEXT NOT NULL DEFAULT '',
		  description TEXT NOT NULL DEFAULT ''
		);

		CREATE INDEX idx_task_events_task_id_id ON task_events (task_id, id);
		CREATE INDEX idx_tasks_user_conversation_created
		  ON tasks (user_id, (metadata->>'conversation_id'), created_at DESC);
		CREATE INDEX idx_tasks_replay_of_created
		  ON tasks (replay_of_task_id, created_at DESC);
		CREATE INDEX idx_auth_sessions_username ON auth_sessions (username);
		CREATE INDEX idx_auth_sessions_expires_at ON auth_sessions (expires_at);`,
	)
	return err
}

func getenv(key string) string {
	return strings.TrimSpace(strings.Trim(os.Getenv(key), `"`))
}
