package auth

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/coreos/go-oidc/v3/oidc"
	"golang.org/x/oauth2"
)

// ErrOIDCDisabled OIDC 未启用
var ErrOIDCDisabled = errors.New("oidc disabled")

// ErrOIDCStateInvalid state 校验失败
var ErrOIDCStateInvalid = errors.New("invalid oidc state")

// OIDCConfig OIDC 配置
type OIDCConfig struct {
	Issuer        string            `yaml:"issuer"`
	ClientID      string            `yaml:"client_id"`
	RedirectURL   string            `yaml:"redirect_url"`
	Scopes        []string          `yaml:"scopes"`
	UsernameClaim string            `yaml:"username_claim"`
	RoleClaim     string            `yaml:"role_claim"`
	RoleMappings  map[string]string `yaml:"role_mappings"`
	DefaultRole   string            `yaml:"default_role"`
	// AllowLocalLogin 本地账号密码登录兜底（break-glass）：OIDC 启用时仍允许本地登录。
	// 默认 false 保持严格语义（OIDC 启用即禁本地）。
	AllowLocalLogin bool `yaml:"allow_local_login"`
}

// DefaultOIDCConfig 返回默认 OIDC 配置
func DefaultOIDCConfig() OIDCConfig {
	return OIDCConfig{
		Scopes:        []string{"openid", "profile", "email"},
		UsernameClaim: "preferred_username",
		RoleClaim:     "groups",
		DefaultRole:   models.RoleViewer,
	}
}

// oidcState 一次授权请求的 state + PKCE verifier
type oidcState struct {
	verifier string
	nonce    string
	expires  time.Time
}

const oidcStateTTL = 10 * time.Minute

// OIDC OIDC 认证客户端
type OIDC struct {
	cfg      OIDCConfig
	provider *oidc.Provider
	verifier *oidc.IDTokenVerifier
	oauthCfg oauth2.Config
	mu       sync.Mutex
	states   map[string]*oidcState
	baseURL  string
}

// NewOIDC 创建 OIDC 客户端
func NewOIDC(ctx context.Context, cfg OIDCConfig, baseURL string) (*OIDC, error) {
	if cfg.Issuer == "" {
		return nil, ErrOIDCDisabled
	}
	if cfg.ClientID == "" {
		return nil, fmt.Errorf("oidc client_id is required")
	}
	if cfg.UsernameClaim == "" {
		cfg.UsernameClaim = "preferred_username"
	}
	if cfg.RoleClaim == "" {
		cfg.RoleClaim = "groups"
	}
	if cfg.DefaultRole == "" {
		cfg.DefaultRole = models.RoleViewer
	}
	if !ValidRole(cfg.DefaultRole) {
		return nil, fmt.Errorf("oidc default_role is invalid")
	}
	for group, role := range cfg.RoleMappings {
		if !ValidRole(role) {
			return nil, fmt.Errorf("oidc role mapping %q is invalid", group)
		}
	}
	if len(cfg.Scopes) == 0 {
		cfg.Scopes = []string{"openid", "profile", "email"}
	}
	if cfg.RedirectURL == "" {
		cfg.RedirectURL = baseURL + "/api/oidc/callback"
	}
	provider, err := oidc.NewProvider(ctx, cfg.Issuer)
	if err != nil {
		return nil, fmt.Errorf("oidc provider: %w", err)
	}
	return &OIDC{
		cfg:      cfg,
		provider: provider,
		verifier: provider.Verifier(&oidc.Config{ClientID: cfg.ClientID}),
		oauthCfg: oauth2.Config{
			ClientID:    cfg.ClientID,
			Endpoint:    provider.Endpoint(),
			RedirectURL: cfg.RedirectURL,
			Scopes:      cfg.Scopes,
		},
		states:  map[string]*oidcState{},
		baseURL: baseURL,
	}, nil
}

// Enabled 是否启用
func (o *OIDC) Enabled() bool { return o != nil && o.cfg.Issuer != "" }

// LocalFallback 本地登录兜底是否生效（OIDC 启用且 allow_local_login 开启）。nil 安全。
func (o *OIDC) LocalFallback() bool {
	return o != nil && o.cfg.Issuer != "" && o.cfg.AllowLocalLogin
}

