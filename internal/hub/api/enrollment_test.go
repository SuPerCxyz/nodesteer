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
	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// newEnrollmentTestServer 构造带 NodeManager 与纳管配置的 API Server。
func newEnrollmentTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	nm := hub.NewNodeManager(st, hub.NewRevisionManager(st))
	s := New(st, auth.New(st, time.Hour), nm, nil, nil, nil, nil, nil, nil, logger)
	s.SetEnrollmentConfig("reg-token", "https://hub.example:8443")

	hash, err := auth.HashPassword("secret")
	if err != nil {
		t.Fatal(err)
	}
	if err := st.CreateUser(t.Context(), &models.User{
		Username: "enroll-admin", PasswordHash: hash, Role: models.RoleAdministrator,
	}); err != nil {
		t.Fatalf("create admin: %v", err)
	}
	return s, loginAs(t, s, "enroll-admin", "secret")
}

// TestEnrollmentTemplateOmitsGeneratedKeys T3：纳管生成的 agent.yaml 不再硬编码
// deployment_mode/host_integration/agent_version/data_dir 四键；docker run 保留
// NODESTEER_DEPLOYMENT_MODE=docker（显式 env 优先级最高）。
func TestEnrollmentTemplateOmitsGeneratedKeys(t *testing.T) {
	s, token := newEnrollmentTestServer(t)
	body := `{"node_name":"enroll-node","node_ip":"192.0.2.10","hub_address":"http://hub.example:8080"}`
	req := httptest.NewRequest(http.MethodPost, "/api/nodes/enrollment", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.withAuth(s.handleNodeEnrollment, "read")(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("enrollment status = %d, body %s", rec.Code, rec.Body.String())
	}
	var resp struct {
		Native        string `json:"native"`
		DockerRun     string `json:"docker_run"`
		DockerCompose string `json:"docker_compose"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	for _, key := range []string{"deployment_mode:", "host_integration:", "agent_version:", "data_dir"} {
		if strings.Contains(resp.Native, key) {
			t.Fatalf("generated config must not hardcode %q, got:\n%s", key, resp.Native)
		}
	}
	// 必要连接参数仍在
	for _, want := range []string{"hub_url:", "registration_token:", "node_name:", "node_ip:"} {
		if !strings.Contains(resp.Native, want) {
			t.Fatalf("generated config missing %q, got:\n%s", want, resp.Native)
		}
	}
	if !strings.Contains(resp.DockerRun, "NODESTEER_DEPLOYMENT_MODE=docker") {
		t.Fatalf("docker run must keep explicit NODESTEER_DEPLOYMENT_MODE=docker, got:\n%s", resp.DockerRun)
	}
	if !strings.Contains(resp.DockerCompose, "NODESTEER_DEPLOYMENT_MODE: docker") {
		t.Fatalf("docker compose must keep explicit NODESTEER_DEPLOYMENT_MODE, got:\n%s", resp.DockerCompose)
	}
}

// notifyTestConn 实现 hub.AgentConn，捕获下发消息。
type notifyTestConn struct {
	nodeID string
	sent   chan protocol.Envelope
}

func (c *notifyTestConn) NodeID() string { return c.nodeID }
func (c *notifyTestConn) Send(msg protocol.Envelope) error {
	c.sent <- msg
	return nil
}
func (c *notifyTestConn) Close() error { return nil }

// TestNotifySettingsReachesPausedNode 不拦项：settings 下发不按节点状态过滤，
// 暂停（maintenance/disabled）节点照常收到。
func TestNotifySettingsReachesPausedNode(t *testing.T) {
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	s := New(st, auth.New(st, time.Hour), nil, nil, nil, nil, nil, nil, nil, logger)

	sessions := hub.NewSessionManager()
	s.SetSessions(sessions)
	for _, status := range []string{models.NodeStatusMaintenance, models.NodeStatusDisabled} {
		nodeID := "n-settings-" + status
		if err := st.UpsertNode(t.Context(), &models.Node{ID: nodeID, AgentID: "agent-" + nodeID, Hostname: nodeID, Status: status}); err != nil {
			t.Fatalf("upsert node: %v", err)
		}
		conn := &notifyTestConn{nodeID: nodeID, sent: make(chan protocol.Envelope, 1)}
		sessions.Register(conn)

		s.notifySettings()
		select {
		case msg := <-conn.sent:
			if msg.Type != protocol.MsgSettings {
				t.Fatalf("expected settings message, got %s", msg.Type)
			}
		default:
			t.Fatalf("settings dispatch must reach paused node (status=%s)", status)
		}
	}
}
