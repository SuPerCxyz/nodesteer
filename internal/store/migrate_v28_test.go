package store

import (
	"database/sql"
	"path/filepath"
	"strings"
	"testing"
)

func TestMigrationV28CreatesQueryIndexes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "hub.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if _, err := db.Exec(`CREATE TABLE schema_migrations (
		version INTEGER PRIMARY KEY,
		name TEXT NOT NULL,
		applied_at TEXT NOT NULL DEFAULT (datetime('now'))
	)`); err != nil {
		t.Fatal(err)
	}
	for _, migration := range migrations {
		if migration.version > 27 {
			continue
		}
		if _, err := db.Exec(migration.sql); err != nil {
			t.Fatalf("apply migration %d: %v", migration.version, err)
		}
		if _, err := db.Exec(`INSERT INTO schema_migrations (version, name) VALUES (?, ?)`,
			migration.version, migration.name); err != nil {
			t.Fatalf("record migration %d: %v", migration.version, err)
		}
	}
	if err := Migrate(db); err != nil {
		t.Fatalf("migrate to v28: %v", err)
	}
	var version int
	if err := db.QueryRow(`SELECT MAX(version) FROM schema_migrations`).Scan(&version); err != nil {
		t.Fatal(err)
	}
	if version != 28 {
		t.Fatalf("schema version = %d, want 28", version)
	}
	for table, want := range map[string][]string{
		"executions": {
			"idx_executions_created_at",
			"idx_executions_node_created_at",
			"idx_executions_status_created_at",
		},
		"file_transfers": {
			"idx_file_transfers_created_at",
			"idx_file_transfers_source_created_at",
		},
		"audit_logs": {"idx_audit_logs_created_at"},
	} {
		rows, err := db.Query(`SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?`, table)
		if err != nil {
			t.Fatal(err)
		}
		found := map[string]bool{}
		for rows.Next() {
			var name string
			if err := rows.Scan(&name); err != nil {
				rows.Close()
				t.Fatal(err)
			}
			found[name] = true
		}
		rows.Close()
		for _, index := range want {
			if !found[index] {
				t.Fatalf("%s missing index %s (found %v)", table, index, found)
			}
		}
	}
}

func TestExecutionListUsesCreatedAtIndex(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	rows, err := st.DB().Query(`EXPLAIN QUERY PLAN SELECT id FROM executions ORDER BY created_at DESC LIMIT 10`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	plan := ""
	for rows.Next() {
		var id, parent, notUsed int
		var detail string
		if err := rows.Scan(&id, &parent, &notUsed, &detail); err != nil {
			t.Fatal(err)
		}
		plan += detail + "\n"
	}
	if !strings.Contains(plan, "idx_executions_created_at") {
		t.Fatalf("query plan does not use created_at index:\n%s", plan)
	}
}
