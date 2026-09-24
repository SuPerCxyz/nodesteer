package agent

import (
	"os"
	"strings"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// 默认探测路径：容器运行时标记文件与 1 号进程 cgroup。
const (
	DefaultDockerEnvPath = "/.dockerenv"
	DefaultCgroupPath    = "/proc/1/cgroup"
)

// cgroup 容器运行时关键字（docker/lxc/kubepods/containerd）。
var cgroupContainerKeywords = []string{"docker", "lxc", "kubepods", "containerd"}

// DetectDeploymentMode 自动探测部署模式：存在 dockerenv 标记文件，或 cgroup 命中
// 容器运行时关键字 → docker；否则 native。路径可注入以便测试。
func DetectDeploymentMode(dockerenvPath, cgroupPath string) string {
	if dockerenvPath != "" {
		if _, err := os.Stat(dockerenvPath); err == nil {
			return models.DeploymentModeDocker
		}
	}
	if cgroupPath != "" {
		if b, err := os.ReadFile(cgroupPath); err == nil {
			low := strings.ToLower(string(b))
			for _, kw := range cgroupContainerKeywords {
				if strings.Contains(low, kw) {
					return models.DeploymentModeDocker
				}
			}
		}
	}
	return models.DeploymentModeNative
}

// HostIntegrationFromMode 由 deployment_mode 推导 host_integration（不再独立读配置）。
// 现状语义：宿主机集成仅在 docker_host_integration 模式生效；native 自身即具备宿主能力
// （capabilities 走 mode==native 分支），docker 纯容器形态不具备。
func HostIntegrationFromMode(mode string) bool {
	return mode == models.DeploymentModeDockerHostInt
}
