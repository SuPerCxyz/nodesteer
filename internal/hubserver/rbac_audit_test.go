package hubserver

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/google/uuid"
)

// fakeAgentConn 满足 hub.AgentConn，用于让 ExecutionManager.Cancel 找到在线节点。
type fakeAgentConn struct{ nodeID string }

func (f *fakeAgentConn) NodeID() string               { return f.nodeID }
func (f *fakeAgentConn) Send(protocol.Envelope) error { return nil }
func (f *fakeAgentConn) Close() error                 { return nil }

func newRBACTestServer(t *testing.T) (*Hub, string) {
	t.Helper()
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	h, err := New(Config{
		WebAddr:           "127.0.0.1:0",
		GatewayAddr:       "127.0.0.1:0",
		RegistrationToken: "test-token",
		DataDir:           dir,
		ArtifactDir:       dir + "/artifacts",
		BaseURL:           "http://127.0.0.1:18080",
		HeartbeatTimeout:  30 * time.Second,
		AdminUsername:     "admin",
		AdminPassword:     "admin123",
		SessionTTL:        time.Hour,
		RevisionCheckSec:  5,
		ChangelogWindow:   5000,
	}, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatalf("ensure admin: %v", err)
	}
	ts := httptest.NewServer(h.ServeMux())
	t.Cleanup(ts.Close)
	return h, ts.URL
}

func apiLogin(t *testing.T, base, username, password string) string {
	t.Helper()
	resp, err := http.Post(base+"/api/login", "application/json",
		strings.NewReader(`{"username":"`+username+`","password":"`+password+`"}`))
	if err != nil {
		t.Fatalf("login %s: %v", username, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("login %s: status %d body %s", username, resp.StatusCode, string(body))
	}
	var body struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode login: %v", err)
	}
	return body.Token
}

