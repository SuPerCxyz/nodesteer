package hubserver

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

// fullMockIdP 内存 IdP：discovery + JWKS + token 端点（签发 id_token），用于 SSO 全流程。
type fullMockIdP struct {
	server   *httptest.Server
	key      *rsa.PrivateKey
	issuer   string
	username string
	groups   []string
	nonce    string
}

func newFullMockIdP(t *testing.T) *fullMockIdP {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	idp := &fullMockIdP{key: key}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/.well-known/openid-configuration":
			writeJSONTest(w, map[string]any{
				"issuer":                                idp.issuer,
				"authorization_endpoint":                idp.issuer + "/authorize",
				"token_endpoint":                        idp.issuer + "/token",
				"jwks_uri":                              idp.issuer + "/jwks",
				"response_types_supported":              []string{"code"},
				"subject_types_supported":               []string{"public"},
				"id_token_signing_alg_values_supported": []string{"RS256"},
			})
		case "/jwks":
			pub := key.Public().(*rsa.PublicKey)
			writeJSONTest(w, map[string]any{
				"keys": []any{
					map[string]any{
						"kty": "RSA",
						"kid": "test-key",
						"use": "sig",
						"alg": "RS256",
						"n":   base64.RawURLEncoding.EncodeToString(pub.N.Bytes()),
						"e":   base64.RawURLEncoding.EncodeToString([]byte{1, 0, 1}),
					},
				},
			})
		case "/token":
			if r.PostFormValue("code_verifier") == "" {
				http.Error(w, "missing code_verifier", http.StatusBadRequest)
				return
			}
			idToken, err := idp.signIDToken()
			if err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			writeJSONTest(w, map[string]any{
				"access_token": "mock-access-token",
				"token_type":   "Bearer",
				"expires_in":   3600,
				"id_token":     idToken,
			})
		default:
			http.NotFound(w, r)
		}
	}))
	idp.server = srv
	idp.issuer = srv.URL
	t.Cleanup(srv.Close)
	return idp
}

func (m *fullMockIdP) signIDToken() (string, error) {
	signer, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.RS256, Key: m.key}, (&jose.SignerOptions{}).
		WithType("JWT").WithHeader("kid", "test-key"))
	if err != nil {
		return "", err
	}
	now := time.Now()
	claims := map[string]any{
		"iss":                m.issuer,
		"sub":                "sub-parity",
		"aud":                "test-client",
		"nonce":              m.nonce,
		"iat":                now.Unix(),
		"exp":                now.Add(time.Hour).Unix(),
		"preferred_username": m.username,
		"groups":             m.groups,
	}
	return jwt.Signed(signer).Claims(claims).Serialize()
}

