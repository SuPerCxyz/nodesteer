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
	"strings"
	"syscall"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub/agentbundle"
	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/hubserver"
	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/SuPerCxyz/nodesteer/web"
	"gopkg.in/yaml.v3"
)

// Config 命令行配置
type Config struct {
	WebAddr                      string        `yaml:"web_addr"`
	GatewayAddr                  string        `yaml:"gateway_addr"`
	GatewayBaseURL               string        `yaml:"gateway_base_url"`
	WebTLSCert                   string        `yaml:"web_tls_cert"`
	WebTLSKey                    string        `yaml:"web_tls_key"`
	GatewayTLSCert               string        `yaml:"gateway_tls_cert"`
	GatewayTLSKey                string        `yaml:"gateway_tls_key"`
	RegistrationToken            string        `yaml:"registration_token"`
	DataDir                      string        `yaml:"data_dir"`
	ArtifactDir                  string        `yaml:"artifact_dir"`
	AgentBinaryAMD64Path         string        `yaml:"agent_binary_amd64_path"`
	AgentBinaryARM64Path         string        `yaml:"agent_binary_arm64_path"`
	BaseURL                      string        `yaml:"base_url"`
	AdminUsername                string        `yaml:"admin_username"`
	AdminPassword                string        `yaml:"admin_password"`
	SessionTTL                   time.Duration `yaml:"session_ttl"`
	RevisionCheckSec             int           `yaml:"revision_check_sec"`
	ChangelogWindow              int64         `yaml:"changelog_window"`
	HeartbeatTimeout             time.Duration `yaml:"heartbeat_timeout"`
	SQLiteBusyTimeout            time.Duration `yaml:"sqlite_busy_timeout"`
	NativeMetricsInterval        time.Duration `yaml:"native_metrics_interval"`
	NativeMetricsLateWindow      time.Duration `yaml:"native_metrics_late_window"`
	NativeMetricsAggregateBudget time.Duration `yaml:"native_metrics_aggregate_budget"`
	MaxFileTransferBytes         int64         `yaml:"max_file_transfer_bytes"`
	MonitoringAllowedCIDRs       []string      `yaml:"monitoring_allowed_cidrs"`
	MonitoringWorkers            int           `yaml:"monitoring_workers"`
	// 入站连接（Hub 主动拨号）配置；默认关闭。
	InboundEnabled         bool            `yaml:"inbound_enabled"`
	InboundCAFile          string          `yaml:"inbound_ca_file"`
	InboundCAKeyFile       string          `yaml:"inbound_ca_key_file"`
	InboundClientCertFile  string          `yaml:"inbound_client_cert_file"`
	InboundClientKeyFile   string          `yaml:"inbound_client_key_file"`
	InboundDialConcurrency int             `yaml:"inbound_dial_concurrency"`
	InboundDialTimeout     time.Duration   `yaml:"inbound_dial_timeout"`
	InboundBackoffInitial  time.Duration   `yaml:"inbound_backoff_initial"`
	InboundBackoffMax      time.Duration   `yaml:"inbound_backoff_max"`
	InboundAllowedPorts    []int           `yaml:"inbound_allowed_ports"`
	InboundDeniedCIDRs     []string        `yaml:"inbound_denied_cidrs"`
	InboundAllowLoopback   bool            `yaml:"inbound_allow_loopback"`
	DevNoAuth              bool            `yaml:"dev_no_auth"`
	AllowEmptyPassword     bool            `yaml:"allow_empty_password"` // 仅测试环境使用：允许空管理员密码，默认 false
	OIDC                   auth.OIDCConfig `yaml:"oidc"`
}

// DefaultConfig 默认配置
func DefaultConfig() Config {
	return Config{
		WebAddr:                      ":8080",
		GatewayAddr:                  ":8443",
		GatewayBaseURL:               "",
		DataDir:                      "/var/lib/nodesteer-hub",
		ArtifactDir:                  "/var/lib/nodesteer-hub/artifacts",
		AgentBinaryAMD64Path:         "/usr/local/bin/nodesteer-agent",
		BaseURL:                      "http://localhost:8080",
		AdminUsername:                "admin",
		AdminPassword:                "",
		SessionTTL:                   24 * time.Hour,
		RevisionCheckSec:             30,
		ChangelogWindow:              5000,
		HeartbeatTimeout:             90 * time.Second,
		SQLiteBusyTimeout:            5 * time.Second,
		NativeMetricsInterval:        5 * time.Minute,
		NativeMetricsLateWindow:      15 * time.Minute,
		NativeMetricsAggregateBudget: 10 * time.Second,
		MaxFileTransferBytes:         10 << 30,
		// 入站连接默认关闭；只允许默认端口 18444，保留 RFC1918。
		InboundEnabled:         false,
		InboundDialConcurrency: 16,
		InboundDialTimeout:     20 * time.Second,
		InboundBackoffInitial:  2 * time.Second,
		InboundBackoffMax:      60 * time.Second,
		InboundAllowedPorts:    []int{18444},
	}
}

