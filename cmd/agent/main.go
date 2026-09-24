package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/SuPerCxyz/nodesteer/internal/agent"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"gopkg.in/yaml.v3"
)

// version 编译期注入版本（ldflags -X main.version=...），默认 dev。
// HELLO 上报的 agent_version 以该值为准，yaml/env 的 agent_version 旧键保留解析兼容但忽略。
var version = "dev"

// Config Agent 配置
type Config struct {
	HubURL            string   `yaml:"hub_url"`
	RegistrationToken string   `yaml:"registration_token"`
	AgentID           string   `yaml:"agent_id"`
	NodeName          string   `yaml:"node_name"`
	NodeIP            string   `yaml:"node_ip"`
	DeploymentMode    string   `yaml:"deployment_mode"`
	HostIntegration   bool     `yaml:"host_integration"`
	DataDir           string   `yaml:"data_dir"`
	HostRoot          string   `yaml:"host_root"`
	HostPathAllowlist []string `yaml:"host_path_allowlist"`
	TLSCAFile         string   `yaml:"tls_ca_file"`
	// AgentVersion 旧配置键：保留解析兼容；上报值由编译注入版本决定（见 resolveAgentVersion）
	AgentVersion     string `yaml:"agent_version"`
	HeartbeatSec     int    `yaml:"heartbeat_sec"`
	RevisionCheckSec int    `yaml:"revision_check_sec"`
	MaxLogBytes      int    `yaml:"max_log_bytes"`
}

// DefaultConfig 默认配置
func DefaultConfig() Config {
	return Config{
		// DeploymentMode 留空：未显式配置时由 resolveDeploymentMode 自动探测（显式配置优先）
		DataDir:          "/var/lib/nodesteer",
		HeartbeatSec:     30,
		RevisionCheckSec: 45,
		MaxLogBytes:      1 << 20,
		HostRoot:         "/host",
	}
}

func main() {
	var configPath string
	flag.StringVar(&configPath, "config", "", "config file path (yaml)")
	flag.Parse()

	cfg := DefaultConfig()
	if configPath != "" {
		data, err := os.ReadFile(configPath)
		if err != nil {
			fmt.Fprintf(os.Stderr, "read config: %v\n", err)
			os.Exit(1)
		}
		if err := yaml.Unmarshal(data, &cfg); err != nil {
			fmt.Fprintf(os.Stderr, "parse config: %v\n", err)
			os.Exit(1)
		}
	}
	applyEnv(&cfg)

	// deployment_mode：显式配置（yaml/env）> 自动探测 > native
	cfg.DeploymentMode = resolveDeploymentMode(cfg.DeploymentMode, detectDeploymentMode)
	// agent_version：编译注入值 > "dev" 回退；yaml/env agent_version 旧键忽略
	cfg.AgentVersion = resolveAgentVersion(version, cfg.AgentVersion)

	if cfg.HubURL == "" {
		fmt.Fprintln(os.Stderr, "hub_url is required")
		os.Exit(1)
	}

	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	os.MkdirAll(cfg.DataDir, 0o755)

	a, err := agent.New(agent.Config{
		HubURL:            cfg.HubURL,
		RegistrationToken: cfg.RegistrationToken,
		AgentID:           cfg.AgentID,
		NodeName:          cfg.NodeName,
		NodeIP:            cfg.NodeIP,
		DeploymentMode:    cfg.DeploymentMode,
		HostIntegration:   cfg.HostIntegration,
		DataDir:           cfg.DataDir,
		HostRoot:          cfg.HostRoot,
		HostPathAllowlist: cfg.HostPathAllowlist,
		TLSCAFile:         cfg.TLSCAFile,
		AgentVersion:      cfg.AgentVersion,
		HeartbeatSec:      cfg.HeartbeatSec,
		RevisionCheckSec:  cfg.RevisionCheckSec,
		MaxLogBytes:       cfg.MaxLogBytes,
	}, logger)
	if err != nil {
		fmt.Fprintf(os.Stderr, "init agent: %v\n", err)
		os.Exit(1)
	}
	defer a.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sig
		cancel()
	}()

	logger.Info("nodesteer agent starting",
		"version", cfg.AgentVersion, "hub", cfg.HubURL, "mode", cfg.DeploymentMode, "data_dir", cfg.DataDir)

	if err := a.Run(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "agent run: %v\n", err)
		os.Exit(1)
	}
	logger.Info("nodesteer agent stopped")
}

func applyEnv(cfg *Config) {
	if v := os.Getenv("NODESTEER_HUB_URL"); v != "" {
		cfg.HubURL = v
	}
	if v := os.Getenv("NODESTEER_REGISTRATION_TOKEN"); v != "" {
		cfg.RegistrationToken = v
	}
	if v := os.Getenv("NODESTEER_AGENT_ID"); v != "" {
		cfg.AgentID = v
	}
	if v := os.Getenv("NODESTEER_NODE_NAME"); v != "" {
		cfg.NodeName = v
	}
	if v := os.Getenv("NODESTEER_NODE_IP"); v != "" {
		cfg.NodeIP = v
	}
	if v := os.Getenv("NODESTEER_DEPLOYMENT_MODE"); v != "" {
		cfg.DeploymentMode = v
	}
	if v := os.Getenv("NODESTEER_HOST_INTEGRATION"); v == "true" {
		cfg.HostIntegration = true
	}
	if v := os.Getenv("NODESTEER_DATA_DIR"); v != "" {
		cfg.DataDir = v
	}
	if v := os.Getenv("NODESTEER_HOST_ROOT"); v != "" {
		cfg.HostRoot = v
	}
	if v := os.Getenv("NODESTEER_TLS_CA_FILE"); v != "" {
		cfg.TLSCAFile = v
	}
	if v := os.Getenv("NODESTEER_AGENT_VERSION"); v != "" {
		// 旧键：保留解析兼容，上报值由编译注入版本覆盖（resolveAgentVersion）
		cfg.AgentVersion = v
	}
}

// detectDeploymentMode 运行时容器环境探测（路径常量集中于此，便于测试注入替换）
func detectDeploymentMode() string {
	return agent.DetectDeploymentMode(agent.DefaultDockerEnvPath, agent.DefaultCgroupPath)
}

// resolveDeploymentMode 部署模式优先级：显式配置（yaml/env）> 自动探测 > native。
func resolveDeploymentMode(explicit string, detect func() string) string {
	if explicit != "" {
		return explicit
	}
	if detect != nil {
		if m := detect(); m != "" {
			return m
		}
	}
	return models.DeploymentModeNative
}

// resolveAgentVersion HELLO 上报的 agent_version：编译注入值优先，空值回退 "dev"；
// configured（yaml/env agent_version 旧键）仅保留解析兼容，不参与上报。
func resolveAgentVersion(compiled, configured string) string {
	_ = configured
	if compiled == "" {
		return "dev"
	}
	return compiled
}
