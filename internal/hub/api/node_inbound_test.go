package api

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type inboundViewTestConn struct {
	nodeID string
	origin string
}

func (c *inboundViewTestConn) NodeID() string               { return c.nodeID }
func (c *inboundViewTestConn) Send(protocol.Envelope) error { return nil }
func (c *inboundViewTestConn) Close() error                 { return nil }
func (c *inboundViewTestConn) Origin() string               { return c.origin }

type nodeInboundResponse struct {
	Endpoint           string                 `json:"endpoint"`
	ConnectionMode     string                 `json:"connection_mode"`
	LastConnectionMode string                 `json:"last_connection_mode"`
	InboundBundleStale bool                   `json:"inbound_bundle_stale"`
	EndpointUpdatedAt  string                 `json:"endpoint_updated_at"`
	EndpointUpdatedBy  string                 `json:"endpoint_updated_by"`
	Inbound            models.NodeInboundInfo `json:"inbound"`
}

func createInboundAPITestNode(t *testing.T, c *contractServer) *models.Node {
	t.Helper()
	node := &models.Node{
		ID: "node-inbound-api", AgentID: "agent-inbound-api", Hostname: "inbound-host",
		Endpoint: "agent.example.com:18444", Status: models.NodeStatusOnline,
		Capabilities: map[string]bool{
			models.CapInboundConnection: true,
			models.CapInboundDataPlane:  true,
		},
	}
	if err := c.store.UpsertNode(t.Context(), node); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	if err := c.store.UpdateNodeInboundIdentity(t.Context(), node.ID, true,
		"0123456789abcdef", "serial-1", time.Now().Add(24*time.Hour), "encrypted-node-token"); err != nil {
		t.Fatalf("set inbound identity: %v", err)
	}
	if err := c.store.UpdateNodeLastConnectionMode(t.Context(), node.ID, models.ConnectionModeInbound); err != nil {
		t.Fatalf("set last connection mode: %v", err)
	}
	return node
}

func TestNodeInboundAPIContract(t *testing.T) {
	c, operator, _ := nodeDisplayNameTestServer(t)
	c.server.SetSessions(c.sessions)
	node := createInboundAPITestNode(t, c)

	rec := nodeDisplayNameDo(t, c, operator, http.MethodGet, "/api/nodes/"+node.ID, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("GET node = %d %s", rec.Code, rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), "encrypted-node-token") || strings.Contains(rec.Body.String(), "inbound_token_enc") {
		t.Fatalf("node API leaked inbound token: %s", rec.Body.String())
	}
	var got nodeInboundResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode node: %v", err)
	}
	if got.Endpoint != "agent.example.com:18444" || got.ConnectionMode != models.ConnectionModeOffline ||
		got.LastConnectionMode != models.ConnectionModeInbound || !got.Inbound.Configured ||
		!got.Inbound.Capable || !got.Inbound.DataPlane {
		t.Fatalf("node inbound response = %+v", got)
	}
	if got.Inbound.CertFingerprint != "sha256:0123456789abcdef" || got.Inbound.CertNotAfter == "" ||
		got.Inbound.LastError != "" {
		t.Fatalf("node inbound diagnostics = %+v", got.Inbound)
	}

	// 活跃会话期间 connection_mode 为实时方向。
	c.sessions.Register(&inboundViewTestConn{nodeID: node.ID, origin: models.ConnectionModeInbound})
	rec = nodeDisplayNameDo(t, c, operator, http.MethodGet, "/api/nodes/"+node.ID, "")
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode connected node: %v", err)
	}
	if got.ConnectionMode != models.ConnectionModeInbound {
		t.Fatalf("connected connection_mode = %q", got.ConnectionMode)
	}
	rec = nodeDisplayNameDo(t, c, operator, http.MethodGet, "/api/nodes", "")
	if !strings.Contains(rec.Body.String(), `"connection_mode":"inbound"`) {
		t.Fatalf("node list missing live connection_mode: %s", rec.Body.String())
	}
	c.sessions.Unregister(node.ID)

	// Operator 不能修改 endpoint；不产生修改。
	rec = nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID, `{"endpoint":"operator.example.com"}`)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("operator endpoint update = %d %s", rec.Code, rec.Body.String())
	}
	persisted, _ := c.store.GetNode(t.Context(), node.ID)
	if persisted.Endpoint != "agent.example.com:18444" {
		t.Fatalf("operator changed endpoint to %q", persisted.Endpoint)
	}

	// connection_mode 只读。
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes/"+node.ID, `{"connection_mode":"outbound"}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "read-only") {
		t.Fatalf("connection_mode write = %d %s", rec.Code, rec.Body.String())
	}

	// 非法 endpoint 不产生部分更新。
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes/"+node.ID,
		`{"status":"maintenance","endpoint":"https://new.example.com"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid endpoint = %d %s", rec.Code, rec.Body.String())
	}
	persisted, _ = c.store.GetNode(t.Context(), node.ID)
	if persisted.Status != models.NodeStatusOnline {
		t.Fatalf("invalid endpoint partially changed status to %q", persisted.Status)
	}

	// Administrator 修改 endpoint：规范化默认端口、暂停入站身份并审计。
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes/"+node.ID, `{"endpoint":"New.Example.COM."}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("admin endpoint update = %d %s", rec.Code, rec.Body.String())
	}
	var updateResp struct {
		OK                 string `json:"ok"`
		InboundBundleStale bool   `json:"inbound_bundle_stale"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &updateResp); err != nil {
		t.Fatalf("decode update response: %v", err)
	}
	if updateResp.OK != "true" || !updateResp.InboundBundleStale {
		t.Fatalf("endpoint update response = %+v", updateResp)
	}
	persisted, _ = c.store.GetNode(t.Context(), node.ID)
	if persisted.Endpoint != "new.example.com:18444" || persisted.InboundProvisioned ||
		persisted.EndpointUpdatedBy != "admin" || persisted.EndpointUpdatedAt == "" {
		t.Fatalf("endpoint persisted = %+v", persisted)
	}

	audits, err := c.store.ListAudit(t.Context(), store.AuditFilter{Action: "endpoint_update", Limit: 10})
	if err != nil {
		t.Fatalf("list audits: %v", err)
	}
	if len(audits) != 1 || !strings.Contains(audits[0].Detail, `"old":"agent.example.com:18444"`) ||
		!strings.Contains(audits[0].Detail, `"new":"new.example.com:18444"`) {
		t.Fatalf("endpoint audits = %+v", audits)
	}
	if strings.Contains(audits[0].Detail, "encrypted-node-token") {
		t.Fatal("audit must not contain inbound token")
	}

	// 批量接口不接受 endpoint。
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes", `{"id":"`+node.ID+`","endpoint":"batch.example.com"}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "batch") {
		t.Fatalf("batch endpoint = %d %s", rec.Code, rec.Body.String())
	}
}