func main() {
	var configPath string
	flag.StringVar(&configPath, "config", "", "config file path (yaml)")
	backfillFrom := flag.String("backfill-native-from", "", "one-shot native metric backfill start (RFC3339)")
	backfillTo := flag.String("backfill-native-to", "", "one-shot native metric backfill end (RFC3339, default now)")
	backfillChunk := flag.Duration("backfill-native-chunk", 15*time.Minute, "backfill transaction chunk")
	backfillPause := flag.Duration("backfill-native-pause", 10*time.Millisecond, "pause between backfill chunks")
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
	// 环境变量覆盖（三态：未设置不覆盖 yaml/默认值；设置则解析）
	if err := applyEnv(&cfg); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if *backfillFrom != "" {
		if err := runNativeBackfill(cfg, *backfillFrom, *backfillTo, *backfillChunk, *backfillPause); err != nil {
			fmt.Fprintf(os.Stderr, "native backfill: %v\n", err)
			os.Exit(1)
		}
		return
	}
	if err := validateAdminCredentials(cfg); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := validateInboundConfig(cfg); err != nil {
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
	if cfg.DevNoAuth {
		logger.Warn("DEV NO AUTH ENABLED: all API requests are treated as administrator; use only for local testing")
	}
	if cfg.AllowEmptyPassword {
		logger.Warn("ALLOW EMPTY PASSWORD ENABLED: local accounts may sign in with an empty password; use only for testing")
	}
	bundledAgentPayloads, err := agentbundle.LoadExecutable()
	if err != nil {
		logger.Warn("load bundled agent payloads failed", "error", err)
		bundledAgentPayloads = nil
	}

	os.MkdirAll(cfg.DataDir, 0o755)
	os.MkdirAll(cfg.ArtifactDir, 0o755)

	h, err := hubserver.New(hubserver.Config{
		WebAddr:                      cfg.WebAddr,
		GatewayAddr:                  cfg.GatewayAddr,
		GatewayBaseURL:               cfg.GatewayBaseURL,
		WebTLSCert:                   cfg.WebTLSCert,
		WebTLSKey:                    cfg.WebTLSKey,
		GatewayTLSCert:               cfg.GatewayTLSCert,
		GatewayTLSKey:                cfg.GatewayTLSKey,
		RegistrationToken:            cfg.RegistrationToken,
		DataDir:                      cfg.DataDir,
		ArtifactDir:                  cfg.ArtifactDir,
		AgentBinaryAMD64Path:         cfg.AgentBinaryAMD64Path,
		AgentBinaryARM64Path:         cfg.AgentBinaryARM64Path,
		AgentBinaryPayloads:          bundledAgentPayloads,
		BaseURL:                      cfg.BaseURL,
		HeartbeatTimeout:             cfg.HeartbeatTimeout,
		AdminUsername:                cfg.AdminUsername,
		AdminPassword:                cfg.AdminPassword,
		SessionTTL:                   cfg.SessionTTL,
		RevisionCheckSec:             cfg.RevisionCheckSec,
		ChangelogWindow:              cfg.ChangelogWindow,
		MaxFileTransferBytes:         cfg.MaxFileTransferBytes,
		SQLiteBusyTimeout:            cfg.SQLiteBusyTimeout,
		NativeMetricsInterval:        cfg.NativeMetricsInterval,
		NativeMetricsLateWindow:      cfg.NativeMetricsLateWindow,
		NativeMetricsAggregateBudget: cfg.NativeMetricsAggregateBudget,
		MonitoringAllowedCIDRs:       cfg.MonitoringAllowedCIDRs,
		MonitoringWorkers:            cfg.MonitoringWorkers,
		InboundEnabled:               cfg.InboundEnabled,
		InboundCAFile:                cfg.InboundCAFile,
		InboundCAKeyFile:             cfg.InboundCAKeyFile,
		InboundClientCertFile:        cfg.InboundClientCertFile,
		InboundClientKeyFile:         cfg.InboundClientKeyFile,
		InboundDialConcurrency:       cfg.InboundDialConcurrency,
		InboundDialTimeout:           cfg.InboundDialTimeout,
		InboundBackoffInitial:        cfg.InboundBackoffInitial,
		InboundBackoffMax:            cfg.InboundBackoffMax,
		InboundAllowedPorts:          cfg.InboundAllowedPorts,
		InboundDeniedCIDRs:           cfg.InboundDeniedCIDRs,
		InboundAllowLoopback:         cfg.InboundAllowLoopback,
		DevNoAuth:                    cfg.DevNoAuth,
		AllowEmptyPassword:           cfg.AllowEmptyPassword,
		OIDC:                         cfg.OIDC,
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

	logger.Info("nodesteer hub started",
		"web", cfg.WebAddr, "gateway", cfg.GatewayAddr)

	// 等待退出信号
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	logger.Info("nodesteer hub stopping")
}

func runNativeBackfill(cfg Config, fromRaw, toRaw string, chunk, pause time.Duration) error {
	from, err := parseBackfillTime(fromRaw)
	if err != nil {
		return fmt.Errorf("invalid --backfill-native-from: %w", err)
	}
	to := time.Now().UTC()
	if toRaw != "" {
		to, err = parseBackfillTime(toRaw)
		if err != nil {
			return fmt.Errorf("invalid --backfill-native-to: %w", err)
		}
	}
	if !from.Before(to) {
		return fmt.Errorf("backfill start must be before end")
	}
	if err := os.MkdirAll(cfg.DataDir, 0o755); err != nil {
		return err
	}
	st, err := store.OpenSQLiteWithOptions(cfg.DataDir+"/hub.db", store.SQLiteOptions{BusyTimeout: cfg.SQLiteBusyTimeout})
	if err != nil {
		return err
	}
	defer st.Close()
	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	logger.Info("native backfill starting", "from", from, "to", to, "chunk", chunk, "pause", pause)
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	err = st.BackfillNativeMetrics(ctx, from, to, store.NativeBackfillOptions{
		Chunk: chunk,
		Pause: pause,
		Progress: func(tier string, done, total int) {
			logger.Info("native backfill progress", "tier", tier, "series_done", done, "series_total", total)
		},
	})
	if err != nil {
		return err
	}
	stats := st.LastNativeBackfillStats()
	logger.Info("native backfill complete", "from", from, "to", to,
		"duration", stats.Duration, "series", stats.Series,
		"transactions", stats.Transactions, "busy_errors", stats.BusyErrors)
	return nil
}

func parseBackfillTime(value string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339, "2006-01-02T15:04:05", "2006-01-02 15:04:05"} {
		if parsed, err := time.Parse(layout, value); err == nil {
			return parsed.UTC(), nil
		}
	}
	return time.Time{}, fmt.Errorf("unrecognized time %q", value)
}

func validateAdminCredentials(cfg Config) error {
	if cfg.AllowEmptyPassword {
		if cfg.AdminUsername == "" {
			return fmt.Errorf("admin username must be configured")
		}
		return nil
	}
	if cfg.AdminUsername == "" || cfg.AdminPassword == "" {
		return fmt.Errorf("admin username and password must be configured")
	}
	return nil
}

func applyEnv(cfg *Config) error {
	if v := os.Getenv("NODESTEER_WEB_ADDR"); v != "" {
		cfg.WebAddr = v
	}
	if v := os.Getenv("NODESTEER_GATEWAY_ADDR"); v != "" {
		cfg.GatewayAddr = v
	}
	if v := os.Getenv("NODESTEER_GATEWAY_BASE_URL"); v != "" {
		cfg.GatewayBaseURL = v
	}
	if v := os.Getenv("NODESTEER_SQLITE_BUSY_TIMEOUT"); v != "" {
		if timeout, err := time.ParseDuration(v); err == nil && timeout > 0 {
			cfg.SQLiteBusyTimeout = timeout
		}
	}
	if v := os.Getenv("NODESTEER_NATIVE_METRICS_INTERVAL"); v != "" {
		if interval, err := time.ParseDuration(v); err == nil && interval > 0 {
			cfg.NativeMetricsInterval = interval
		}
	}
	if v := os.Getenv("NODESTEER_NATIVE_METRICS_LATE_WINDOW"); v != "" {
		if window, err := time.ParseDuration(v); err == nil && window > 0 {
			cfg.NativeMetricsLateWindow = window
		}
	}
	if v := os.Getenv("NODESTEER_NATIVE_METRICS_AGGREGATE_BUDGET"); v != "" {
		if budget, err := time.ParseDuration(v); err == nil && budget > 0 {
			cfg.NativeMetricsAggregateBudget = budget
		}
	}
	if v := os.Getenv("NODESTEER_WEB_TLS_CERT"); v != "" {
		cfg.WebTLSCert = v
	}
	if v := os.Getenv("NODESTEER_WEB_TLS_KEY"); v != "" {
		cfg.WebTLSKey = v
	}
	if v := os.Getenv("NODESTEER_GATEWAY_TLS_CERT"); v != "" {
		cfg.GatewayTLSCert = v
	}
	if v := os.Getenv("NODESTEER_GATEWAY_TLS_KEY"); v != "" {
		cfg.GatewayTLSKey = v
	}
	if v := os.Getenv("NODESTEER_REGISTRATION_TOKEN"); v != "" {
		cfg.RegistrationToken = v
	}
	if v := os.Getenv("NODESTEER_DATA_DIR"); v != "" {
		cfg.DataDir = v
	}
	if v := os.Getenv("NODESTEER_MONITOR_ALLOWED_CIDRS"); v != "" {
		cfg.MonitoringAllowedCIDRs = strings.Split(v, ",")
	}
	if v := os.Getenv("NODESTEER_MONITOR_WORKERS"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil || n < 1 || n > 128 {
			return fmt.Errorf("NODESTEER_MONITOR_WORKERS must be between 1 and 128")
		}
		cfg.MonitoringWorkers = n
	}
	if v := os.Getenv("NODESTEER_ARTIFACT_DIR"); v != "" {
		cfg.ArtifactDir = v
	}
	if v := os.Getenv("NODESTEER_AGENT_BINARY_AMD64_PATH"); v != "" {
		cfg.AgentBinaryAMD64Path = v
	}
	if v := os.Getenv("NODESTEER_AGENT_BINARY_ARM64_PATH"); v != "" {
		cfg.AgentBinaryARM64Path = v
	}
	if v := os.Getenv("NODESTEER_BASE_URL"); v != "" {
		cfg.BaseURL = v
	}
	if v := os.Getenv("NODESTEER_MAX_FILE_TRANSFER_BYTES"); v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			cfg.MaxFileTransferBytes = n
		}
	}
	if v := os.Getenv("NODESTEER_ADMIN_USERNAME"); v != "" {
		cfg.AdminUsername = v
	}
	if v := os.Getenv("NODESTEER_ADMIN_PASSWORD"); v != "" {
		cfg.AdminPassword = v
	}
	if v, ok := os.LookupEnv("NODESTEER_DEV_NO_AUTH"); ok && v != "" {
		b, err := strconv.ParseBool(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_DEV_NO_AUTH %q: want true or false", v)
		}
		cfg.DevNoAuth = b
	}
	if v, ok := os.LookupEnv("NODESTEER_ALLOW_EMPTY_PASSWORD"); ok && v != "" {
		b, err := strconv.ParseBool(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_ALLOW_EMPTY_PASSWORD %q: want true or false", v)
		}
		cfg.AllowEmptyPassword = b
	}
	if v := os.Getenv("NODESTEER_OIDC_ISSUER"); v != "" {
		cfg.OIDC.Issuer = v
	}
	if v := os.Getenv("NODESTEER_OIDC_CLIENT_ID"); v != "" {
		cfg.OIDC.ClientID = v
	}
	if v := os.Getenv("NODESTEER_OIDC_REDIRECT_URL"); v != "" {
		cfg.OIDC.RedirectURL = v
	}
	if v := os.Getenv("NODESTEER_OIDC_USERNAME_CLAIM"); v != "" {
		cfg.OIDC.UsernameClaim = v
	}
	if v := os.Getenv("NODESTEER_OIDC_ROLE_CLAIM"); v != "" {
		cfg.OIDC.RoleClaim = v
	}
	if v := os.Getenv("NODESTEER_OIDC_DEFAULT_ROLE"); v != "" {
		cfg.OIDC.DefaultRole = v
	}
	// allow_local_login 三态：环境变量未设置时保留 yaml/默认值（默认 true=本地模式）；
	// 设置则必须是合法 bool —— 解析失败直接报错，避免安全开关被静默忽略。
	if v, ok := os.LookupEnv("NODESTEER_OIDC_ALLOW_LOCAL_LOGIN"); ok && v != "" {
		b, err := strconv.ParseBool(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_OIDC_ALLOW_LOCAL_LOGIN %q: want true or false", v)
		}
		cfg.OIDC.AllowLocalLogin = &b
	}
	if err := applyInboundEnv(cfg); err != nil {
		return err
	}
	return nil
}

