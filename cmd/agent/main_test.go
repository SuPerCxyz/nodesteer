package main

import (
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestVersionDefaultsToDev 版本默认 dev；编译注入值优先，yaml/env 的 agent_version 被忽略。
func TestVersionDefaultsToDev(t *testing.T) {
	if version != "dev" {
		t.Fatalf("version default = %q, want dev (no ldflags injection in tests)", version)
	}
	cases := []struct {
		compiled, configured, want string
	}{
		{"", "", "dev"},               // 编译值空 → dev 回退
		{"", "0.1.0", "dev"},          // 手写 0.1.0 不得进入上报
		{version, "0.1.0", "dev"},     // 测试态编译值即 dev
		{"v1.2.3", "0.1.0", "v1.2.3"}, // 编译值优先
		{"v2.0.0-rc1", "", "v2.0.0-rc1"},
	}
	for _, c := range cases {
		if got := resolveAgentVersion(c.compiled, c.configured); got != c.want {
			t.Fatalf("resolveAgentVersion(%q, %q) = %q, want %q", c.compiled, c.configured, got, c.want)
		}
	}
}

// TestResolveAgentVersionIgnoresEnv 旧键 NODESTEER_AGENT_VERSION 保留解析兼容，但不参与上报。
func TestResolveAgentVersionIgnoresEnv(t *testing.T) {
	t.Setenv("NODESTEER_AGENT_VERSION", "9.9.9")
	cfg := DefaultConfig()
	cfg.AgentVersion = "0.1.0"
	applyEnv(&cfg)
	if cfg.AgentVersion != "9.9.9" {
		t.Fatalf("legacy env key should still parse for compat, got %q", cfg.AgentVersion)
	}
	if got := resolveAgentVersion(version, cfg.AgentVersion); got != "dev" {
		t.Fatalf("reported version = %q, want dev (configured value ignored)", got)
	}
}

// TestResolveDeploymentModePrecedence 显式配置（yaml/env）> 自动探测 > native。
func TestResolveDeploymentModePrecedence(t *testing.T) {
	dockerDetect := func() string { return models.DeploymentModeDocker }
	emptyDetect := func() string { return "" }

	if got := resolveDeploymentMode(models.DeploymentModeNative, dockerDetect); got != models.DeploymentModeNative {
		t.Fatalf("explicit config must beat detection, got %q", got)
	}
	if got := resolveDeploymentMode(models.DeploymentModeDocker, emptyDetect); got != models.DeploymentModeDocker {
		t.Fatalf("explicit config kept as-is, got %q", got)
	}
	if got := resolveDeploymentMode("", dockerDetect); got != models.DeploymentModeDocker {
		t.Fatalf("missing explicit config should fall back to detection, got %q", got)
	}
	if got := resolveDeploymentMode("", emptyDetect); got != models.DeploymentModeNative {
		t.Fatalf("detection miss should fall back to native, got %q", got)
	}
	if got := resolveDeploymentMode("", nil); got != models.DeploymentModeNative {
		t.Fatalf("nil detector should fall back to native, got %q", got)
	}
}

// TestDefaultConfigHasNoGeneratedKeys 纳管模板去硬编码键后，默认配置同样不再提供
// 这些生成参数的固定默认值（data_dir 为本地路径默认保留）。
func TestDefaultConfigHasNoGeneratedKeys(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.DeploymentMode != "" {
		t.Fatalf("deployment_mode default = %q, want empty (auto detect)", cfg.DeploymentMode)
	}
	if cfg.AgentVersion != "" {
		t.Fatalf("agent_version default = %q, want empty (compile-injected)", cfg.AgentVersion)
	}
	if cfg.DataDir == "" {
		t.Fatal("data_dir local default should remain")
	}
}
