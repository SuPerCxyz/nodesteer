package main

import (
	"context"
	"flag"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/cadentra/cadentra/internal/hub/agentbundle"
	"github.com/cadentra/cadentra/internal/hub/auth"
	"github.com/cadentra/cadentra/internal/hubserver"
	"github.com/cadentra/cadentra/web"
	"gopkg.in/yaml.v3"
)

// Config 命令行配置
type Config struct {
	WebAddr              string          `yaml:"web_addr"`
	GatewayAddr          string          `yaml:"gateway_addr"`
	GatewayBaseURL       string          `yaml:"gateway_base_url"`
	WebTLSCert           string          `yaml:"web_tls_cert"`
	WebTLSKey            string          `yaml:"web_tls_key"`
	GatewayTLSCert       string          `yaml:"gateway_tls_cert"`
	GatewayTLSKey        string          `yaml:"gateway_tls_key"`
	RegistrationToken    string          `yaml:"registration_token"`
	DataDir              string          `yaml:"data_dir"`
	ArtifactDir          string          `yaml:"artifact_dir"`
	AgentBinaryAMD64Path string          `yaml:"agent_binary_amd64_path"`
	AgentBinaryARM64Path string          `yaml:"agent_binary_arm64_path"`
	BaseURL              string          `yaml:"base_url"`
	AdminUsername        string          `yaml:"admin_username"`
	AdminPassword        string          `yaml:"admin_password"`
	SessionTTL           time.Duration   `yaml:"session_ttl"`
	RevisionCheckSec     int             `yaml:"revision_check_sec"`
	ChangelogWindow      int64           `yaml:"changelog_window"`
	HeartbeatTimeout     time.Duration   `yaml:"heartbeat_timeout"`
	MaxFileTransferBytes int64           `yaml:"max_file_transfer_bytes"`
	OIDC                 auth.OIDCConfig `yaml:"oidc"`
}

// DefaultConfig 默认配置
func DefaultConfig() Config {
	return Config{
		WebAddr:              ":8080",
		GatewayAddr:          ":8443",
		GatewayBaseURL:       "",
		DataDir:              "/var/lib/cadentra-hub",
		ArtifactDir:          "/var/lib/cadentra-hub/artifacts",
		AgentBinaryAMD64Path: "/usr/local/bin/cadentra-agent",
		BaseURL:              "http://localhost:8080",
		AdminUsername:        "admin",
		AdminPassword:        "",
		SessionTTL:           24 * time.Hour,
		RevisionCheckSec:     30,
		ChangelogWindow:      5000,
		HeartbeatTimeout:     90 * time.Second,
		MaxFileTransferBytes: 10 << 30,
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
	// 环境变量覆盖
	applyEnv(&cfg)
	if err := validateAdminCredentials(cfg); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	// OIDC 空字段回填默认值
	defOIDC := auth.DefaultOIDCConfig()
	if len(cfg.OIDC.Scopes) == 0 {
		cfg.OIDC.Scopes = defOIDC.Scopes
	}
	if cfg.OIDC.UsernameClaim == "" {
		cfg.OIDC.UsernameClaim = defOIDC.UsernameClaim
	}
	if cfg.OIDC.RoleClaim == "" {
		cfg.OIDC.RoleClaim = defOIDC.RoleClaim
	}
	if cfg.OIDC.DefaultRole == "" {
		cfg.OIDC.DefaultRole = defOIDC.DefaultRole
	}

	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	slog.SetDefault(logger)
	bundledAgentPayloads, err := agentbundle.LoadExecutable()
	if err != nil {
		logger.Warn("load bundled agent payloads failed", "error", err)
		bundledAgentPayloads = nil
	}

	os.MkdirAll(cfg.DataDir, 0o755)
	os.MkdirAll(cfg.ArtifactDir, 0o755)

	h, err := hubserver.New(hubserver.Config{
		WebAddr:              cfg.WebAddr,
		GatewayAddr:          cfg.GatewayAddr,
		GatewayBaseURL:       cfg.GatewayBaseURL,
		WebTLSCert:           cfg.WebTLSCert,
		WebTLSKey:            cfg.WebTLSKey,
		GatewayTLSCert:       cfg.GatewayTLSCert,
		GatewayTLSKey:        cfg.GatewayTLSKey,
		RegistrationToken:    cfg.RegistrationToken,
		DataDir:              cfg.DataDir,
		ArtifactDir:          cfg.ArtifactDir,
		AgentBinaryAMD64Path: cfg.AgentBinaryAMD64Path,
		AgentBinaryARM64Path: cfg.AgentBinaryARM64Path,
		AgentBinaryPayloads:  bundledAgentPayloads,
		BaseURL:              cfg.BaseURL,
		HeartbeatTimeout:     cfg.HeartbeatTimeout,
		AdminUsername:        cfg.AdminUsername,
		AdminPassword:        cfg.AdminPassword,
		SessionTTL:           cfg.SessionTTL,
		RevisionCheckSec:     cfg.RevisionCheckSec,
		ChangelogWindow:      cfg.ChangelogWindow,
		MaxFileTransferBytes: cfg.MaxFileTransferBytes,
		OIDC:                 cfg.OIDC,
	}, logger)
	if err != nil {
		fmt.Fprintf(os.Stderr, "init hub: %v\n", err)
		os.Exit(1)
	}
	defer h.Close()

	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		fmt.Fprintf(os.Stderr, "ensure admin: %v\n", err)
		os.Exit(1)
	}
	h.SetDefaults(context.Background())

	// 内嵌前端（若构建产物存在）
	if sub, err := fs.Sub(web.Dist, "dist"); err == nil {
		h.SetWebFS(sub)
	}

	if err := h.Start(); err != nil {
		fmt.Fprintf(os.Stderr, "start hub: %v\n", err)
		os.Exit(1)
	}

	logger.Info("cadentra hub started",
		"web", cfg.WebAddr, "gateway", cfg.GatewayAddr)

	// 等待退出信号
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	logger.Info("cadentra hub stopping")
}

