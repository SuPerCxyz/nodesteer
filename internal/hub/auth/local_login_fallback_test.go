package auth

import "testing"

// allowLocal 返回指向 v 的指针（三态开关测试辅助）。
func allowLocal(v bool) *bool { return &v }

// allow_local_login 是两种登录方式的互斥选择器，nil（未显式配置）视为 true（默认本地模式）。
func TestLocalLoginAllowedTriState(t *testing.T) {
	var zero OIDCConfig
	if !zero.LocalLoginAllowed() {
		t.Fatal("zero value (nil) must default to local login mode")
	}
	if got := DefaultOIDCConfig().LocalLoginAllowed(); !got {
		t.Fatal("DefaultOIDCConfig must default to local login mode")
	}
	if (OIDCConfig{AllowLocalLogin: allowLocal(true)}).LocalLoginAllowed() != true {
		t.Fatal("explicit true must select local login mode")
	}
	if (OIDCConfig{AllowLocalLogin: allowLocal(false)}).LocalLoginAllowed() {
		t.Fatal("explicit false must select sso-only mode")
	}
}

// LocalLoginDisabled 只取决于 allow_local_login 开关：
// false 模式恒禁用（绝对语义，不依赖 OIDC 是否启用），true 模式恒可用。
func TestLocalLoginDisabledFollowsSwitch(t *testing.T) {
	m, _ := newAuthTestManager(t)

	// 默认（nil → true，本地模式）：本地登录可用
	if m.LocalLoginDisabled() {
		t.Fatal("local login should be enabled by default")
	}
	if !m.LocalLoginAllowed() {
		t.Fatal("default mode should be local-allowed")
	}
	if !m.OIDC().LocalFallback() {
		t.Fatal("nil OIDC should report local fallback true in default local mode")
	}

	// false 模式（SSO-only）：即使 OIDC 客户端尚未注入，密码登录也禁用（绝对语义）
	m.SetLocalLoginAllowed(false)
	if !m.LocalLoginDisabled() {
		t.Fatal("local login should be disabled when allow_local_login=false, regardless of oidc client")
	}

	// true 模式：即使存在 OIDC 客户端也不禁本地登录
	m.SetLocalLoginAllowed(true)
	m.SetOIDC(&OIDC{cfg: OIDCConfig{Issuer: "https://idp.example"}})
	if m.LocalLoginDisabled() {
		t.Fatal("local login should stay enabled in local mode even with oidc client set")
	}
	if !m.OIDC().LocalFallback() {
		t.Fatal("local fallback should report true in local mode")
	}

	// false 模式 + OIDC 客户端（SSO-only 正常接线）：禁本地，LocalFallback=false
	ssoCfg := OIDCConfig{Issuer: "https://idp.example", AllowLocalLogin: allowLocal(false)}
	m.SetLocalLoginAllowed(false)
	m.SetOIDC(&OIDC{cfg: ssoCfg})
	if !m.LocalLoginDisabled() {
		t.Fatal("local login should be disabled in sso-only mode")
	}
	if m.OIDC().LocalFallback() {
		t.Fatal("local fallback should report false in sso-only mode")
	}
	if !m.OIDC().Enabled() {
		t.Fatal("oidc client should stay enabled in sso-only mode")
	}

	// 清除 OIDC 后开关语义不变
	m.SetOIDC(nil)
	if !m.LocalLoginDisabled() {
		t.Fatal("switch must not depend on oidc client presence")
	}
}
