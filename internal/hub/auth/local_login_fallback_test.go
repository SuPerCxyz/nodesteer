package auth

import "testing"

// LocalLoginDisabled 必须由 allow_local_login 显式控制，默认严格禁用。
func TestLocalLoginDisabledToggle(t *testing.T) {
	m, _ := newAuthTestManager(t)

	// 未设置 OIDC：本地登录可用
	if m.LocalLoginDisabled() {
		t.Fatal("local login should be enabled when oidc is not set")
	}

	// OIDC 启用、兜底关闭（默认）：本地登录禁用
	m.SetOIDC(&OIDC{cfg: OIDCConfig{Issuer: "https://idp.example"}})
	if !m.LocalLoginDisabled() {
		t.Fatal("local login should be disabled when oidc enabled without fallback")
	}
	if m.OIDC().LocalFallback() {
		t.Fatal("local fallback should be false by default")
	}

	// OIDC 启用 + 兜底开启：本地登录保持可用
	m.SetOIDC(&OIDC{cfg: OIDCConfig{
		Issuer:          "https://idp.example",
		AllowLocalLogin: true,
	}})
	if m.LocalLoginDisabled() {
		t.Fatal("local login should stay enabled with allow_local_login")
	}
	if !m.OIDC().LocalFallback() {
		t.Fatal("local fallback should report true when enabled")
	}

	// 回到未启用：可用
	m.SetOIDC(nil)
	if m.LocalLoginDisabled() {
		t.Fatal("local login should be enabled after oidc cleared")
	}
	// nil OIDC 上调用 LocalFallback 不得 panic
	var o *OIDC
	if o.LocalFallback() || o.Enabled() {
		t.Fatal("nil OIDC should report false for Enabled/LocalFallback")
	}
}
