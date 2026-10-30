package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestDevNoAuthBypassesAuth 本地测试免登录：/api/auth-mode 公开返回开关，
// 无 token 的 API 请求按管理员放行；默认关闭时仍返回 401。
func TestDevNoAuthBypassesAuth(t *testing.T) {
	c := newContractServer(t)

	// 默认关闭：无 token 仍 401。
	req := httptest.NewRequest(http.MethodGet, "/api/nodes", nil)
	rec := httptest.NewRecorder()
	c.handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("default auth = %d, want 401", rec.Code)
	}

	c.server.SetDevNoAuth(true)
	// auth-mode 公开可访问。
	req = httptest.NewRequest(http.MethodGet, "/api/auth-mode", nil)
	rec = httptest.NewRecorder()
	c.handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("auth-mode = %d %s", rec.Code, rec.Body.String())
	}
	var mode struct {
		DevNoAuth bool `json:"dev_no_auth"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &mode); err != nil || !mode.DevNoAuth {
		t.Fatalf("auth-mode body = %s err=%v", rec.Body.String(), err)
	}

	// 无 token 的 nodes/me 走管理员放行。
	for _, path := range []string{"/api/nodes", "/api/me"} {
		req = httptest.NewRequest(http.MethodGet, path, nil)
		rec = httptest.NewRecorder()
		c.handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("%s without token = %d %s", path, rec.Code, rec.Body.String())
		}
	}
	var me struct {
		Username string `json:"username"`
		Role     string `json:"role"`
	}
	// 重新请求 /me 以解码。
	req = httptest.NewRequest(http.MethodGet, "/api/me", nil)
	rec = httptest.NewRecorder()
	c.handler.ServeHTTP(rec, req)
	if err := json.Unmarshal(rec.Body.Bytes(), &me); err != nil {
		t.Fatalf("decode me: %v", err)
	}
	if me.Username != "dev" || me.Role != models.RoleAdministrator {
		t.Fatalf("dev session = %+v", me)
	}
}