func applyInboundEnv(cfg *Config) error {
	if v, ok := os.LookupEnv("NODESTEER_INBOUND_ENABLED"); ok && v != "" {
		b, err := strconv.ParseBool(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_ENABLED %q: want true or false", v)
		}
		cfg.InboundEnabled = b
	}
	if v := os.Getenv("NODESTEER_INBOUND_CA_FILE"); v != "" {
		cfg.InboundCAFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_CA_KEY_FILE"); v != "" {
		cfg.InboundCAKeyFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_CLIENT_CERT_FILE"); v != "" {
		cfg.InboundClientCertFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_CLIENT_KEY_FILE"); v != "" {
		cfg.InboundClientKeyFile = v
	}
	if v := os.Getenv("NODESTEER_INBOUND_DIAL_CONCURRENCY"); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_DIAL_CONCURRENCY %q: want an integer", v)
		}
		cfg.InboundDialConcurrency = n
	}
	if v := os.Getenv("NODESTEER_INBOUND_DIAL_TIMEOUT"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_DIAL_TIMEOUT %q: %w", v, err)
		}
		cfg.InboundDialTimeout = d
	}
	if v := os.Getenv("NODESTEER_INBOUND_BACKOFF_INITIAL"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_BACKOFF_INITIAL %q: %w", v, err)
		}
		cfg.InboundBackoffInitial = d
	}
	if v := os.Getenv("NODESTEER_INBOUND_BACKOFF_MAX"); v != "" {
		d, err := time.ParseDuration(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_BACKOFF_MAX %q: %w", v, err)
		}
		cfg.InboundBackoffMax = d
	}
	if v := os.Getenv("NODESTEER_INBOUND_ALLOWED_PORTS"); v != "" {
		ports, err := parsePortList(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_ALLOWED_PORTS: %w", err)
		}
		cfg.InboundAllowedPorts = ports
	}
	if v := os.Getenv("NODESTEER_INBOUND_DENIED_CIDRS"); v != "" {
		cfg.InboundDeniedCIDRs = splitCSV(v)
	}
	if v, ok := os.LookupEnv("NODESTEER_INBOUND_ALLOW_LOOPBACK"); ok && v != "" {
		b, err := strconv.ParseBool(v)
		if err != nil {
			return fmt.Errorf("invalid NODESTEER_INBOUND_ALLOW_LOOPBACK %q: want true or false", v)
		}
		cfg.InboundAllowLoopback = b
	}
	return nil
}

