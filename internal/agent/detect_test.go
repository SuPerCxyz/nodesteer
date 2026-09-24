package agent

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// TestDetectDeploymentMode 探测：dockerenv 存在 / cgroup 命中关键字 → docker；否则 native。
func TestDetectDeploymentMode(t *testing.T) {
	dir := t.TempDir()
	dockerenv := filepath.Join(dir, ".dockerenv")
	cgroup := filepath.Join(dir, "cgroup")
	if err := os.WriteFile(dockerenv, []byte(""), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cgroup, []byte("0::/user.slice\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	// dockerenv 存在 → docker
	if got := DetectDeploymentMode(dockerenv, cgroup); got != models.DeploymentModeDocker {
		t.Fatalf("dockerenv present: got %q, want docker", got)
	}

	missingEnv := filepath.Join(dir, "no-such-dockerenv")
	cases := []struct {
		name, content, want string
	}{
		{"docker keyword", "12:cpuset:/docker/abc\n", models.DeploymentModeDocker},
		{"lxc keyword", "1:name=systemd:/lxc/def\n", models.DeploymentModeDocker},
		{"kubepods keyword", "0::/kubepods/pod123\n", models.DeploymentModeDocker},
		{"containerd keyword", "0::/system.slice/containerd.service\n", models.DeploymentModeDocker},
		{"plain cgroup", "0::/user.slice\n", models.DeploymentModeNative},
	}
	for _, c := range cases {
		if err := os.WriteFile(cgroup, []byte(c.content), 0o644); err != nil {
			t.Fatal(err)
		}
		if got := DetectDeploymentMode(missingEnv, cgroup); got != c.want {
			t.Fatalf("%s: got %q, want %q", c.name, got, c.want)
		}
	}

	// 两个路径都不存在 → native
	if got := DetectDeploymentMode(missingEnv, filepath.Join(dir, "no-such-cgroup")); got != models.DeploymentModeNative {
		t.Fatalf("missing both probes: got %q, want native", got)
	}
}

// newTestAgent 构造测试 Agent。
func newTestAgent(t *testing.T, cfg Config) *Agent {
	t.Helper()
	if cfg.HubURL == "" {
		cfg.HubURL = "ws://127.0.0.1:8443"
	}
	if cfg.DataDir == "" {
		cfg.DataDir = t.TempDir()
	}
	a, err := New(cfg, discardLogger())
	if err != nil {
		t.Fatalf("new agent: %v", err)
	}
	t.Cleanup(func() { _ = a.Close() })
	return a
}

// TestHostIntegrationDerivedFromMode host_integration 由 deployment_mode 推导，不再独立读配置。
func TestHostIntegrationDerivedFromMode(t *testing.T) {
	hi := newTestAgent(t, Config{DeploymentMode: models.DeploymentModeDockerHostInt})
	if !hi.cfg.HostIntegration {
		t.Fatal("docker_host_integration must derive host_integration=true")
	}
	if !hi.helloFn().HostIntegration {
		t.Fatal("HELLO host_integration must follow derived value")
	}

	// 显式 HostIntegration=true + docker：推导后必须被忽略（不再独立读配置）
	ignored := newTestAgent(t, Config{DeploymentMode: models.DeploymentModeDocker, HostIntegration: true})
	if ignored.cfg.HostIntegration {
		t.Fatal("host_integration config must be ignored; derived from deployment_mode only")
	}
	if ignored.helloFn().HostIntegration {
		t.Fatal("HELLO must not report config-driven host_integration")
	}

	native := newTestAgent(t, Config{DeploymentMode: models.DeploymentModeNative})
	if native.cfg.HostIntegration {
		t.Fatal("native mode must not enable host_integration")
	}

	// 缺省 deployment_mode → native 回退
	blank := newTestAgent(t, Config{})
	if blank.cfg.DeploymentMode != models.DeploymentModeNative {
		t.Fatalf("blank deployment_mode = %q, want native", blank.cfg.DeploymentMode)
	}
	if blank.helloFn().DeploymentMode != models.DeploymentModeNative {
		t.Fatalf("HELLO deployment_mode = %q, want native", blank.helloFn().DeploymentMode)
	}
}

// TestHelloUsesInjectedAgentVersion HELLO 上报使用注入的编译版本；空值兜底 dev。
func TestHelloUsesInjectedAgentVersion(t *testing.T) {
	injected := newTestAgent(t, Config{AgentVersion: "v9.9.9-test", DeploymentMode: models.DeploymentModeNative})
	if got := injected.helloFn().AgentVersion; got != "v9.9.9-test" {
		t.Fatalf("HELLO agent_version = %q, want injected v9.9.9-test", got)
	}

	blank := newTestAgent(t, Config{DeploymentMode: models.DeploymentModeNative})
	if got := blank.helloFn().AgentVersion; got != "dev" {
		t.Fatalf("HELLO agent_version = %q, want dev fallback", got)
	}
}