func TestInboundDataPlaneAPI409(t *testing.T) {
	c := newContractServer(t)
	c.server.SetSessions(c.sessions)
	node := &models.Node{
		ID: "node-inbound-409", AgentID: "agent-inbound-409", Hostname: "inbound-409",
		Status: models.NodeStatusOnline,
	}
	if err := c.store.UpsertNode(t.Context(), node); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	c.sessions.Register(&inboundViewTestConn{nodeID: node.ID, origin: models.ConnectionModeInbound})
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	transfers, err := hub.NewFileTransferManager(c.store, t.TempDir(), "http://hub.example", c.sessions, c.server.nodes, logger)
	if err != nil {
		t.Fatal(err)
	}
	c.server.SetFileTransfers(transfers)
	body := `{"source_node_id":"` + node.ID + `","source_path":"/tmp/a","targets":[{"node_id":"other","destination_path":"/tmp/b"}]}`
	rec := nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/transfers", body)
	if rec.Code != http.StatusConflict || !strings.Contains(rec.Body.String(), `"code":"inbound_data_plane_unavailable"`) {
		t.Fatalf("inbound data plane API = %d %s", rec.Code, rec.Body.String())
	}
}

func TestNodeEnrollmentRequiresEndpoint(t *testing.T) {
	s, token := newEnrollmentTestServer(t)
	do := func(body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, "/api/nodes/enrollment", strings.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+token)
		req.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()
		s.withAuth(s.handleNodeEnrollment, "read")(rec, req)
		return rec
	}

	rec := do(`{"node_name":"missing-endpoint","node_ip":"192.0.2.20"}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "endpoint") {
		t.Fatalf("missing endpoint = %d %s", rec.Code, rec.Body.String())
	}
	rec = do(`{"node_name":"invalid-endpoint","node_ip":"192.0.2.21","endpoint":"https://agent.example.com"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid endpoint = %d %s", rec.Code, rec.Body.String())
	}
	rec = do(`{"node_name":"invalid-port","node_ip":"192.0.2.22","endpoint":"agent.example.com:2222"}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "not allowed") {
		t.Fatalf("non-allowed port = %d %s", rec.Code, rec.Body.String())
	}
	rec = do(`{"node_name":"readonly-mode","node_ip":"192.0.2.23","endpoint":"agent.example.com","connection_mode":"inbound"}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "read-only") {
		t.Fatalf("connection_mode on enrollment = %d %s", rec.Code, rec.Body.String())
	}

	nodes, err := s.nodes.ListNodes(t.Context())
	if err != nil {
		t.Fatalf("list nodes: %v", err)
	}
	if len(nodes) != 0 {
		t.Fatalf("invalid enrollment created nodes: %+v", nodes)
	}

	rec = do(`{"node_name":"valid-node","node_ip":"192.0.2.24","endpoint":"Agent.Example.COM.","hub_address":"http://hub.example:8080"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("valid enrollment = %d %s", rec.Code, rec.Body.String())
	}
	var resp struct {
		Endpoint               string `json:"endpoint"`
		InboundBundleAvailable bool   `json:"inbound_bundle_available"`
		NodeID                 string `json:"node_id"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode enrollment: %v", err)
	}
	if resp.Endpoint != "agent.example.com:18444" || resp.InboundBundleAvailable || resp.NodeID == "" {
		t.Fatalf("enrollment response = %+v", resp)
	}
	nodes, err = s.nodes.ListNodes(t.Context())
	if err != nil || len(nodes) != 1 {
		t.Fatalf("nodes after enrollment = %+v err=%v", nodes, err)
	}
	if nodes[0].Endpoint != "agent.example.com:18444" {
		t.Fatalf("enrollment endpoint = %q", nodes[0].Endpoint)
	}
}
