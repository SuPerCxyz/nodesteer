package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent"
	"github.com/SuPerCxyz/nodesteer/internal/agent/connection"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"gopkg.in/yaml.v3"
)

// version 编译期注入版本（ldflags -X main.version=...），默认 dev。
// HELLO 上报的 agent_version 以该值为准，yaml/env 的 agent_version 旧键保留解析兼容但忽略。
var version = "dev"

// Config Agent 配置
type Config struct {
	HubURL                       string              `yaml:"hub_url"`
	RegistrationToken            string              `yaml:"registration_token"`
	AgentID                      string              `yaml:"agent_id"`
	NodeName                     string              `yaml:"node_name"`
	NodeIP                       string              `yaml:"node_ip"`
	DeploymentMode               string              `yaml:"deployment_mode"`
	HostIntegration              bool                `yaml:"host_integration"`
	DataDir                      string              `yaml:"data_dir"`
	HostRoot                     string              `yaml:"host_root"`
	HostPathAllowlist            []string            `yaml:"host_path_allowlist"`
	ConnectionMode               string              `yaml:"connection_mode"`
	Inbound                      InboundConfig       `yaml:"inbound"`
	MonitoringAllowedCIDRs       []string            `yaml:"monitoring_allowed_cidrs"`
	AllowInsecureMonitorHub      bool                `yaml:"allow_insecure_monitor_hub"`
	MonitoringSecretDestinations map[string][]string `yaml:"monitoring_secret_destinations"`
	TLSCAFile                    string              `yaml:"tls_ca_file"`
	// AgentVersion 旧配置键：保留解析兼容；上报值由编译注入版本决定（见 resolveAgentVersion）
	AgentVersion     string `yaml:"agent_version"`
	HeartbeatSec     int    `yaml:"heartbeat_sec"`
	RevisionCheckSec int    `yaml:"revision_check_sec"`
	MaxLogBytes      int    `yaml:"max_log_bytes"`
	// MetricsEnabled 节点指标采集开关，默认 true。
	MetricsEnabled bool `yaml:"metrics_enabled"`
	// MetricsInterval 采集周期，默认 30s，运行时限制在 5s~300s。
	MetricsInterval time.Duration `yaml:"metrics_interval"`
}

// InboundConfig Agent 入站 listener YAML 配置。
type InboundConfig struct {
	ListenAddr                  string        `yaml:"listen_addr"`
	Endpoint                    string        `yaml:"endpoint"`
	CertFile                    string        `yaml:"cert_file"`
	KeyFile                     string        `yaml:"key_file"`
	CAFile                      string        `yaml:"ca_file"`
	HubClientFingerprintFile    string        `yaml:"hub_client_fingerprint_file"`
	TokenFile                   string        `yaml:"token_file"`
	AllowedSourceCIDRs          []string      `yaml:"allowed_source_cidrs"`
	MaxConnections              int           `yaml:"max_connections"`
	HandshakeTimeout            time.Duration `yaml:"handshake_timeout"`
	RateLimitPerMinute          int           `yaml:"rate_limit_per_minute"`
	GlobalRateLimitPerMinute    int           `yaml:"global_rate_limit_per_minute"`
	DataPlaneRateLimitPerMinute int           `yaml:"data_plane_rate_limit_per_minute"`
}

