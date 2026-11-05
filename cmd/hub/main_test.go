package main

import (
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

func TestDefaultConfigRequiresExplicitAdminPassword(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.AdminPassword != "" {
		t.Fatal("default admin password must be empty")
	}
	if validateAdminCredentials(cfg) == nil {
		t.Fatal("empty admin credentials must be rejected")
	}
	cfg.AdminUsername = "configured-admin"
	cfg.AdminPassword = "configured-password"
	if err := validateAdminCredentials(cfg); err != nil {
		t.Fatalf("configured credentials rejected: %v", err)
	}
}

// allow_empty_password 默认关闭：仅显式开启时才允许空管理员密码，生产默认行为不变。
func TestAllowEmptyPasswordConfig(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.AllowEmptyPassword {
		t.Fatal("allow_empty_password must default to false")
	}
	cfg.AdminUsername = "test-admin"
	if validateAdminCredentials(cfg) == nil {
		t.Fatal("empty admin password must be rejected when switch is off")
	}

	cfg.AllowEmptyPassword = true
	if err := validateAdminCredentials(cfg); err != nil {
		t.Fatalf("empty admin password should be allowed when switch is on: %v", err)
	}
	cfg.AdminUsername = ""
	if validateAdminCredentials(cfg) == nil {
		t.Fatal("empty admin username must still be rejected")
	}

	// yaml 显式开启
	var parsed Config
	if err := yaml.Unmarshal([]byte("allow_empty_password: true\nadmin_username: yaml-admin\n"), &parsed); err != nil {
		t.Fatalf("yaml unmarshal: %v", err)
	}
	if !parsed.AllowEmptyPassword {
		t.Fatal("yaml allow_empty_password=true not applied")
	}
	if err := validateAdminCredentials(parsed); err != nil {
		t.Fatalf("yaml-enabled empty password rejected: %v", err)
	}

	// env=true 覆盖默认值
	t.Setenv("NODESTEER_ALLOW_EMPTY_PASSWORD", "true")
	envCfg := DefaultConfig()
	envCfg.AdminUsername = "env-admin"
	if err := applyEnv(&envCfg); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if !envCfg.AllowEmptyPassword {
		t.Fatal("env=true must enable allow_empty_password")
	}
	if err := validateAdminCredentials(envCfg); err != nil {
		t.Fatalf("env-enabled empty password rejected: %v", err)
	}

	// env=false 覆盖 yaml true
	t.Setenv("NODESTEER_ALLOW_EMPTY_PASSWORD", "false")
	disabled := Config{AllowEmptyPassword: true}
	if err := applyEnv(&disabled); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if disabled.AllowEmptyPassword {
		t.Fatal("env=false must disable allow_empty_password")
	}

	// 非法值必须报错（安全开关不得被静默忽略）
	t.Setenv("NODESTEER_ALLOW_EMPTY_PASSWORD", "flase")
	if err := applyEnv(&envCfg); err == nil {
		t.Fatal("invalid bool env must be rejected")
	} else if !strings.Contains(err.Error(), "NODESTEER_ALLOW_EMPTY_PASSWORD") {
		t.Fatalf("error should name the env var, got: %v", err)
	}
}

// allow_local_login 三态：默认/yaml 显式值/env 覆盖必须按互斥语义生效（nil=true）。
func TestAllowLocalLoginTriState(t *testing.T) {
	// 1) 未配置 yaml、未设置 env → 默认 true（本地模式）
	cfg := DefaultConfig()
	if !cfg.OIDC.LocalLoginAllowed() {
		t.Fatal("default must be local login mode (nil means true)")
	}
	t.Setenv("NODESTEER_OIDC_ALLOW_LOCAL_LOGIN", "")
	if err := applyEnv(&cfg); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if !cfg.OIDC.LocalLoginAllowed() {
		t.Fatal("unset/empty env must not override default (local mode)")
	}

	// 2) yaml 显式 false，env 未设置 → 保持 false
	var parsed Config
	if err := yaml.Unmarshal([]byte("oidc:\n  allow_local_login: false\n"), &parsed); err != nil {
		t.Fatalf("yaml unmarshal: %v", err)
	}
	if parsed.OIDC.LocalLoginAllowed() {
		t.Fatal("yaml false must select sso-only mode")
	}
	if err := applyEnv(&parsed); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if parsed.OIDC.LocalLoginAllowed() {
		t.Fatal("unset env must not override yaml false")
	}

	// 3) yaml false，env=true → env 覆盖回本地模式
	t.Setenv("NODESTEER_OIDC_ALLOW_LOCAL_LOGIN", "true")
	if err := applyEnv(&parsed); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if !parsed.OIDC.LocalLoginAllowed() {
		t.Fatal("env=true must override yaml to local mode")
	}

	// 4) env=false → SSO-only 模式
	cfg2 := DefaultConfig()
	t.Setenv("NODESTEER_OIDC_ALLOW_LOCAL_LOGIN", "false")
	if err := applyEnv(&cfg2); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if cfg2.OIDC.LocalLoginAllowed() {
		t.Fatal("env=false must select sso-only mode")
	}

	// 5) 非法值必须报错（安全开关不得被静默忽略）
	t.Setenv("NODESTEER_OIDC_ALLOW_LOCAL_LOGIN", "flase")
	if err := applyEnv(&cfg2); err == nil {
		t.Fatal("invalid bool env must be rejected")
	} else if !strings.Contains(err.Error(), "NODESTEER_OIDC_ALLOW_LOCAL_LOGIN") {
		t.Fatalf("error should name the env var, got: %v", err)
	}
}
