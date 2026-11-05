package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
)

type authModeResponse struct {
	DevNoAuth          bool `json:"dev_no_auth"`
	AllowEmptyPassword bool `json:"allow_empty_password"`
}

func getAuthMode(t *testing.T, h http.Handler) authModeResponse {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, "/api/auth-mode", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("auth-mode = %d %s", rec.Code, rec.Body.String())
	}
	var mode authModeResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &mode); err != nil {
		t.Fatalf("decode auth-mode: %v body=%s", err, rec.Body.String())
	}
	return mode
}

func postLogin(t *testing.T, h http.Handler, username, password string) *httptest.ResponseRecorder {
	t.Helper()
	body, err := json.Marshal(map[string]string{"username": username, "password": password})
	if err != nil {
		t.Fatalf("marshal login: %v", err)
	}
	req := httptest.NewRequest(http.MethodPost, "/api/login", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

// allow_empty_password 默认关闭：/api/auth-mode 如实上报，空密码登录仍 401；
// 开启后空密码可登录，非空密码行为不变。
func TestAllowEmptyPasswordAuthModeAndLogin(t *testing.T) {
	c := newContractServer(t)

	// 默认 false，不泄露其他信息。
	if mode := getAuthMode(t, c.handler); mode.AllowEmptyPassword {
		t.Fatalf("auth-mode allow_empty_password = true by default: %+v", mode)
	}

	// 默认拒绝空密码（即使账号密码 hash 为空）。
	hash, err := auth.HashPassword("")
	if err != nil {
		t.Fatalf("hash empty password: %v", err)
	}
	if err := c.store.CreateUser(t.Context(), &models.User{
		Username: "empty-admin", PasswordHash: hash, Role: models.RoleAdministrator,
	}); err != nil {
		t.Fatalf("create empty-password user: %v", err)
	}
	if rec := postLogin(t, c.handler, "empty-admin", ""); rec.Code != http.StatusUnauthorized {
		t.Fatalf("empty password login by default = %d %s, want 401", rec.Code, rec.Body.String())
	}

	// 开启开关：auth-mode 上报 true，空密码登录成功。
	c.server.auth.SetAllowEmptyPassword(true)
	if mode := getAuthMode(t, c.handler); !mode.AllowEmptyPassword {
		t.Fatalf("auth-mode allow_empty_password = false after enabling: %+v", mode)
	}
	rec := postLogin(t, c.handler, "empty-admin", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("empty password login with switch on = %d %s", rec.Code, rec.Body.String())
	}
	var login struct {
		Token string `json:"token"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &login); err != nil || login.Token == "" {
		t.Fatalf("decode login token: %v body=%s", err, rec.Body.String())
	}
	meReq := httptest.NewRequest(http.MethodGet, "/api/me", nil)
	meReq.Header.Set("Authorization", "Bearer "+login.Token)
	meRec := httptest.NewRecorder()
	c.handler.ServeHTTP(meRec, meReq)
	if meRec.Code != http.StatusOK {
		t.Fatalf("/api/me with empty-password session = %d %s", meRec.Code, meRec.Body.String())
	}

	// 非空密码账号仍正常；错误密码仍拒绝。
	if rec := postLogin(t, c.handler, "admin", "secret"); rec.Code != http.StatusOK {
		t.Fatalf("non-empty password login with switch on = %d %s", rec.Code, rec.Body.String())
	}
	if rec := postLogin(t, c.handler, "empty-admin", "wrong"); rec.Code != http.StatusUnauthorized {
		t.Fatalf("wrong password with switch on = %d %s, want 401", rec.Code, rec.Body.String())
	}
}
