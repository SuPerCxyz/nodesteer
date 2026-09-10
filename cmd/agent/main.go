package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/cadentra/cadentra/internal/agent"
	"gopkg.in/yaml.v3"
)

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
	AgentVersion      string   `yaml:"agent_version"`
	HeartbeatSec      int      `yaml:"heartbeat_sec"`
	RevisionCheckSec  int      `yaml:"revision_check_sec"`
	MaxLogBytes       int      `yaml:"max_log_bytes"`
}

// DefaultConfig 默认配置
func DefaultConfig() Config {
	return Config{
		DeploymentMode:   "native",
		DataDir:          "/var/lib/cadentra",
		AgentVersion:     "0.1.0",
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

	logger.Info("cadentra agent starting",
		"hub", cfg.HubURL, "mode", cfg.DeploymentMode, "data_dir", cfg.DataDir)

	if err := a.Run(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "agent run: %v\n", err)
		os.Exit(1)
	}
	logger.Info("cadentra agent stopped")
}

func applyEnv(cfg *Config) {
	if v := os.Getenv("CADENTRA_HUB_URL"); v != "" {
		cfg.HubURL = v
	}
	if v := os.Getenv("CADENTRA_REGISTRATION_TOKEN"); v != "" {
		cfg.RegistrationToken = v
	}
	if v := os.Getenv("CADENTRA_AGENT_ID"); v != "" {
		cfg.AgentID = v
	}
	if v := os.Getenv("CADENTRA_NODE_NAME"); v != "" {
		cfg.NodeName = v
	}
	if v := os.Getenv("CADENTRA_NODE_IP"); v != "" {
		cfg.NodeIP = v
	}
	if v := os.Getenv("CADENTRA_DEPLOYMENT_MODE"); v != "" {
		cfg.DeploymentMode = v
	}
	if v := os.Getenv("CADENTRA_HOST_INTEGRATION"); v == "true" {
		cfg.HostIntegration = true
	}
	if v := os.Getenv("CADENTRA_DATA_DIR"); v != "" {
		cfg.DataDir = v
	}
	if v := os.Getenv("CADENTRA_HOST_ROOT"); v != "" {
		cfg.HostRoot = v
	}
	if v := os.Getenv("CADENTRA_TLS_CA_FILE"); v != "" {
		cfg.TLSCAFile = v
	}
	if v := os.Getenv("CADENTRA_AGENT_VERSION"); v != "" {
		cfg.AgentVersion = v
	}
}
