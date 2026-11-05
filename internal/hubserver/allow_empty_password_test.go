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
)

func newAllowEmptyPasswordHub(t *testing.T, allow bool) *Hub {
	t.Helper()
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	h, err := New(Config{
		WebAddr:            "127.0.0.1:0",
		GatewayAddr:        "127.0.0.1:1",
		DataDir:            dir,
		ArtifactDir:        dir + "/artifacts",
		BaseURL:            "http://127.0.0.1:18080",
		HeartbeatTimeout:   30 * time.Second,
		AdminUsername:      "admin",
		AdminPassword:      "",
		AllowEmptyPassword: allow,
		SessionTTL:         time.Hour,
		ChangelogWindow:    5000,
	}, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatalf("ensure admin: %v", err)
	}
	return h
}

// TestAllowEmptyPasswordConfigPropagation 验证 hubserver 配置默认关闭，
// 开启后 /api/auth-mode 如实上报且空密码管理员可登录。
func TestAllowEmptyPasswordConfigPropagation(t *testing.T) {
	t.Run("default false", func(t *testing.T) {
		h := newAllowEmptyPasswordHub(t, false)

		req := httptest.NewRequest(http.MethodGet, "/api/auth-mode", nil)
		rec := httptest.NewRecorder()
		h.ServeMux().ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("auth-mode = %d %s", rec.Code, rec.Body.String())
		}
		var mode struct {
			AllowEmptyPassword bool `json:"allow_empty_password"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &mode); err != nil {
			t.Fatalf("decode auth-mode: %v", err)
		}
		if mode.AllowEmptyPassword {
			t.Fatal("allow_empty_password must be false by default")
		}

		req = httptest.NewRequest(http.MethodPost, "/api/login",
			strings.NewReader(`{"username":"admin","password":""}`))
		req.Header.Set("Content-Type", "application/json")
		rec = httptest.NewRecorder()
		h.ServeMux().ServeHTTP(rec, req)
		if rec.Code != http.StatusUnauthorized {
			t.Fatalf("empty password login by default = %d %s, want 401", rec.Code, rec.Body.String())
		}
	})

	t.Run("enabled", func(t *testing.T) {
		h := newAllowEmptyPasswordHub(t, true)

		req := httptest.NewRequest(http.MethodGet, "/api/auth-mode", nil)
		rec := httptest.NewRecorder()
		h.ServeMux().ServeHTTP(rec, req)
		var mode struct {
			AllowEmptyPassword bool `json:"allow_empty_password"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &mode); err != nil {
			t.Fatalf("decode auth-mode: %v", err)
		}
		if !mode.AllowEmptyPassword {
			t.Fatal("allow_empty_password must be reported as true when enabled")
		}

		req = httptest.NewRequest(http.MethodPost, "/api/login",
			strings.NewReader(`{"username":"admin","password":""}`))
		req.Header.Set("Content-Type", "application/json")
		rec = httptest.NewRecorder()
		h.ServeMux().ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("empty password login with switch on = %d %s", rec.Code, rec.Body.String())
		}
	})
}