func parsePortList(value string) ([]int, error) {
	parts := splitCSV(value)
	if len(parts) == 0 {
		return nil, fmt.Errorf("at least one port is required")
	}
	ports := make([]int, 0, len(parts))
	for _, part := range parts {
		port, err := strconv.Atoi(part)
		if err != nil {
			return nil, fmt.Errorf("%q is not an integer", part)
		}
		ports = append(ports, port)
	}
	return ports, nil
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}

// validateInboundConfig 校验入站开关、PKI 路径成对性、端口/CIDR 与拨号参数。
// 默认关闭时也校验显式配置，避免安全开关或策略被静默忽略。
func validateInboundConfig(cfg Config) error {
	if (cfg.InboundCAFile == "") != (cfg.InboundCAKeyFile == "") {
		return fmt.Errorf("inbound_ca_file and inbound_ca_key_file must be configured together")
	}
	if (cfg.InboundClientCertFile == "") != (cfg.InboundClientKeyFile == "") {
		return fmt.Errorf("inbound_client_cert_file and inbound_client_key_file must be configured together")
	}
	if _, err := inbound.NewDialPolicy(cfg.InboundAllowedPorts, cfg.InboundDeniedCIDRs, cfg.InboundAllowLoopback); err != nil {
		return err
	}
	if !cfg.InboundEnabled {
		return nil
	}
	if cfg.InboundDialConcurrency < 1 || cfg.InboundDialConcurrency > 256 {
		return fmt.Errorf("inbound_dial_concurrency must be between 1 and 256")
	}
	if cfg.InboundDialTimeout <= 0 {
		return fmt.Errorf("inbound_dial_timeout must be positive")
	}
	if cfg.InboundBackoffInitial <= 0 || cfg.InboundBackoffMax < cfg.InboundBackoffInitial {
		return fmt.Errorf("inbound backoff must satisfy 0 < initial <= max")
	}
	return nil
}
