package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func nodeColumnSet(t *testing.T, db *sql.DB) map[string]bool {
	t.Helper()
	rows, err := db.Query(`SELECT name FROM pragma_table_info('nodes')`)
	if err != nil {
		t.Fatalf("read nodes columns: %v", err)
	}
	defer rows.Close()
	columns := map[string]bool{}
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			t.Fatalf("scan nodes column: %v", err)
		}
		columns[name] = true
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("iterate nodes columns: %v", err)
	}
	return columns
}

func TestFreshDatabaseHasInboundColumns(t *testing.T) {
	st, err := OpenInMemory()
	if err != nil {
		t.Fatalf("open fresh store: %v", err)
	}
	defer st.Close()
	columns := nodeColumnSet(t, st.DB())
	for _, column := range []string{
		"endpoint", "connection_mode", "inbound_provisioned", "inbound_cert_fingerprint",
		"inbound_cert_serial", "inbound_cert_not_after", "inbound_token_enc",
		"inbound_last_handshake", "inbound_last_error", "endpoint_updated_at", "endpoint_updated_by",
	} {
		if !columns[column] {
			t.Fatalf("fresh database missing nodes.%s", column)
		}
	}
}

func TestMigrationV27AddsInboundColumns(t *testing.T) {
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
		if migration.version > 26 {
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
		t.Fatalf("migrate v26 -> v27: %v", err)
	}

	var version int
	if err := db.QueryRow(`SELECT MAX(version) FROM schema_migrations`).Scan(&version); err != nil {
		t.Fatalf("read schema version: %v", err)
	}
	if version != 29 {
		t.Fatalf("schema version = %d, want 29", version)
	}
	columns := nodeColumnSet(t, db)
	for _, column := range []string{
		"endpoint", "connection_mode", "inbound_provisioned", "inbound_cert_fingerprint",
		"inbound_cert_serial", "inbound_cert_not_after", "inbound_token_enc",
		"inbound_last_handshake", "inbound_last_error", "endpoint_updated_at", "endpoint_updated_by",
	} {
		if !columns[column] {
			t.Fatalf("nodes.%s was not added by v27", column)
		}
	}
	var hostname, endpoint, connectionMode string
	var provisioned int
	if err := db.QueryRow(`SELECT hostname, endpoint, connection_mode, inbound_provisioned FROM nodes WHERE id = 'legacy-node'`).
		Scan(&hostname, &endpoint, &connectionMode, &provisioned); err != nil {
		t.Fatalf("read migrated node: %v", err)
	}
	if hostname != "legacy-host" || endpoint != "" || connectionMode != "" || provisioned != 0 {
		t.Fatalf("unexpected migrated row: hostname=%q endpoint=%q mode=%q provisioned=%d",
			hostname, endpoint, connectionMode, provisioned)
	}
	var index int
	if err := db.QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_nodes_inbound_candidates'`).Scan(&index); err != nil {
		t.Fatalf("query inbound index: %v", err)
	}
	if index != 1 {
		t.Fatal("idx_nodes_inbound_candidates must exist after v27")
	}

	// 重复启动幂等。
	if err := Migrate(db); err != nil {
		t.Fatalf("migrate twice: %v", err)
	}
}

func TestNodeInboundUpsertPreservesControlFields(t *testing.T) {
	st, err := OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	defer st.Close()
	ctx := context.Background()

	node := &models.Node{
		ID: "node-inbound", AgentID: "agent-inbound", Hostname: "host-1",
		Endpoint: "agent.example.com:18444", Status: models.NodeStatusOnline,
		Capabilities: map[string]bool{
			models.CapInboundConnection: true,
			models.CapInboundDataPlane:  true,
		},
	}
	if err := st.UpsertNode(ctx, node); err != nil {
		t.Fatalf("insert node: %v", err)
	}
	notAfter := time.Now().Add(24 * time.Hour).UTC().Truncate(time.Second)
	if err := st.UpdateNodeInboundIdentity(ctx, node.ID, true, "abcdef", "serial-1", notAfter, "enc-token"); err != nil {
		t.Fatalf("update inbound identity: %v", err)
	}
	if err := st.UpdateNodeInboundDiagnostics(ctx, node.ID, notAfter, "tcp_timeout"); err != nil {
		t.Fatalf("update inbound diagnostics: %v", err)
	}
	if err := st.UpdateNodeLastConnectionMode(ctx, node.ID, models.ConnectionModeInbound); err != nil {
		t.Fatalf("update connection mode: %v", err)
	}

	// 模拟 Agent 注册/Upsert：endpoint 与全部入站控制字段不得被覆盖。
	node.Endpoint = "attacker.example.com:18444"
	node.InboundProvisioned = false
	node.InboundCertFingerprint = "overwritten"
	node.InboundTokenEnc = "overwritten"
	node.Capabilities = map[string]bool{}
	if err := st.UpsertNode(ctx, node); err != nil {
		t.Fatalf("upsert existing node: %v", err)
	}

	got, err := st.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.Endpoint != "agent.example.com:18444" {
		t.Fatalf("endpoint after upsert = %q", got.Endpoint)
	}
	if !got.InboundProvisioned || got.InboundCertFingerprint != "abcdef" ||
		got.InboundCertSerial != "serial-1" || got.InboundTokenEnc != "enc-token" {
		t.Fatalf("inbound identity overwritten: %+v", got)
	}
	if got.InboundLastError != "tcp_timeout" || got.InboundLastHandshake == "" {
		t.Fatalf("inbound diagnostics overwritten: %+v", got)
	}
	if got.LastConnectionMode != models.ConnectionModeInbound {
		t.Fatalf("last connection mode = %q", got.LastConnectionMode)
	}
	if got.Inbound.Configured != true || got.Inbound.Capable {
		t.Fatalf("inbound view = %+v", got.Inbound)
	}
	if got.Inbound.CertFingerprint != "sha256:abcdef" {
		t.Fatalf("api fingerprint = %q", got.Inbound.CertFingerprint)
	}

	// 专用 endpoint 更新会暂停入站身份，但保留旧身份用于展示/轮换判定。
	if err := st.UpdateNodeEndpoint(ctx, node.ID, "new.example.com:18444", "admin", time.Now()); err != nil {
		t.Fatalf("update endpoint: %v", err)
	}
	got, _ = st.GetNode(ctx, node.ID)
	if got.Endpoint != "new.example.com:18444" || got.InboundProvisioned || !got.InboundBundleStale ||
		got.EndpointUpdatedBy != "admin" || got.EndpointUpdatedAt == "" {
		t.Fatalf("endpoint update result: %+v", got)
	}

	// 严格零行命中语义。
	for name, fn := range map[string]func() error{
		"endpoint": func() error {
			return st.UpdateNodeEndpoint(ctx, "missing", "x:18444", "admin", time.Now())
		},
		"identity": func() error {
			return st.UpdateNodeInboundIdentity(ctx, "missing", true, "", "", time.Time{}, "")
		},
		"diagnostics": func() error {
			return st.UpdateNodeInboundDiagnostics(ctx, "missing", time.Time{}, "")
		},
		"connection_mode": func() error {
			return st.UpdateNodeLastConnectionMode(ctx, "missing", models.ConnectionModeOutbound)
		},
	} {
		if err := fn(); !errors.Is(err, sql.ErrNoRows) {
			t.Fatalf("%s update missing node err = %v, want sql.ErrNoRows", name, err)
		}
	}
}