// BaseURL 返回回跳基址
func (o *OIDC) BaseURL() string { return o.baseURL }

// AuthCodeURL 生成带 state + PKCE 的授权跳转 URL
func (o *OIDC) AuthCodeURL() (string, error) {
	state, err := randomToken()
	if err != nil {
		return "", err
	}
	verifier := oauth2.GenerateVerifier()
	nonce, err := randomToken()
	if err != nil {
		return "", err
	}
	o.mu.Lock()
	o.states[state] = &oidcState{verifier: verifier, nonce: nonce, expires: time.Now().Add(oidcStateTTL)}
	o.mu.Unlock()
	// 清理过期 state
	go o.cleanup()
	return o.oauthCfg.AuthCodeURL(state, oauth2.S256ChallengeOption(verifier), oauth2.SetAuthURLParam("nonce", nonce)), nil
}

// Exchange 处理 callback：校验 state、交换 token、验证 ID Token、提取用户与角色
func (o *OIDC) Exchange(ctx context.Context, state, code string) (username, role string, err error) {
	o.mu.Lock()
	st, ok := o.states[state]
	if !ok || time.Now().After(st.expires) {
		o.mu.Unlock()
		return "", "", ErrOIDCStateInvalid
	}
	delete(o.states, state)
	verifier := st.verifier
	nonce := st.nonce
	o.mu.Unlock()

	raw, err := o.oauthCfg.Exchange(ctx, code, oauth2.VerifierOption(verifier))
	if err != nil {
		return "", "", fmt.Errorf("oidc token exchange: %w", err)
	}
	idToken, ok := raw.Extra("id_token").(string)
	if !ok || idToken == "" {
		return "", "", errors.New("oidc: no id_token in response")
	}
	verified, err := o.verifier.Verify(ctx, idToken)
	if err != nil {
		return "", "", fmt.Errorf("oidc id token verify: %w", err)
	}

	var claims map[string]any
	if err := verified.Claims(&claims); err != nil {
		return "", "", fmt.Errorf("oidc claims: %w", err)
	}
	claimNonce, ok := claims["nonce"].(string)
	if !ok || claimNonce == "" || claimNonce != nonce {
		return "", "", errors.New("oidc nonce validation failed")
	}
	username = o.extractUsername(claims)
	if username == "" {
		return "", "", errors.New("oidc: cannot determine username from claims")
	}
	role = o.mapRole(claims)
	return username, role, nil
}

// extractUsername 按配置的 username claim 提取，fallback email/sub
func (o *OIDC) extractUsername(claims map[string]any) string {
	if v, ok := claims[o.cfg.UsernameClaim]; ok {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
	}
	for _, k := range []string{"email", "sub"} {
		if v, ok := claims[k]; ok {
			if s, ok := v.(string); ok && s != "" {
				return s
			}
		}
	}
	return ""
}

// mapRole 按 role claim 映射角色
func (o *OIDC) mapRole(claims map[string]any) string {
	if o.cfg.RoleClaim == "" || len(o.cfg.RoleMappings) == 0 {
		return o.cfg.DefaultRole
	}
	groups := o.extractStringSlice(claims[o.cfg.RoleClaim])
	for _, g := range groups {
		if r, ok := o.cfg.RoleMappings[g]; ok {
			return r
		}
	}
	return o.cfg.DefaultRole
}

// extractStringSlice 从 claim 提取字符串切片（兼容单字符串）
func (o *OIDC) extractStringSlice(v any) []string {
	switch t := v.(type) {
	case []any:
		out := []string{}
		for _, e := range t {
			if s, ok := e.(string); ok {
				out = append(out, s)
			}
		}
		return out
	case []string:
		return t
	case string:
		if t != "" {
			return []string{t}
		}
	}
	return nil
}

// cleanup 清理过期 state
func (o *OIDC) cleanup() {
	o.mu.Lock()
	defer o.mu.Unlock()
	now := time.Now()
	for k, v := range o.states {
		if now.After(v.expires) {
			delete(o.states, k)
		}
	}
}

// randomToken 生成随机字符串
func randomToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