func writeJSONTest(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

var tokenFromCallback = regexp.MustCompile(`localStorage\.setItem\('cadentra_token', ("[^"]+")\)`)

// ssoLogin 走完整 SSO 流程：/api/oidc/login 取 state+nonce → callback 换会话 token。
func ssoLogin(t *testing.T, base string, idp *fullMockIdP, username string, groups []string) string {
	t.Helper()
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	resp, err := client.Get(base + "/api/oidc/login")
	if err != nil {
		t.Fatalf("oidc login: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusFound {
		t.Fatalf("oidc/login expected 302, got %d", resp.StatusCode)
	}
	u, err := url.Parse(resp.Header.Get("Location"))
	if err != nil {
		t.Fatalf("parse auth url: %v", err)
	}
	state := u.Query().Get("state")
	nonce := u.Query().Get("nonce")
	if state == "" || nonce == "" {
		t.Fatalf("auth url missing state/nonce: %s", u.String())
	}
	idp.username = username
	idp.groups = groups
	idp.nonce = nonce

	cb, err := http.Get(base + "/api/oidc/callback?state=" + url.QueryEscape(state) + "&code=mock-code")
	if err != nil {
		t.Fatalf("callback: %v", err)
	}
	body, _ := io.ReadAll(cb.Body)
	cb.Body.Close()
	if cb.StatusCode != http.StatusOK {
		t.Fatalf("callback expected 200, got %d body %s", cb.StatusCode, string(body))
	}
	m := tokenFromCallback.FindSubmatch(body)
	if m == nil {
		t.Fatalf("callback body missing token: %s", string(body))
	}
	var token string
	if err := json.Unmarshal(m[1], &token); err != nil {
		t.Fatalf("parse token: %v", err)
	}
	return token
}

// meRole 读取 /api/me 的角色。
func meRole(t *testing.T, base, token string) string {
	t.Helper()
	status, body := apiDo(t, base, http.MethodGet, "/api/me", token, "")
	if status != http.StatusOK {
		t.Fatalf("/api/me: status %d body %s", status, body)
	}
	var me struct {
		Role string `json:"role"`
	}
	if err := json.Unmarshal([]byte(body), &me); err != nil {
		t.Fatalf("decode me: %v", err)
	}
	return me.Role
}

// TestLocalAndSSOLoginPermissionParity 验收硬性要求：
// 同角色用户经本地登录（默认本地模式）与 SSO 登录（SSO-only 模式 + mock IdP）
// 后 /api/me 角色一致，受保护端点的允许/拒绝行为逐点一致。
func TestLocalAndSSOLoginPermissionParity(t *testing.T) {
	// ---- Hub A：默认本地模式，operator 用户本地密码登录 ----
	dirA := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	hA, err := New(fallbackHubConfig(dirA, "", nil), logger)
	if err != nil {
		t.Fatalf("new hub A: %v", err)
	}
	t.Cleanup(func() { hA.Close() })
	if err := hA.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatalf("ensure admin A: %v", err)
	}
	tsA := httptest.NewServer(hA.ServeMux())
	t.Cleanup(tsA.Close)
	baseA := tsA.URL

	adminToken := apiLogin(t, baseA, "admin", "admin123")
	status, body := apiDo(t, baseA, http.MethodPost, "/api/users", adminToken,
		`{"username":"ops1","password":"ops123","role":"operator"}`)
	if status != http.StatusOK {
		t.Fatalf("create operator: status %d body %s", status, body)
	}
	localToken := apiLogin(t, baseA, "ops1", "ops123")

	// ---- Hub B：SSO-only 模式（allow_local_login=false）+ mock IdP ----
	idp := newFullMockIdP(t)
	dirB := t.TempDir()
	cfgB := fallbackHubConfig(dirB, idp.issuer, boolPtr(false))
	cfgB.OIDC.RoleMappings = map[string]string{"ops": "operator"}
	hB, err := New(cfgB, logger)
	if err != nil {
		t.Fatalf("new hub B: %v", err)
	}
	t.Cleanup(func() { hB.Close() })
	tsB := httptest.NewServer(hB.ServeMux())
	t.Cleanup(tsB.Close)
	baseB := tsB.URL

	// SSO 用户 ops1 经 role_mappings 映射为 operator（与 Hub A 本地用户同角色）
	ssoToken := ssoLogin(t, baseB, idp, "ops1", []string{"ops"})

	// 1) /api/me 角色一致
	localRole := meRole(t, baseA, localToken)
	ssoRole := meRole(t, baseB, ssoToken)
	if localRole != models.RoleOperator || ssoRole != models.RoleOperator {
		t.Fatalf("roles must both be operator: local=%q sso=%q", localRole, ssoRole)
	}
	if localRole != ssoRole {
		t.Fatalf("role mismatch: local=%q sso=%q", localRole, ssoRole)
	}

	// 2) 受保护端点允许/拒绝行为逐点一致
	probes := []struct {
		name   string
		method string
		path   string
		body   string
	}{
		{"read scripts allowed", http.MethodGet, "/api/scripts", ""},
		{"list users admin-only denied", http.MethodGet, "/api/users", ""},
		{"create user admin-only denied", http.MethodPost, "/api/users", `{"username":"x","password":"y","role":"viewer"}`},
	}
	for _, p := range probes {
		localStatus, localBody := apiDo(t, baseA, p.method, p.path, localToken, p.body)
		ssoStatus, ssoBody := apiDo(t, baseB, p.method, p.path, ssoToken, p.body)
		if localStatus != ssoStatus {
			t.Fatalf("%s: status mismatch local=%d sso=%d (local body %s / sso body %s)",
				p.name, localStatus, ssoStatus, localBody, ssoBody)
		}
		if localStatus == http.StatusOK {
			continue
		}
		// 非 200 时还要求拒绝语义一致（同为 403 permission denied）
		if !strings.Contains(localBody, "permission denied") || !strings.Contains(ssoBody, "permission denied") {
			t.Fatalf("%s: deny semantics mismatch: local=%q sso=%q", p.name, localBody, ssoBody)
		}
	}
}
