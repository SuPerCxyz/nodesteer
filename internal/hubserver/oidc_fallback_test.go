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

// fallbackHubConfig 生成最小可用 Hub 配置，issuer 由调用方指定。
func fallbackHubConfig(dir, issuer string, allowLocal bool) Config {
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

// TestOIDCInitFailureFailFast 未开启兜底时 discovery 失败必须拒绝启动。
func TestOIDCInitFailureFailFast(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	// 127.0.0.1:1 连接必失败，模拟 IdP 不可达
	_, err := New(fallbackHubConfig(dir, "http://127.0.0.1:1", false), logger)
	if err == nil {
		t.Fatal("hub should refuse to start when oidc init fails without fallback")
	}
	if !strings.Contains(err.Error(), "init oidc") {
		t.Fatalf("expected init oidc error, got: %v", err)
	}
}

// TestOIDCInitFailureDegradesWithFallback 开启兜底时 discovery 失败降级启动：
// OIDC 未启用、state=false、本地登录可用。
func TestOIDCInitFailureDegradesWithFallback(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(fallbackHubConfig(dir, "http://127.0.0.1:1", true), logger)
	if err != nil {
		t.Fatalf("hub should degrade-start with local login fallback, got: %v", err)
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
	if state.Enabled || state.LocalFallback {
		t.Fatalf("degraded hub should report disabled oidc, got enabled=%v fallback=%v",
			state.Enabled, state.LocalFallback)
	}

	// 本地登录必须可用
	loginResp, err := http.Post(ts.URL+"/api/login", "application/json",
		strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer loginResp.Body.Close()
	if loginResp.StatusCode != http.StatusOK {
		t.Fatalf("local login should work after degraded start, got %d", loginResp.StatusCode)
	}
}

// TestOIDCEnabledWithLocalFallback discovery 成功且开启兜底时：
// state 同时报告 enabled 与 local_fallback，本地登录不被 403。
func TestOIDCEnabledWithLocalFallback(t *testing.T) {
	idp := newMockDiscovery(t)
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(fallbackHubConfig(dir, idp.URL, true), logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
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
	if !state.Enabled {
		t.Fatal("state.enabled should be true when discovery succeeded")
	}
	if !state.LocalFallback {
		t.Fatal("state.local_fallback should be true when allow_local_login set")
	}

	// 本地登录可用（break-glass）
	loginResp, err := http.Post(ts.URL+"/api/login", "application/json",
		strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer loginResp.Body.Close()
	if loginResp.StatusCode != http.StatusOK {
		t.Fatalf("local login should work with fallback enabled, got %d", loginResp.StatusCode)
	}
}