func apiDo(t *testing.T, base, method, path, token, body string) (int, string) {
	t.Helper()
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, base+path, reader)
	if err != nil {
		t.Fatalf("request %s %s: %v", method, path, err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do %s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	return resp.StatusCode, string(b)
}

func listAudit(t *testing.T, base, token, query string) []*models.AuditLog {
	t.Helper()
	status, body := apiDo(t, base, http.MethodGet, "/api/audit?"+query, token, "")
	if status != http.StatusOK {
		t.Fatalf("list audit: status %d body %s", status, body)
	}
	var out []*models.AuditLog
	if err := json.Unmarshal([]byte(body), &out); err != nil {
		t.Fatalf("decode audit: %v (body %s)", err, body)
	}
	return out
}

// FAIL-B-101：角色降权必须对已登录会话立即生效（无需重新登录）。
func TestRoleChangeAppliesToExistingSession(t *testing.T) {
	_, base := newRBACTestServer(t)
	adminToken := apiLogin(t, base, "admin", "admin123")

	// 创建 operator 用户
	status, body := apiDo(t, base, http.MethodPost, "/api/users", adminToken,
		`{"username":"ops1","password":"ops123","role":"operator"}`)
	if status != http.StatusOK {
		t.Fatalf("create user: status %d body %s", status, body)
	}
	var created models.User
	if err := json.Unmarshal([]byte(body), &created); err != nil {
		t.Fatalf("decode user: %v", err)
	}
	opsToken := apiLogin(t, base, "ops1", "ops123")

	// operator 会话通过 canRun：无对应执行记录时为 400（而非 403）
	status, _ = apiDo(t, base, http.MethodPost, "/api/executions/"+uuid.NewString()+"/cancel", opsToken, "")
	if status != http.StatusBadRequest {
		t.Fatalf("operator cancel probe: status %d, want 400 (permission granted)", status)
	}

	// 管理员降权为 viewer
	status, body = apiDo(t, base, http.MethodPut, "/api/users/"+created.ID, adminToken, `{"role":"viewer"}`)
	if status != http.StatusOK {
		t.Fatalf("demote user: status %d body %s", status, body)
	}

	// 旧会话立即失去 operator 权限
	status, body = apiDo(t, base, http.MethodPost, "/api/executions/"+uuid.NewString()+"/cancel", opsToken, "")
	if status != http.StatusForbidden {
		t.Fatalf("demoted old session: status %d body %s, want 403", status, body)
	}

	// /api/me 返回数据库当前角色
	status, body = apiDo(t, base, http.MethodGet, "/api/me", opsToken, "")
	if status != http.StatusOK {
		t.Fatalf("me: status %d body %s", status, body)
	}
	var me struct {
		Role string `json:"role"`
	}
	if err := json.Unmarshal([]byte(body), &me); err != nil {
		t.Fatalf("decode me: %v", err)
	}
	if me.Role != models.RoleViewer {
		t.Fatalf("me role = %q, want viewer", me.Role)
	}

	// 重新登录同样为 viewer（新会话一致）
	freshToken := apiLogin(t, base, "ops1", "ops123")
	status, _ = apiDo(t, base, http.MethodPost, "/api/executions/"+uuid.NewString()+"/cancel", freshToken, "")
	if status != http.StatusForbidden {
		t.Fatalf("fresh session after demotion: status %d, want 403", status)
	}
}

// FAIL-A-004：取消执行成功后写入审计。
func TestExecutionCancelWritesAudit(t *testing.T) {
	h, base := newRBACTestServer(t)
	adminToken := apiLogin(t, base, "admin", "admin123")
	ctx := context.Background()

	nodeID := "node-cancel-audit"
	h.Sessions().Register(&fakeAgentConn{nodeID: nodeID})
	execID := uuid.NewString()
	if err := h.Store().CreateExecution(ctx, &models.Execution{
		ID:          execID,
		TaskID:      "task-cancel-audit",
		NodeID:      nodeID,
		TriggerType: models.TriggerManual,
		Status:      models.ExecStatusPending,
	}); err != nil {
		t.Fatalf("create execution: %v", err)
	}

	status, body := apiDo(t, base, http.MethodPost, "/api/executions/"+execID+"/cancel", adminToken, "")
	if status != http.StatusOK {
		t.Fatalf("cancel execution: status %d body %s", status, body)
	}

	logs := listAudit(t, base, adminToken, "action=cancel")
	found := false
	for _, a := range logs {
		if a.Resource == "execution" && a.ResourceID == execID {
			found = true
			if a.Username != "admin" {
				t.Fatalf("audit username = %q, want admin", a.Username)
			}
		}
	}
	if !found {
		t.Fatalf("no execution cancel audit found: %+v", logs)
	}
}

// FAIL-B-105：保存运行时设置写入审计，detail 包含被修改的键。
func TestSettingsUpdateWritesAudit(t *testing.T) {
	_, base := newRBACTestServer(t)
	adminToken := apiLogin(t, base, "admin", "admin123")

	status, body := apiDo(t, base, http.MethodPut, "/api/settings", adminToken, `{"changelog_window":"900"}`)
	if status != http.StatusOK {
		t.Fatalf("update settings: status %d body %s", status, body)
	}

	logs := listAudit(t, base, adminToken, "action=update")
	found := false
	for _, a := range logs {
		if a.Resource == "setting" {
			found = true
			if !strings.Contains(a.Detail, "changelog_window") {
				t.Fatalf("setting audit detail = %q, want key changelog_window", a.Detail)
			}
			if a.Username != "admin" {
				t.Fatalf("audit username = %q, want admin", a.Username)
			}
		}
	}
	if !found {
		t.Fatalf("no settings update audit found: %+v", logs)
	}

	countSettingAudits := func() int {
		n := 0
		for _, a := range listAudit(t, base, adminToken, "action=update") {
			if a.Resource == "setting" {
				n++
			}
		}
		return n
	}
	if countSettingAudits() != 1 {
		t.Fatalf("expected exactly one setting audit entry")
	}

	// 校验失败的请求不应写入审计
	status, _ = apiDo(t, base, http.MethodPut, "/api/settings", adminToken, `{"changelog_window":"0"}`)
	if status != http.StatusBadRequest {
		t.Fatalf("invalid settings: status %d, want 400", status)
	}
	if got := countSettingAudits(); got != 1 {
		t.Fatalf("invalid settings should not be audited, setting audits = %d", got)
	}
}
