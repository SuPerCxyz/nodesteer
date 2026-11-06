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
	if version != 29 {
		t.Fatalf("schema version = %d, want 29", version)
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

func TestMigrationV29BackfillsSeriesDirectory(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
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
		if migration.version > 28 {
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
	if _, err := db.Exec(`INSERT INTO node_metric_samples (node_id, metric, series_key, timestamp, value, quality, dimensions_json)
		VALUES ('node-old', 'cpu.usage', 'cpu.usage|cpu=all', '2026-01-01T00:00:00.000000000Z', 1, 1, '{"cpu":"all"}')`); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`INSERT INTO node_metric_rollups
		(node_id, metric, series_key, tier, bucket_start, "count", "expected", "missing", "sum", "min", "max", "first", "last", first_ts, last_ts, quality)
		VALUES ('node-old', 'memory.usage', 'memory.usage', '1m', '2026-01-01T00:00:00.000000000Z', 1, 1, 0, 1, 1, 1, 1, 1,
		'2026-01-01T00:00:00.000000000Z', '2026-01-01T00:00:00.000000000Z', 0)`); err != nil {
		t.Fatal(err)
	}
	if err := Migrate(db); err != nil {
		t.Fatalf("migrate to v29: %v", err)
	}
	rows, err := db.Query(`SELECT metric, series_key, dimensions_json FROM node_metric_series WHERE node_id='node-old' ORDER BY metric`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	found := map[string]string{}
	for rows.Next() {
		var metric, seriesKey, dimensions string
		if err := rows.Scan(&metric, &seriesKey, &dimensions); err != nil {
			t.Fatal(err)
		}
		found[metric] = seriesKey + "|" + dimensions
	}
	if len(found) != 2 {
		t.Fatalf("series directory rows = %d, want 2: %v", len(found), found)
	}
	if found["cpu.usage"] == "" || found["memory.usage"] == "" {
		t.Fatalf("series directory missing raw/rollup series: %v", found)
	}
}
