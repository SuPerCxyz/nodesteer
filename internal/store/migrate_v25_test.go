package store

import (
	"database/sql"
	"testing"
)

// TestMigrationV25DropsLegacyBindingsAndAddsRollupIndex 模拟 v24 旧库升级：
// 保留既有节点数据，删除不再被任何代码引用的 monitoring_node_bindings，
// 并补充 node_metric_rollups 的 (node_id, tier, bucket_start) 索引。
func TestMigrationV25DropsLegacyBindingsAndAddsRollupIndex(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()

	if _, err := db.Exec(`CREATE TABLE schema_migrations (
		version INTEGER PRIMARY KEY,
		name TEXT NOT NULL,
		applied_at TEXT NOT NULL DEFAULT (datetime('now'))
	)`); err != nil {
		t.Fatalf("create schema_migrations: %v", err)
	}
	for _, migration := range migrations {
		if migration.version > 24 {
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
	if _, err := db.Exec(`INSERT INTO nodes (id, agent_id, hostname, status) VALUES ('legacy-node', 'agent-legacy', 'legacy-node', 'online')`); err != nil {
		t.Fatalf("insert legacy node: %v", err)
	}
	if _, err := db.Exec(`INSERT INTO monitoring_node_bindings (node_id, nocturne_node_id) VALUES ('legacy-node', 'nocturne-1')`); err != nil {
		t.Fatalf("insert legacy binding: %v", err)
	}

	if err := Migrate(db); err != nil {
		t.Fatalf("migrate v24 -> v25: %v", err)
	}

	var version int
	if err := db.QueryRow(`SELECT MAX(version) FROM schema_migrations`).Scan(&version); err != nil {
		t.Fatalf("read schema version: %v", err)
	}
	if version != 29 {
		t.Fatalf("schema version = %d, want 29", version)
	}
	var legacyTable int
	if err := db.QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'monitoring_node_bindings'`).Scan(&legacyTable); err != nil {
		t.Fatalf("query legacy table: %v", err)
	}
	if legacyTable != 0 {
		t.Fatal("monitoring_node_bindings must be dropped by migration v25")
	}
	var rollupIndex int
	if err := db.QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_node_metric_rollups_node_tier_time'`).Scan(&rollupIndex); err != nil {
		t.Fatalf("query rollup index: %v", err)
	}
	if rollupIndex != 1 {
		t.Fatal("idx_node_metric_rollups_node_tier_time must exist after migration v25")
	}
	var nodes int
	if err := db.QueryRow(`SELECT COUNT(*) FROM nodes WHERE id = 'legacy-node'`).Scan(&nodes); err != nil {
		t.Fatalf("query preserved node: %v", err)
	}
	if nodes != 1 {
		t.Fatal("migration v25 must not touch unrelated node data")
	}

	// 迁移记录已存在时必须幂等，不得因 DROP TABLE IF EXISTS 之外的重复执行失败。
	if err := Migrate(db); err != nil {
		t.Fatalf("migrate twice: %v", err)
	}
}
