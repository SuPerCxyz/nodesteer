package hub

import (
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestNormalizeDeploymentMode 枚举白名单：合法值原样，非法/空值归一化为 native。
func TestNormalizeDeploymentMode(t *testing.T) {
	valid := []string{
		models.DeploymentModeNative,
		models.DeploymentModeDocker,
		models.DeploymentModeDockerHostInt,
	}
	for _, m := range valid {
		if got := normalizeDeploymentMode(m); got != m {
			t.Fatalf("valid mode %q must stay unchanged, got %q", m, got)
		}
	}

	invalid := []string{
		"",
		"vmware",
		"container", // host 适配器内部别名，非上报枚举
		"DOCKER",
		"native ",
	}
	for _, m := range invalid {
		if got := normalizeDeploymentMode(m); got != models.DeploymentModeNative {
			t.Fatalf("invalid mode %q must normalize to native, got %q", m, got)
		}
	}
}

// TestIsDispatchableStatus 暂停（maintenance/disabled）判定为不可下发，其余状态可下发。
func TestIsDispatchableStatus(t *testing.T) {
	blocked := []string{models.NodeStatusMaintenance, models.NodeStatusDisabled}
	for _, s := range blocked {
		if isDispatchableStatus(s) {
			t.Fatalf("status %q must be blocked from dispatch", s)
		}
	}
	allowed := []string{models.NodeStatusOnline, models.NodeStatusOffline, models.NodeStatusPending}
	for _, s := range allowed {
		if !isDispatchableStatus(s) {
			t.Fatalf("status %q must not be blocked (offline/pending handled by session gate)", s)
		}
	}
}
