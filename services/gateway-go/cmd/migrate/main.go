package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/migration"
)

func main() {
	log.SetFlags(0)

	command, args := splitCommand(os.Args[1:])
	fs := flag.NewFlagSet("migrate", flag.ExitOnError)
	databaseURL := fs.String("database-url", firstNonBlank(os.Getenv("SYNAPSE_DATABASE_URL"), os.Getenv("DATABASE_URL")), "PostgreSQL connection URL")
	migrationsPath := fs.String("path", defaultMigrationsPath(), "migration directory")
	steps := fs.Int("steps", 1, "number of migrations to roll back for down")
	version := fs.Uint("version", migration.RequiredVersion, "baseline version")
	name := fs.String("name", "", "migration name for create")
	if err := fs.Parse(args); err != nil {
		log.Fatal(err)
	}

	opts := migration.Options{
		DatabaseURL:    strings.TrimSpace(*databaseURL),
		MigrationsPath: strings.TrimSpace(*migrationsPath),
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	switch command {
	case "up":
		mustHaveDatabaseURL(opts.DatabaseURL)
		if err := migration.Up(opts); err != nil {
			log.Fatalf("migrate up failed: %v", err)
		}
		fmt.Println("migration status: up to date")
	case "down":
		mustHaveDatabaseURL(opts.DatabaseURL)
		if err := migration.Down(opts, *steps); err != nil {
			log.Fatalf("migrate down failed: %v", err)
		}
		fmt.Printf("migration status: rolled back %d step(s)\n", *steps)
	case "status", "version":
		mustHaveDatabaseURL(opts.DatabaseURL)
		state, err := migration.Version(opts)
		if err != nil {
			log.Fatalf("migration status failed: %v", err)
		}
		printVersion(state)
	case "baseline":
		mustHaveDatabaseURL(opts.DatabaseURL)
		if err := migration.Baseline(ctx, opts, uint(*version)); err != nil {
			log.Fatalf("migration baseline failed: %v", err)
		}
		fmt.Printf("migration baseline recorded version %d\n", *version)
	case "create":
		created, err := migration.Create(opts.MigrationsPath, *name)
		if err != nil {
			log.Fatalf("migration create failed: %v", err)
		}
		for _, path := range created {
			fmt.Println(path)
		}
	case "help", "-h", "--help":
		usage(fs)
	default:
		usage(fs)
		log.Fatalf("unknown migration command: %s", command)
	}
}

func splitCommand(args []string) (string, []string) {
	if len(args) == 0 {
		return "status", args
	}
	if strings.HasPrefix(args[0], "-") {
		return "status", args
	}
	return args[0], args[1:]
}

func printVersion(state migration.VersionState) {
	if !state.Initialized {
		fmt.Printf("migration version: none required=%d dirty=false\n", state.Required)
		return
	}
	fmt.Printf("migration version: current=%d required=%d dirty=%s\n", state.Version, state.Required, strconv.FormatBool(state.Dirty))
}

func usage(fs *flag.FlagSet) {
	fmt.Println("usage: migrate <up|down|status|baseline|create> [flags]")
	fs.PrintDefaults()
}

func mustHaveDatabaseURL(value string) {
	if strings.TrimSpace(value) == "" {
		log.Fatal("database URL is required; set SYNAPSE_DATABASE_URL or pass -database-url")
	}
}

func defaultMigrationsPath() string {
	candidates := []string{
		"migrations",
		filepath.Join("services", "gateway-go", "migrations"),
	}
	for _, candidate := range candidates {
		if info, err := os.Stat(candidate); err == nil && info.IsDir() {
			return candidate
		}
	}
	return candidates[0]
}

func firstNonBlank(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}
