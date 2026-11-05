package store

import (
	"context"
	"database/sql"
	"errors"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestMigrationV26AddsNodeDisplayName 覆盖 v25 旧库升级：新增列默认空，
// 既有节点与 hostname 不被改写，重复启动幂等。
func TestMigrationV26AddsNodeDisplayName(t *testing.T) {
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
		if migration.version > 25 {
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
	if _, err := db.Exec(`INSERT INTO nodes (id, agent_id, hostname, status) VALUES ('legacy-node', 'agent-legacy', 'legacy-host', 'online')`); err != nil {
		t.Fatalf("insert legacy node: %v", err)
	}

	if err := Migrate(db); err != nil {
		t.Fatalf("migrate v25 -> v26: %v", err)
	}

	var version int
	if err := db.QueryRow(`SELECT MAX(version) FROM schema_migrations`).Scan(&version); err != nil {
		t.Fatalf("read schema version: %v", err)
	}
	if version != 27 {
		t.Fatalf("schema version = %d, want 27", version)
	}
	var hostname, displayName string
	if err := db.QueryRow(`SELECT hostname, display_name FROM nodes WHERE id = 'legacy-node'`).Scan(&hostname, &displayName); err != nil {
		t.Fatalf("read legacy node: %v", err)
	}
	if hostname != "legacy-host" {
		t.Fatalf("hostname = %q, want legacy-host", hostname)
	}
	if displayName != "" {
		t.Fatalf("display_name = %q, want empty default", displayName)
	}

	// 已应用迁移的库再次启动必须幂等。
	if err := Migrate(db); err != nil {
		t.Fatalf("migrate twice: %v", err)
	}
}

// TestMigrationV26AppliesAfterV27 覆盖与 add-agent-inbound-connection 的乱序应用：
// 模拟 v27 先合入并记录，再启动当前版本时仍能补应用 v26，且列默认值正确。
func TestMigrationV26AppliesAfterV27(t *testing.T) {
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
		if migration.version > 25 {
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
	// 模拟 v27 先合入并记录，再启动当前版本时仍能补应用 v26。
	for _, migration := range migrations {
		if migration.version != 27 {
			continue
		}
		if _, err := db.Exec(migration.sql); err != nil {
			t.Fatalf("apply v27 before v26: %v", err)
		}
		if _, err := db.Exec(`INSERT INTO schema_migrations (version, name) VALUES (?, ?)`,
			migration.version, migration.name); err != nil {
			t.Fatalf("record v27: %v", err)
		}
	}
	if _, err := db.Exec(`INSERT INTO nodes (id, agent_id, hostname, status) VALUES ('legacy-node', 'agent-legacy', 'legacy-host', 'online')`); err != nil {
		t.Fatalf("insert legacy node: %v", err)
	}

	if err := Migrate(db); err != nil {
		t.Fatalf("migrate v25+v27 -> v26: %v", err)
	}
	var displayName string
	if err := db.QueryRow(`SELECT display_name FROM nodes WHERE id = 'legacy-node'`).Scan(&displayName); err != nil {
		t.Fatalf("read display_name: %v", err)
	}
	if displayName != "" {
		t.Fatalf("display_name = %q, want empty default", displayName)
	}
	var v26 int
	if err := db.QueryRow(`SELECT COUNT(*) FROM schema_migrations WHERE version = 26`).Scan(&v26); err != nil || v26 != 1 {
		t.Fatalf("v26 migration not recorded: count=%d err=%v", v26, err)
	}
}

// TestNodeDisplayNameUpsertAndUpdate 覆盖：首次 INSERT 可写入、Upsert 冲突不覆盖、
// 专用更新方法校验行数、所有读取路径返回该字段。
func TestNodeDisplayNameUpsertAndUpdate(t *testing.T) {
	st, err := OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	defer st.Close()
	ctx := context.Background()

	node := &models.Node{
		ID: "node-1", AgentID: "agent-1", Hostname: "host-1",
		DisplayName: "生产数据库 01", Status: models.NodeStatusOnline,
	}
	if err := st.UpsertNode(ctx, node); err != nil {
		t.Fatalf("insert node: %v", err)
	}
	got, err := st.GetNode(ctx, "node-1")
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.DisplayName != "生产数据库 01" {
		t.Fatalf("display_name after insert = %q", got.DisplayName)
	}

	// 模拟 Agent 重新注册/Upsert：hostname 更新，display_name 必须保留。
	node.Hostname = "host-renamed-by-agent"
	node.DisplayName = "agent-value-must-not-win"
	if err := st.UpsertNode(ctx, node); err != nil {
		t.Fatalf("upsert existing node: %v", err)
	}
	got, err = st.GetNodeByAgentID(ctx, "agent-1")
	if err != nil {
		t.Fatalf("get node by agent id: %v", err)
	}
	if got.Hostname != "host-renamed-by-agent" {
		t.Fatalf("hostname = %q, want updated value", got.Hostname)
	}
	if got.DisplayName != "生产数据库 01" {
		t.Fatalf("display_name after upsert = %q, want preserved", got.DisplayName)
	}

	nodes, err := st.ListNodes(ctx)
	if err != nil || len(nodes) != 1 {
		t.Fatalf("list nodes = %+v err=%v", nodes, err)
	}
	if nodes[0].DisplayName != "生产数据库 01" {
		t.Fatalf("list display_name = %q", nodes[0].DisplayName)
	}

	if err := st.UpdateNodeDisplayName(ctx, "node-1", "新名称"); err != nil {
		t.Fatalf("update display name: %v", err)
	}
	got, _ = st.GetNode(ctx, "node-1")
	if got.DisplayName != "新名称" {
		t.Fatalf("display_name after update = %q", got.DisplayName)
	}

	// 清除显示名。
	if err := st.UpdateNodeDisplayName(ctx, "node-1", ""); err != nil {
		t.Fatalf("clear display name: %v", err)
	}
	got, _ = st.GetNode(ctx, "node-1")
	if got.DisplayName != "" {
		t.Fatalf("display_name after clear = %q, want empty", got.DisplayName)
	}

	if err := st.UpdateNodeDisplayName(ctx, "missing", "x"); !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("update missing node err = %v, want sql.ErrNoRows", err)
	}
}