func validateAdminCredentials(cfg Config) error {
	if cfg.AdminUsername == "" || cfg.AdminPassword == "" {
		return fmt.Errorf("admin username and password must be configured")
	}
	return nil
}

func applyEnv(cfg *Config) {
	if v := os.Getenv("CADENTRA_WEB_ADDR"); v != "" {
		cfg.WebAddr = v
	}
	if v := os.Getenv("CADENTRA_GATEWAY_ADDR"); v != "" {
		cfg.GatewayAddr = v
	}
	if v := os.Getenv("CADENTRA_GATEWAY_BASE_URL"); v != "" {
		cfg.GatewayBaseURL = v
	}
	if v := os.Getenv("CADENTRA_WEB_TLS_CERT"); v != "" {
		cfg.WebTLSCert = v
	}
	if v := os.Getenv("CADENTRA_WEB_TLS_KEY"); v != "" {
		cfg.WebTLSKey = v
	}
	if v := os.Getenv("CADENTRA_GATEWAY_TLS_CERT"); v != "" {
		cfg.GatewayTLSCert = v
	}
	if v := os.Getenv("CADENTRA_GATEWAY_TLS_KEY"); v != "" {
		cfg.GatewayTLSKey = v
	}
	if v := os.Getenv("CADENTRA_REGISTRATION_TOKEN"); v != "" {
		cfg.RegistrationToken = v
	}
	if v := os.Getenv("CADENTRA_DATA_DIR"); v != "" {
		cfg.DataDir = v
	}
	if v := os.Getenv("CADENTRA_ARTIFACT_DIR"); v != "" {
		cfg.ArtifactDir = v
	}
	if v := os.Getenv("CADENTRA_AGENT_BINARY_AMD64_PATH"); v != "" {
		cfg.AgentBinaryAMD64Path = v
	}
	if v := os.Getenv("CADENTRA_AGENT_BINARY_ARM64_PATH"); v != "" {
		cfg.AgentBinaryARM64Path = v
	}
	if v := os.Getenv("CADENTRA_BASE_URL"); v != "" {
		cfg.BaseURL = v
	}
	if v := os.Getenv("CADENTRA_MAX_FILE_TRANSFER_BYTES"); v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			cfg.MaxFileTransferBytes = n
		}
	}
	if v := os.Getenv("CADENTRA_ADMIN_USERNAME"); v != "" {
		cfg.AdminUsername = v
	}
	if v := os.Getenv("CADENTRA_ADMIN_PASSWORD"); v != "" {
		cfg.AdminPassword = v
	}
	if v := os.Getenv("CADENTRA_OIDC_ISSUER"); v != "" {
		cfg.OIDC.Issuer = v
	}
	if v := os.Getenv("CADENTRA_OIDC_CLIENT_ID"); v != "" {
		cfg.OIDC.ClientID = v
	}
	if v := os.Getenv("CADENTRA_OIDC_REDIRECT_URL"); v != "" {
		cfg.OIDC.RedirectURL = v
	}
	if v := os.Getenv("CADENTRA_OIDC_USERNAME_CLAIM"); v != "" {
		cfg.OIDC.UsernameClaim = v
	}
	if v := os.Getenv("CADENTRA_OIDC_ROLE_CLAIM"); v != "" {
		cfg.OIDC.RoleClaim = v
	}
	if v := os.Getenv("CADENTRA_OIDC_DEFAULT_ROLE"); v != "" {
		cfg.OIDC.DefaultRole = v
	}
	if v := os.Getenv("CADENTRA_OIDC_ALLOW_LOCAL_LOGIN"); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			cfg.OIDC.AllowLocalLogin = b
		}
	}
}
