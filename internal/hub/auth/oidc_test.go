package auth

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"

	"github.com/go-jose/go-jose/v4"
	"github.com/go-jose/go-jose/v4/jwt"
)

// mockIdP 内存 IdP：提供 discovery、JWKS、token 端点
type mockIdP struct {
	server   *httptest.Server
	key      *rsa.PrivateKey
	issuer   string
	claims   map[string]any
	username string
	role     string
	nonce    string
}

func newMockIdP(t *testing.T) *mockIdP {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	idp := &mockIdP{key: key}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/.well-known/openid-configuration":
			writeJSON(t, w, map[string]any{
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
			writeJSON(t, w, map[string]any{
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
			// 校验 code 与 PKCE verifier（简单模拟，S256）
			q := r.PostFormValue("code_verifier")
			if q == "" {
				http.Error(w, "missing code_verifier", http.StatusBadRequest)
				return
			}
			idToken, err := idp.signIDToken()
			if err != nil {
				http.Error(w, err.Error(), http.StatusInternalServerError)
				return
			}
			writeJSON(t, w, map[string]any{
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
	return idp
}

func (m *mockIdP) signIDToken() (string, error) {
	signer, err := jose.NewSigner(jose.SigningKey{Algorithm: jose.RS256, Key: m.key}, (&jose.SignerOptions{}).
		WithType("JWT").WithHeader("kid", "test-key"))
	if err != nil {
		return "", err
	}
	now := time.Now()
	claims := map[string]any{
		"iss":   m.issuer,
		"sub":   "sub-123",
		"aud":   "test-client",
		"nonce": m.nonce,
		"iat":   now.Unix(),
		"exp":   now.Add(1 * time.Hour).Unix(),
		"email": "alice@example.com",
	}
	if m.username != "" {
		claims["preferred_username"] = m.username
	}
	if m.role != "" {
		claims["groups"] = []string{m.role, "everyone"}
	}
	for k, v := range m.claims {
		claims[k] = v
	}
	raw, err := jwt.Signed(signer).Claims(claims).Serialize()
	return raw, err
}

func (m *mockIdP) Close() { m.server.Close() }

func writeJSON(t *testing.T, w http.ResponseWriter, v any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

func newTestOIDC(t *testing.T, idp *mockIdP, mutate func(*OIDCConfig)) *OIDC {
	t.Helper()
	cfg := DefaultOIDCConfig()
	cfg.Issuer = idp.issuer
	cfg.ClientID = "test-client"
	cfg.RoleMappings = map[string]string{"admins": "administrator", "ops": "operator"}
	if mutate != nil {
		mutate(&cfg)
	}
	o, err := NewOIDC(context.Background(), cfg, "http://hub.test")
	if err != nil {
		t.Fatalf("NewOIDC: %v", err)
	}
	return o
}

// authCodeURLHelper 从授权 URL 提取 state 与 code_challenge
func parseAuthURL(t *testing.T, raw string) (state, verifier, nonce string) {
	t.Helper()
	u, err := url.Parse(raw)
	if err != nil {
		t.Fatalf("parse auth url: %v", err)
	}
	q := u.Query()
	if q.Get("state") == "" {
		t.Fatal("missing state")
	}
	if q.Get("code_challenge") == "" || q.Get("code_challenge_method") != "S256" {
		t.Fatalf("missing/invalid code_challenge: %s %s", q.Get("code_challenge"), q.Get("code_challenge_method"))
	}
	if q.Get("nonce") == "" {
		t.Fatal("missing nonce")
	}
	return q.Get("state"), q.Get("code_challenge"), q.Get("nonce")
}

func TestOIDCDisabled(t *testing.T) {
	cfg := DefaultOIDCConfig()
	if _, err := NewOIDC(context.Background(), cfg, "http://hub.test"); err != ErrOIDCDisabled {
		t.Fatalf("expected ErrOIDCDisabled, got %v", err)
	}
	var nilOIDC *OIDC
	if nilOIDC.Enabled() {
		t.Fatal("nil OIDC should be disabled")
	}
}

func TestOIDCExchangeSuccess(t *testing.T) {
	idp := newMockIdP(t)
	defer idp.Close()
	idp.username = "alice"
	idp.role = "admins"

	o := newTestOIDC(t, idp, nil)
	authURL, err := o.AuthCodeURL()
	if err != nil {
		t.Fatal(err)
	}
	state, _, nonce := parseAuthURL(t, authURL)
	idp.nonce = nonce

	username, role, err := o.Exchange(context.Background(), state, "mock-code")
	if err != nil {
		t.Fatalf("Exchange: %v", err)
	}
	if username != "alice" {
		t.Fatalf("username = %q, want alice", username)
	}
	if role != "administrator" {
		t.Fatalf("role = %q, want administrator", role)
	}
}

func TestOIDCExchangeRoleDefault(t *testing.T) {
	idp := newMockIdP(t)
	defer idp.Close()
	idp.username = "bob"
	idp.role = "random-group"

	o := newTestOIDC(t, idp, nil)
	authURL, _ := o.AuthCodeURL()
	state, _, nonce := parseAuthURL(t, authURL)
	idp.nonce = nonce

	_, role, err := o.Exchange(context.Background(), state, "mock-code")
	if err != nil {
		t.Fatal(err)
	}
	if role != "viewer" {
		t.Fatalf("role = %q, want viewer (default)", role)
	}
}

func TestOIDCExchangeUsernameFallback(t *testing.T) {
	idp := newMockIdP(t)
	defer idp.Close()
	idp.username = "" // 无 preferred_username，fallback email
	idp.claims = map[string]any{"email": "carol@example.com"}

	o := newTestOIDC(t, idp, nil)
	authURL, _ := o.AuthCodeURL()
	state, _, nonce := parseAuthURL(t, authURL)
	idp.nonce = nonce

	username, _, err := o.Exchange(context.Background(), state, "mock-code")
	if err != nil {
		t.Fatal(err)
	}
	if username != "carol@example.com" {
		t.Fatalf("username = %q, want carol@example.com", username)
	}
}

func TestOIDCExchangeCustomClaims(t *testing.T) {
	idp := newMockIdP(t)
	defer idp.Close()
	idp.username = "dave"
	idp.claims = map[string]any{"email": "dave@example.com", "roles": []string{"ops"}}

	o := newTestOIDC(t, idp, func(c *OIDCConfig) {
		c.UsernameClaim = "email"
		c.RoleClaim = "roles"
	})
	authURL, _ := o.AuthCodeURL()
	state, _, nonce := parseAuthURL(t, authURL)
	idp.nonce = nonce

	username, role, err := o.Exchange(context.Background(), state, "mock-code")
	if err != nil {
		t.Fatal(err)
	}
	if username != "dave@example.com" {
		t.Fatalf("username = %q, want dave@example.com (email claim)", username)
	}
	if role != "operator" {
		t.Fatalf("role = %q, want operator", role)
	}
}

func TestOIDCExchangeInvalidState(t *testing.T) {
	idp := newMockIdP(t)
	defer idp.Close()
	o := newTestOIDC(t, idp, nil)
	if _, _, err := o.Exchange(context.Background(), "nonexistent-state", "code"); err != ErrOIDCStateInvalid {
		t.Fatalf("expected ErrOIDCStateInvalid, got %v", err)
	}
}

func TestOIDCExchangeReplayState(t *testing.T) {
	idp := newMockIdP(t)
	defer idp.Close()
	o := newTestOIDC(t, idp, nil)
	authURL, _ := o.AuthCodeURL()
	state, _, nonce := parseAuthURL(t, authURL)
	idp.nonce = nonce
	if _, _, err := o.Exchange(context.Background(), state, "mock-code"); err != nil {
		t.Fatal(err)
	}
	// 第二次使用同一 state 应失败（一次性）
	if _, _, err := o.Exchange(context.Background(), state, "mock-code"); err != ErrOIDCStateInvalid {
		t.Fatalf("expected ErrOIDCStateInvalid on replay, got %v", err)
	}
}

func TestOIDCMissingPKCEVerifier(t *testing.T) {
	idp := newMockIdP(t)
	defer idp.Close()
	idp.username = "alice"

	// 直接调 token 端点：模拟 IdP 拒绝无 PKCE
	form := url.Values{}
	form.Set("grant_type", "authorization_code")
	form.Set("code", "mock-code")
	form.Set("redirect_uri", "http://hub.test/api/oidc/callback")
	resp, err := http.PostForm(idp.server.URL+"/token", form)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		t.Fatal("token endpoint should reject missing code_verifier")
	}
}