// DefaultConfig 默认配置
func DefaultConfig() Config {
	return Config{
		// DeploymentMode 留空：未显式配置时由 resolveDeploymentMode 自动探测（显式配置优先）
		DataDir:          "/var/lib/nodesteer",
		ConnectionMode:   "auto",
		HeartbeatSec:     30,
		RevisionCheckSec: 45,
		MaxLogBytes:      1 << 20,
		HostRoot:         "/host",
		MetricsEnabled:   true,
		MetricsInterval:  30 * time.Second,
		Inbound: InboundConfig{
			ListenAddr:       "0.0.0.0:18444",
			MaxConnections:   8,
			HandshakeTimeout: 20 * time.Second,
		},
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
	if err := applyEnv(&cfg); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

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
		ConnectionMode:    cfg.ConnectionMode,
		Inbound: connection.InboundConfig{
			ListenAddr:                  cfg.Inbound.ListenAddr,
			PublicEndpoint:              cfg.Inbound.Endpoint,
			CertFile:                    cfg.Inbound.CertFile,
			KeyFile:                     cfg.Inbound.KeyFile,
			CAFile:                      cfg.Inbound.CAFile,
			HubClientFingerprintFile:    cfg.Inbound.HubClientFingerprintFile,
			TokenFile:                   cfg.Inbound.TokenFile,
			AllowedSourceCIDRs:          cfg.Inbound.AllowedSourceCIDRs,
			MaxConnections:              cfg.Inbound.MaxConnections,
			HandshakeTimeout:            cfg.Inbound.HandshakeTimeout,
			RateLimitPerMinute:          cfg.Inbound.RateLimitPerMinute,
			GlobalRateLimitPerMinute:    cfg.Inbound.GlobalRateLimitPerMinute,
			DataPlaneRateLimitPerMinute: cfg.Inbound.DataPlaneRateLimitPerMinute,
		},
		DataDir:                      cfg.DataDir,
		HostRoot:                     cfg.HostRoot,
		HostPathAllowlist:            cfg.HostPathAllowlist,
		MonitoringAllowedCIDRs:       cfg.MonitoringAllowedCIDRs,
		AllowInsecureMonitorHub:      cfg.AllowInsecureMonitorHub,
		MonitoringSecretDestinations: cfg.MonitoringSecretDestinations,
		TLSCAFile:                    cfg.TLSCAFile,
		AgentVersion:                 cfg.AgentVersion,
		HeartbeatSec:                 cfg.HeartbeatSec,
		RevisionCheckSec:             cfg.RevisionCheckSec,
		MaxLogBytes:                  cfg.MaxLogBytes,
		MetricsEnabled:               cfg.MetricsEnabled,
		MetricsInterval:              cfg.MetricsInterval,
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

func applyEnv(cfg *Config) error {
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
	if v := os.Getenv("NODESTEER_MONITOR_ALLOWED_CIDRS"); v != "" {
		cfg.MonitoringAllowedCIDRs = strings.Split(v, ",")
	}
	if v := os.Getenv("NODESTEER_MONITOR_ALLOW_INSECURE_HUB"); v != "" {
		cfg.AllowInsecureMonitorHub, _ = strconv.ParseBool(v)
	}
	if v := os.Getenv("NODESTEER_MONITOR_SECRET_DESTINATIONS"); v != "" {
		var destinations map[string][]string
		if json.Unmarshal([]byte(v), &destinations) == nil {
			cfg.MonitoringSecretDestinations = destinations
		}
	}
	if v := os.Getenv("NODESTEER_TLS_CA_FILE"); v != "" {
		cfg.TLSCAFile = v
	}
	if v := os.Getenv("NODESTEER_AGENT_VERSION"); v != "" {
		// 旧键：保留解析兼容，上报值由编译注入版本覆盖（resolveAgentVersion）
		cfg.AgentVersion = v
	}
	if v := os.Getenv("NODESTEER_METRICS_ENABLED"); v != "" {
		if enabled, err := strconv.ParseBool(v); err == nil {
			cfg.MetricsEnabled = enabled
		}
	}
	if v := os.Getenv("NODESTEER_METRICS_INTERVAL"); v != "" {
		if interval, err := time.ParseDuration(v); err == nil {
			cfg.MetricsInterval = interval
		}
	}
	return applyInboundEnv(cfg)
}

func applyInboundEnv(cfg *Config) error {
	if v := os.Getenv("NODESTEER_CONNECTION_MODE"); v != "" {
		cfg.ConnectionMode = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_LISTEN_ADDR"); v != "" {
		cfg.Inbound.ListenAddr = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_ENDPOINT"); v != "" {
		cfg.Inbound.Endpoint = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_CERT_FILE"); v != "" {
		cfg.Inbound.CertFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_KEY_FILE"); v != "" {
		cfg.Inbound.KeyFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_CA_FILE"); v != "" {
		cfg.Inbound.CAFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_HUB_CLIENT_FINGERPRINT_FILE"); v != "" {
		cfg.Inbound.HubClientFingerprintFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_TOKEN_FILE"); v != "" {
		cfg.Inbound.TokenFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_ALLOWED_SOURCE_CIDRS"); v != "" {
		cfg.Inbound.AllowedSourceCIDRs = splitCSV(v)
	}
	if v := os.Getenv("NODESTEER_INBOUND_MAX_CONNECTIONS"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_MAX_CONNECTIONS %q: want an integer", v)
		}
		cfg.Inbound.MaxConnections = n
	}
	if v := os.Getenv("NODESTEER_INBOUND_HANDSHAKE_TIMEOUT"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_HANDSHAKE_TIMEOUT %q: %w", v, err)
		}
		cfg.Inbound.HandshakeTimeout = d
	}
	if v := os.Getenv("NODESTEER_INBOUND_RATE_LIMIT_PER_MINUTE"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_RATE_LIMIT_PER_MINUTE %q: want an integer", v)
		}
		cfg.Inbound.RateLimitPerMinute = n
	}
	if v := os.Getenv("NODESTEER_INBOUND_GLOBAL_RATE_LIMIT_PER_MINUTE"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_GLOBAL_RATE_LIMIT_PER_MINUTE %q: want an integer", v)
		}
		cfg.Inbound.GlobalRateLimitPerMinute = n
	}
	if v := os.Getenv("NODESTEER_INBOUND_DATA_PLANE_RATE_LIMIT_PER_MINUTE"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_DATA_PLANE_RATE_LIMIT_PER_MINUTE %q: want an integer", v)
		}
		cfg.Inbound.DataPlaneRateLimitPerMinute = n
	}
	return nil
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		if part = strings.TrimSpace(part); part != "" {
			out = append(out, part)
		}
	}
	return out
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
