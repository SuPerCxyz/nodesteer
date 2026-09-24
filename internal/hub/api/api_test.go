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

	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// newMeTestServer 构造仅依赖 store/auth 的 API Server（OIDC 与各 Manager 不参与本链路）。
func newMeTestServer(t *testing.T) (*Server, *store.SQLiteStore) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return New(st, auth.New(st, time.Hour), nil, nil, nil, nil, nil, nil, nil, logger), st
}

// loginAs 以本地密码登录并返回 token。
func loginAs(t *testing.T, s *Server, username, password string) string {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/api/login",
		strings.NewReader(`{"username":"`+username+`","password":"`+password+`"}`))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.handleLogin(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("login status = %d, body %s", rec.Code, rec.Body.String())
	}
	var resp struct {
		Token string `json:"token"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode login response: %v", err)
	}
	return resp.Token
}

// doMe 经由 withAuth 真实链路请求 /api/me 并返回响应体。
func doMe(t *testing.T, s *Server, token string) map[string]any {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/api/me", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	rec := httptest.NewRecorder()
	s.withAuth(s.handleMe, "read")(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("/api/me status = %d, body %s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode /api/me response: %v", err)
	}
	return body
}

// /api/me 必须带出用户头像（OIDC picture claim 同步后的持久化值）。
func TestMeReturnsAvatar(t *testing.T) {
	s, st := newMeTestServer(t)
	ctx := t.Context()

	hash, err := auth.HashPassword("secret")
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	if err := st.CreateUser(ctx, &models.User{
		Username: "with-avatar", PasswordHash: hash, Role: models.RoleAdministrator,
		AvatarURL: "https://idp.example/a.png",
	}); err != nil {
		t.Fatalf("create user: %v", err)
	}

	body := doMe(t, s, loginAs(t, s, "with-avatar", "secret"))
	if body["avatar"] != "https://idp.example/a.png" {
		t.Fatalf("/api/me avatar = %v, want https://idp.example/a.png", body["avatar"])
	}
	if body["Username"] != "with-avatar" {
		t.Fatalf("/api/me Username = %v", body["Username"])
	}
}

// 无头像用户的 /api/me：avatar 字段省略，不影响响应可用性。
func TestMeOmitsEmptyAvatar(t *testing.T) {
	s, st := newMeTestServer(t)
	ctx := t.Context()

	hash, err := auth.HashPassword("secret")
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	if err := st.CreateUser(ctx, &models.User{
		Username: "no-avatar", PasswordHash: hash, Role: models.RoleViewer,
	}); err != nil {
		t.Fatalf("create user: %v", err)
	}

	body := doMe(t, s, loginAs(t, s, "no-avatar", "secret"))
	if _, ok := body["avatar"]; ok {
		t.Fatalf("/api/me should omit empty avatar, got %v", body["avatar"])
	}
}

func TestValidNodeAddress(t *testing.T) {
	tests := []struct {
		name    string
		address string
		valid   bool
	}{
		{name: "ipv4", address: "192.0.2.10", valid: true},
		{name: "ipv6", address: "2001:db8::10", valid: true},
		{name: "hostname", address: "agent.example.com", valid: true},
		{name: "single label hostname", address: "node-01", valid: true},
		{name: "trailing dot hostname", address: "agent.example.com.", valid: true},
		{name: "empty", address: "", valid: false},
		{name: "scheme", address: "https://agent.example.com", valid: false},
		{name: "port", address: "agent.example.com:8443", valid: false},
		{name: "invalid label", address: "-agent.example.com", valid: false},
		{name: "underscore", address: "agent_node.example.com", valid: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := validNodeAddress(test.address); got != test.valid {
				t.Fatalf("validNodeAddress(%q) = %v, want %v", test.address, got, test.valid)
			}
		})
	}
}

// TestConfiguredGatewayAddr 锁定纳管地址派生的端口契约：
// 配置 URL 显式端口优先；隐式端口按 scheme 补全（https→:443、http→:80），
// 不得对隐式 443 的反代地址硬编码回退 :8443；解析失败或无 scheme 时兜底 :8443。
func TestConfiguredGatewayAddr(t *testing.T) {
	tests := []struct {
		name string
		base string
		want string
	}{
		{name: "https 隐式端口按 scheme 补 443", base: "https://x.y", want: ":443"},
		{name: "https 显式端口优先", base: "https://x.y:8443", want: ":8443"},
		{name: "http 隐式端口按 scheme 补 80", base: "http://x.y", want: ":80"},
		{name: "http 显式端口优先", base: "http://x.y:8080", want: ":8080"},
		{name: "空值兜底 8443", base: "", want: ":8443"},
		{name: "坏值兜底 8443", base: "://bad url", want: ":8443"},
		{name: "无 scheme 兜底 8443", base: "nodesteer.example.com", want: ":8443"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := configuredGatewayAddr(test.base); got != test.want {
				t.Fatalf("configuredGatewayAddr(%q) = %q, want %q", test.base, got, test.want)
			}
		})
	}
}