func TestListInboundCandidateNodes(t *testing.T) {
	st, err := OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	defer st.Close()
	ctx := context.Background()
	for _, node := range []*models.Node{
		{ID: "candidate", AgentID: "agent-candidate", Hostname: "candidate", Endpoint: "a.example.com:18444", Status: models.NodeStatusPending},
		{ID: "no-endpoint", AgentID: "agent-no-endpoint", Hostname: "no-endpoint", Status: models.NodeStatusOnline},
		{ID: "not-provisioned", AgentID: "agent-not-provisioned", Hostname: "not-provisioned", Endpoint: "b.example.com:18444", Status: models.NodeStatusOnline},
		{ID: "disabled", AgentID: "agent-disabled", Hostname: "disabled", Endpoint: "c.example.com:18444", Status: models.NodeStatusDisabled},
	} {
		if err := st.UpsertNode(ctx, node); err != nil {
			t.Fatalf("upsert %s: %v", node.ID, err)
		}
	}
	for _, id := range []string{"candidate", "disabled"} {
		if err := st.UpdateNodeInboundIdentity(ctx, id, true, "fp-"+id, "serial-"+id, time.Now().Add(time.Hour), "enc"); err != nil {
			t.Fatalf("provision %s: %v", id, err)
		}
	}

	candidates, err := st.ListInboundCandidateNodes(ctx)
	if err != nil {
		t.Fatalf("list candidates: %v", err)
	}
	if len(candidates) != 1 || candidates[0].ID != "candidate" {
		t.Fatalf("candidates = %+v, want only candidate", candidates)
	}
}

func TestNodeJSONOmitsInboundToken(t *testing.T) {
	node := &models.Node{
		ID: "node-json", Endpoint: "a.example.com:18444",
		InboundProvisioned: true, InboundTokenEnc: "super-secret-token",
		InboundCertFingerprint: "fp", InboundCertNotAfter: "2027-01-01T00:00:00Z",
	}
	node.SyncInboundView()
	data, err := json.Marshal(node)
	if err != nil {
		t.Fatalf("marshal node: %v", err)
	}
	body := string(data)
	if strings.Contains(body, "super-secret-token") || strings.Contains(body, "inbound_token_enc") {
		t.Fatalf("node JSON leaked inbound token: %s", body)
	}
	for _, want := range []string{`"endpoint":"a.example.com:18444"`, `"inbound_bundle_stale":false`, `"cert_fingerprint":"sha256:fp"`} {
		if !strings.Contains(body, want) {
			t.Fatalf("node JSON missing %s: %s", want, body)
		}
	}
}

func TestMigrationFailureRollsBackWholeStep(t *testing.T) {
	db, err := sql.Open("sqlite", "file:"+filepath.Join(t.TempDir(), "rollback.db")+"?_pragma=foreign_keys(1)")
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	original := migrations
	migrations = append(append([]migration{}, original...),
		migration{version: 998, name: "forced_failure", sql: `
CREATE TABLE migration_partial_should_not_exist (id TEXT PRIMARY KEY);
INSERT INTO table_that_does_not_exist (id) VALUES ('boom');
`})
	defer func() { migrations = original }()
	if err := Migrate(db); err == nil {
		t.Fatal("forced migration failure was not reported")
	}
	var count int
	if err := db.QueryRow(`SELECT count(*) FROM sqlite_master WHERE type='table' AND name='migration_partial_should_not_exist'`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatal("failed migration left partial table behind")
	}
	if err := db.QueryRow(`SELECT count(*) FROM schema_migrations WHERE version = 998`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatal("failed migration was recorded as applied")
	}
}
