package hubserver

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// newMockDiscovery 只提供 discovery 文档的 mock IdP（NewOIDC 构造时会请求）
func newMockDiscovery(t *testing.T) *httptest.Server {
	t.Helper()
	var srv *httptest.Server
	srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/.well-known/openid-configuration" {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]any{
				"issuer":                  srv.URL,
				"authorization_endpoint":  srv.URL + "/authorize",
				"token_endpoint":          srv.URL + "/token",
				"jwks_uri":                srv.URL + "/jwks",
				"subject_types_supported": []string{"public"},
			})
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(srv.Close)
	return srv
}

// TestOIDCStateAndLocalLogin 未配置 OIDC（默认本地模式）时：
// state={enabled:false, local_fallback:true}，本地登录正常，oidc/login 404
func TestOIDCStateAndLocalLogin(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
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
	defer ts.Close()
	base := ts.URL

	// 默认本地模式：state = {enabled:false, local_fallback:true}
	resp, err := http.Get(base + "/api/oidc/state")
	if err != nil {
		t.Fatal(err)
	}
	var state struct {
		Enabled       bool `json:"enabled"`
		LocalFallback bool `json:"local_fallback"`
	}
	json.NewDecoder(resp.Body).Decode(&state)
	resp.Body.Close()
	if state.Enabled {
		t.Fatal("oidc state should be false by default")
	}
	if !state.LocalFallback {
		t.Fatal("local_fallback should be true by default (local mode)")
	}

	// 本地登录仍可用
	loginResp, err := http.Post(base+"/api/login", "application/json",
		strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer loginResp.Body.Close()
	if loginResp.StatusCode != 200 {
		t.Fatalf("local login expected 200, got %d", loginResp.StatusCode)
	}

	// oidc/login 本地模式下应 404（OIDC 完全失效）
	oidcLogin, err := http.Get(base + "/api/oidc/login")
	if err != nil {
		t.Fatal(err)
	}
	oidcLogin.Body.Close()
	if oidcLogin.StatusCode != 404 {
		t.Fatalf("oidc/login expected 404 in local mode, got %d", oidcLogin.StatusCode)
	}
}

// TestSSOOnlyModeLocalLoginForbidden 显式 allow_local_login=false（SSO 唯一登录）：
// state={enabled:true, local_fallback:false}，本地登录一律 403，oidc/login 302 跳转。
func TestSSOOnlyModeLocalLoginForbidden(t *testing.T) {
	idp := newMockDiscovery(t)
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
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
		OIDC: auth.OIDCConfig{
			Issuer:          idp.URL,
			ClientID:        "test-client",
			AllowLocalLogin: boolPtr(false),
		},
	}, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatalf("ensure admin: %v", err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()
	base := ts.URL

	// OIDC state = {enabled:true, local_fallback:false}
	resp, err := http.Get(base + "/api/oidc/state")
	if err != nil {
		t.Fatal(err)
	}
	var state struct {
		Enabled       bool `json:"enabled"`
		LocalFallback bool `json:"local_fallback"`
	}
	json.NewDecoder(resp.Body).Decode(&state)
	resp.Body.Close()
	if !state.Enabled {
		t.Fatal("oidc state should be true in sso-only mode")
	}
	if state.LocalFallback {
		t.Fatal("local_fallback should be false in sso-only mode")
	}

	// 本地登录一律 403（绝对语义：账户/密码是否存在均不影响）
	loginResp, err := http.Post(base+"/api/login", "application/json",
		strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer loginResp.Body.Close()
	if loginResp.StatusCode != 403 {
		t.Fatalf("local login expected 403 in sso-only mode, got %d", loginResp.StatusCode)
	}

	// oidc/login 应 302 跳转（discovery 提供 /authorize）
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	oidcLogin, err := client.Get(base + "/api/oidc/login")
	if err != nil {
		t.Fatal(err)
	}
	oidcLogin.Body.Close()
	if oidcLogin.StatusCode != http.StatusFound {
		t.Fatalf("oidc/login expected 302, got %d", oidcLogin.StatusCode)
	}
	if loc := oidcLogin.Header.Get("Location"); !strings.HasPrefix(loc, idp.URL+"/authorize") {
		t.Fatalf("redirect location = %q, want %q prefix", loc, idp.URL+"/authorize")
	}
}

// loginAudits 拉取 action=login 的审计记录。
func loginAudits(t *testing.T, h *Hub) []*models.AuditLog {
	t.Helper()
	logs, err := h.Store().ListAudit(context.Background(), store.AuditFilter{Action: "login", Limit: 200})
	if err != nil {
		t.Fatalf("list audit: %v", err)
	}
	return logs
}

// TestLoginAuditRecords 登录审计：成功/失败可区分，失败与 403 均留痕，detail 不含密码。
func TestLoginAuditRecords(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(fallbackHubConfig(dir, "", nil), logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatalf("ensure admin: %v", err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()

	// 失败登录（错误密码）→ 401 + 审计
	resp, err := http.Post(ts.URL+"/api/login", "application/json",
		strings.NewReader(`{"username":"ghost","password":"wrong-pass-1"}`))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("bad login expected 401, got %d", resp.StatusCode)
	}

	// 成功登录 → 审计（detail=local）
	okResp, err := http.Post(ts.URL+"/api/login", "application/json",
		strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	okResp.Body.Close()
	if okResp.StatusCode != http.StatusOK {
		t.Fatalf("admin login expected 200, got %d", okResp.StatusCode)
	}

	var sawFail, sawSuccess bool
	for _, a := range loginAudits(t, h) {
		if a.Username == "ghost" && strings.Contains(a.Detail, "failed") {
			sawFail = true
			if strings.Contains(a.Detail, "wrong-pass-1") || strings.Contains(a.Detail, "password") {
				t.Fatalf("failure audit detail must not contain password: %q", a.Detail)
			}
		}
		if a.Username == "admin" && a.UserID != "" {
			sawSuccess = true
			if strings.Contains(a.Detail, "failed") {
				t.Fatalf("success audit must be distinguishable from failure: %q", a.Detail)
			}
		}
	}
	if !sawFail {
		t.Fatal("failed login must write an audit record")
	}
	if !sawSuccess {
		t.Fatal("successful login must write an audit record")
	}
}

// TestSSOOnlyModeLoginForbiddenWritesAudit SSO-only 模式下的 403 也必须写审计。
func TestSSOOnlyModeLoginForbiddenWritesAudit(t *testing.T) {
	idp := newMockDiscovery(t)
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(fallbackHubConfig(dir, idp.URL, boolPtr(false)), logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/api/login", "application/json",
		strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("local login expected 403 in sso-only mode, got %d", resp.StatusCode)
	}

	found := false
	for _, a := range loginAudits(t, h) {
		if a.Username == "admin" && strings.Contains(a.Detail, "local login disabled") {
			found = true
		}
	}
	if !found {
		t.Fatal("403 local login attempt must write an audit record")
	}
}

// TestLoginFailureThrottle 连续失败达到阈值后，后续失败尝试被延迟（成功后重置）。
func TestLoginFailureThrottle(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(fallbackHubConfig(dir, "", nil), logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatalf("ensure admin: %v", err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()

	attempt := func() {
		resp, err := http.Post(ts.URL+"/api/login", "application/json",
			strings.NewReader(`{"username":"bruter","password":"nope"}`))
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
	}
	// 前 threshold 次无延迟
	for i := 0; i < 3; i++ {
		attempt()
	}
	// 第 4 次起应被延迟（base 200ms；下限断言避免计时抖动误报）
	start := time.Now()
	attempt()
	if elapsed := time.Since(start); elapsed < 100*time.Millisecond {
		t.Fatalf("expected throttle delay after consecutive failures, got %v", elapsed)
	}
}
