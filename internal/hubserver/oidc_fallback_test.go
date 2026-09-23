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

	"github.com/cadentra/cadentra/internal/hub/auth"
)

// boolPtr 返回指向 v 的指针（allow_local_login 三态开关测试辅助）。
func boolPtr(v bool) *bool { return &v }

// fallbackHubConfig 生成最小可用 Hub 配置；allowLocal 为 nil 表示不显式配置（默认 true）。
func fallbackHubConfig(dir, issuer string, allowLocal *bool) Config {
	return Config{
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
			Issuer:          issuer,
			ClientID:        "test-client",
			AllowLocalLogin: allowLocal,
		},
	}
}

// TestDefaultLocalModeSkipsDiscovery 未显式配置（默认 true）本地模式：
// 即使配置了不可达的 issuer，启动也绝不做 discovery（永不因 OIDC 失败）、
// state={enabled:false, local_fallback:true}、/api/oidc/login 404、本地登录 200。
func TestDefaultLocalModeSkipsDiscovery(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	// 127.0.0.1:1 连接必失败；本地模式下 Hub 不应发起 discovery，故正常启动
	h, err := New(fallbackHubConfig(dir, "http://127.0.0.1:1", nil), logger)
	if err != nil {
		t.Fatalf("local mode must start regardless of issuer reachability, got: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatalf("ensure admin: %v", err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/api/oidc/state")
	if err != nil {
		t.Fatal(err)
	}
	var state struct {
		Enabled       bool `json:"enabled"`
		LocalFallback bool `json:"local_fallback"`
	}
	json.NewDecoder(resp.Body).Decode(&state)
	resp.Body.Close()
	if state.Enabled || !state.LocalFallback {
		t.Fatalf("local mode should report enabled=false local_fallback=true, got enabled=%v fallback=%v",
			state.Enabled, state.LocalFallback)
	}

	// OIDC 完全失效：登录入口 404
	oidcLogin, err := http.Get(ts.URL + "/api/oidc/login")
	if err != nil {
		t.Fatal(err)
	}
	oidcLogin.Body.Close()
	if oidcLogin.StatusCode != http.StatusNotFound {
		t.Fatalf("oidc/login expected 404 in local mode, got %d", oidcLogin.StatusCode)
	}

	// 本地密码登录必须可用
	loginResp, err := http.Post(ts.URL+"/api/login", "application/json",
		strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer loginResp.Body.Close()
	if loginResp.StatusCode != http.StatusOK {
		t.Fatalf("local login should work in default local mode, got %d", loginResp.StatusCode)
	}
}

// TestSSOOnlyModeFailsFastOnBadIssuer allow_local_login=false 时 discovery 失败必须 fail-fast 拒启。
func TestSSOOnlyModeFailsFastOnBadIssuer(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	_, err := New(fallbackHubConfig(dir, "http://127.0.0.1:1", boolPtr(false)), logger)
	if err == nil {
		t.Fatal("hub should refuse to start when sso-only mode oidc discovery fails")
	}
	if !strings.Contains(err.Error(), "init oidc") {
		t.Fatalf("expected init oidc error, got: %v", err)
	}
}

// TestSSOOnlyModeRequiresIssuer allow_local_login=false 且未配置 issuer 时必须拒启。
func TestSSOOnlyModeRequiresIssuer(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	_, err := New(fallbackHubConfig(dir, "", boolPtr(false)), logger)
	if err == nil {
		t.Fatal("hub should refuse to start when allow_local_login=false without oidc.issuer")
	}
	if !strings.Contains(err.Error(), "allow_local_login=false requires oidc.issuer") {
		t.Fatalf("expected missing issuer error, got: %v", err)
	}
}
