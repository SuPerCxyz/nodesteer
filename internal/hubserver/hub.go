package hubserver

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"golang.org/x/sys/unix"
	"io/fs"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/hub/api"
	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/metrics"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// Config Hub 配置
type Config struct {
	WebAddr              string
	GatewayAddr          string
	GatewayBaseURL       string
	WebTLSCert           string
	WebTLSKey            string
	GatewayTLSCert       string
	GatewayTLSKey        string
	RegistrationToken    string
	DataDir              string
	ArtifactDir          string
	AgentBinaryAMD64Path string
	AgentBinaryARM64Path string
	AgentBinaryPayloads  map[string][]byte
	BaseURL              string
	HeartbeatTimeout     time.Duration
	AdminUsername        string
	AdminPassword        string
	SessionTTL           time.Duration
	RevisionCheckSec     int
	ChangelogWindow      int64
	MaxFileTransferBytes int64
	// SQLiteBusyTimeout 是 Hub SQLite busy_timeout；零值默认 5s。
	SQLiteBusyTimeout time.Duration
	// NativeMetricsInterval 是原生指标聚合/清理周期；零值默认 5m。
	NativeMetricsInterval time.Duration
	// NativeMetricsLateWindow 是迟到重算窗口；零值默认 15m。
	NativeMetricsLateWindow time.Duration
	// NativeMetricsAggregateBudget 是每轮聚合时间预算；零值默认 10s。
	NativeMetricsAggregateBudget time.Duration
	MonitoringAllowedCIDRs       []string
	MonitoringWorkers            int
	// 入站连接（Hub 主动拨号）配置。默认关闭；启用后未提供 CA/客户端证书路径
	// 时在 DataDir/inbound 下自动生成（私钥 0600）。
	InboundEnabled         bool
	InboundCAFile          string
	InboundCAKeyFile       string
	InboundClientCertFile  string
	InboundClientKeyFile   string
	InboundDialConcurrency int
	InboundDialTimeout     time.Duration
	InboundBackoffInitial  time.Duration
	InboundBackoffMax      time.Duration
	InboundAllowedPorts    []int
	InboundDeniedCIDRs     []string
	InboundAllowLoopback   bool
	// DevNoAuth 仅本地测试使用：所有 API 请求按管理员放行。默认 false。
	DevNoAuth bool
	// AllowEmptyPassword 仅测试环境使用：允许本地账号以空密码登录。默认 false。
	AllowEmptyPassword bool
	OIDC               auth.OIDCConfig
}

// Hub 主结构
type Hub struct {
	cfg              Config
	store            store.Store
	auth             *auth.Manager
	revisions        *hub.RevisionManager
	sessions         *hub.SessionManager
	nodes            *hub.NodeManager
	scripts          *hub.ScriptManager
	tasks            *hub.TaskManager
	schedules        *hub.ScheduleManager
	syncMgr          *hub.SyncManager
	execMgr          *hub.ExecutionManager
	artifacts        *hub.ArtifactManager
	apps             *hub.AppManager
	transfers        *hub.FileTransferManager
	gateway          *hub.Gateway
	inboundDialer    *hub.InboundDialer
	api              *api.Server
	metrics          *metrics.Metrics
	inboundPolicy    *inbound.DialPolicy
	inboundAuthority *inbound.Authority
	inboundEnabled   bool
	monitoring       *monitoring.Scheduler
	alertEvaluator   *monitoring.AlertEvaluator
	monitorLock      *os.File
	monitorDone      chan struct{}
	metricDone       chan struct{}
	alertDone        chan struct{}
	logger           *slog.Logger
	webFS            fs.FS
	// singlePort 单端口模式：GatewayAddr == WebAddr（零新配置项的触发条件）。
	// 此模式下不起独立 Gateway listener，由 Web 最外层 middleware 分流。
	singlePort bool
	ctx        context.Context
	cancel     context.CancelFunc
}

// New 组装 Hub
func New(cfg Config, logger *slog.Logger) (*Hub, error) {
	monitorNetwork, err := monitoring.NewNetworkPolicy(cfg.MonitoringAllowedCIDRs)
	if err != nil {
		return nil, err
	}
	monitorTransport := monitorNetwork.Transport()
	if (cfg.WebTLSCert == "") != (cfg.WebTLSKey == "") {
		return nil, fmt.Errorf("web_tls_cert and web_tls_key must be configured together")
	}
	if (cfg.GatewayTLSCert == "") != (cfg.GatewayTLSKey == "") {
		return nil, fmt.Errorf("gateway_tls_cert and gateway_tls_key must be configured together")
	}
	inboundRuntime, err := prepareInbound(cfg, logger)
	if err != nil {
		return nil, err
	}
	singlePort := cfg.GatewayAddr == cfg.WebAddr
	if singlePort && cfg.GatewayTLSCert != "" {
		// 单端口模式下 HTTPS 统一在反代终结，仅告警防误配，不新增其他 TLS 逻辑。
		logger.Warn("single-port gateway mode: gateway TLS config is unused, terminate HTTPS at the reverse proxy",
			"gateway_tls_cert", cfg.GatewayTLSCert, "addr", cfg.WebAddr)
	}
	st, err := store.OpenSQLiteWithOptions(cfg.DataDir+"/hub.db", store.SQLiteOptions{
		BusyTimeout: cfg.SQLiteBusyTimeout,
	})
	if err != nil {
		return nil, err
	}

	authMgr := auth.New(st, cfg.SessionTTL)
	authMgr.SetAllowEmptyPassword(cfg.AllowEmptyPassword)
	allowLocal := cfg.OIDC.LocalLoginAllowed()
	authMgr.SetLocalLoginAllowed(allowLocal)
	if allowLocal {
		// 本地模式（默认）：本地密码登录可用，OIDC 完全失效——
		// 不做 discovery、不构造 OIDC 客户端，启动永不因 OIDC 失败。
		logger.Info("local login mode (allow_local_login=true), oidc disabled",
			"oidc_issuer_configured", cfg.OIDC.Issuer != "")
	} else {
		// SSO-only 模式：SSO 是唯一登录方式，密码登录一律 403；
		// 配置缺失或 discovery 失败必须 fail-fast 拒启（恢复=切回 true 重启）。
		if cfg.OIDC.Issuer == "" {
			_ = st.Close()
			return nil, fmt.Errorf("allow_local_login=false requires oidc.issuer to be configured")
		}
		oidcClient, err := auth.NewOIDC(context.Background(), cfg.OIDC, cfg.BaseURL)
		if err != nil {
			_ = st.Close()
			return nil, fmt.Errorf("init oidc: %w", err)
		}
		authMgr.SetOIDC(oidcClient)
		logger.Info("oidc enabled (allow_local_login=false, sso-only mode)", "issuer", cfg.OIDC.Issuer)
	}
	revisions := hub.NewRevisionManager(st)
	sessions := hub.NewSessionManager()
	nodes := hub.NewNodeManager(st, revisions)
	nodes.SetSessionManager(sessions)
	syncMgr := hub.NewSyncManager(st, revisions, sessions, nodes)
	nodes.SetSyncManager(syncMgr)
	syncMgr.SetBaseURL(cfg.BaseURL)
	syncMgr.SetChangelogWindow(cfg.ChangelogWindow)
	m := metrics.New()
	execMgr := hub.NewExecutionManager(st, sessions, nodes, logger)
	execMgr.SetMetrics(m)
	execMgr.SetAgentBinaryProvider(func(arch string) ([]byte, string, error) {
		return loadAgentBinaryForArch(cfg, arch)
	})
	scripts := hub.NewScriptManager(st, revisions, syncMgr)
	tasks := hub.NewTaskManager(st, revisions, syncMgr)

	artifacts, err := hub.NewArtifactManager(st, cfg.ArtifactDir, sessions, syncMgr)
	if err != nil {
		return nil, err
	}
	execMgr.SetArtifactManager(artifacts)
	apps := hub.NewAppManager(st, revisions, sessions, artifacts, execMgr, cfg.BaseURL)
	apps.SetSyncMgr(syncMgr)
	schedules := hub.NewScheduleManager(st, revisions, syncMgr, nodes, execMgr)
	gatewayBaseURL := hub.ResolveGatewayBaseURL(cfg.GatewayBaseURL, cfg.BaseURL, cfg.GatewayAddr)
	transfers, err := hub.NewFileTransferManager(st, cfg.DataDir, gatewayBaseURL, sessions, nodes, logger)
	if err != nil {
		return nil, err
	}
	transfers.SetMaxBytes(cfg.MaxFileTransferBytes)

	gateway := hub.NewGateway(hub.GatewayConfig{
		RegistrationToken: cfg.RegistrationToken,
		HeartbeatTimeout:  cfg.HeartbeatTimeout,
	}, nodes, sessions, syncMgr, execMgr, logger)
	gateway.SetFileTransferManager(transfers)
	gateway.SetTransferHandler(http.HandlerFunc(transfers.HandleAgentHTTP))
	gateway.SetOnAgentConn(func(nodeID string) { transfers.ResumeNode(context.Background(), nodeID) })

	var inboundDialer *hub.InboundDialer
	if inboundRuntime.enabled && inboundRuntime.authority != nil {
		inboundDialer = hub.NewInboundDialer(hub.InboundDialerConfig{
			Concurrency:      cfg.InboundDialConcurrency,
			HandshakeTimeout: cfg.InboundDialTimeout,
			BackoffInitial:   cfg.InboundBackoffInitial,
			BackoffMax:       cfg.InboundBackoffMax,
			Authority:        inboundRuntime.authority,
			Policy:           inboundRuntime.policy,
			Metrics:          m,
		}, gateway, logger)
	}

	apiServer := api.New(st, authMgr, nodes, scripts, tasks, schedules, artifacts, apps, execMgr, logger)
	apiServer.SetDevNoAuth(cfg.DevNoAuth)
	apiServer.SetFileTransfers(transfers)
	if inboundDialer != nil {
		apiServer.SetInboundDialer(inboundDialer)
	}
	if inboundRuntime.enabled && inboundRuntime.authority != nil {
		apiServer.SetInboundBundleService(hub.NewInboundBundleService(
			inboundRuntime.authority, nodes, sessions, cfg.RegistrationToken, gatewayBaseURL, cfg.BaseURL, logger))
	}
	apiServer.SetEnrollmentConfig(cfg.RegistrationToken, gatewayBaseURL)
	apiServer.SetInboundConfig(inboundRuntime.enabled, inboundRuntime.policy)
	apiServer.SetBaseURL(cfg.BaseURL)
	apiServer.SetAgentBinaryPaths(cfg.AgentBinaryAMD64Path, cfg.AgentBinaryARM64Path)
	apiServer.SetAgentBinaryPayloads(cfg.AgentBinaryPayloads)
	apiServer.SetMetrics(m)
	apiServer.SetRegistrationToken(cfg.RegistrationToken)
	apiServer.SetSessions(sessions)
	monitorRepo := monitoring.NewRepository(st.DB())
	apiServer.SetMonitoring(monitorRepo)
	monitorScheduler := monitoring.NewScheduler(monitorRepo, monitorTransport, logger)
	if cfg.MonitoringWorkers != 0 {
		if err := monitorScheduler.SetWorkers(cfg.MonitoringWorkers); err != nil {
			_ = st.Close()
			return nil, err
		}
	}
	monitorScheduler.SetNodeMetricProbe(func(ctx context.Context, target monitoring.Target) (monitoring.HTTPObservation, error) {
		return observeNativeNodeMetric(ctx, nodes, st, target, time.Now().UTC())
	})
	monitorScheduler.SetNetwork(monitorNetwork)
	monitorProbes := hub.NewMonitorProbes(sessions, nodes, cfg.MonitoringAllowedCIDRs)
	gateway.SetMonitorProbes(monitorProbes)
	monitorScheduler.SetRemoteProbe(monitorProbes.Probe)
	monitorScheduler.SetTrigger(func(ctx context.Context, rule monitoring.Rule, claim string) error {
		return runMonitoringRule(ctx, rule, claim, st, nodes, tasks, schedules, execMgr)
	})
	// 阈值告警：规则评估与通知发件箱相互独立，通知失败不阻塞评估。
	alertRepo := monitoring.NewAlertRepository(st.DB())
	alertNotifier := monitoring.NewAlertNotifier(alertRepo, logger)
	alertEvaluator := monitoring.NewAlertEvaluator(alertRepo, st, alertNotifier, logger)
	alertEvaluator.SetInterval(alertEvaluationInterval)
	apiServer.SetAlerting(alertRepo, alertNotifier)
	apiServer.SetReadyCheck(func(ctx context.Context) error {
		_, err := st.CurrentGlobalRevision(ctx)
		return err
	})
	if oidcClient := authMgr.OIDC(); oidcClient != nil {
		apiServer.SetOIDC(oidcClient)
	}

	ctx, cancel := context.WithCancel(context.Background())

	return &Hub{
		cfg: cfg, store: st, auth: authMgr, revisions: revisions,
		sessions: sessions, nodes: nodes, scripts: scripts, tasks: tasks,
		schedules: schedules, syncMgr: syncMgr, execMgr: execMgr,
		artifacts: artifacts, apps: apps, transfers: transfers, gateway: gateway, api: apiServer,
		inboundDialer: inboundDialer,
		metrics:       m, monitoring: monitorScheduler, alertEvaluator: alertEvaluator,
		inboundEnabled: inboundRuntime.enabled, inboundPolicy: inboundRuntime.policy,
		inboundAuthority: inboundRuntime.authority,
		logger:           logger, singlePort: singlePort, ctx: ctx, cancel: cancel,
	}, nil
}

var errMonitoringStepFailed = errors.New("monitoring execution did not succeed")

type monitoringStepFailure struct{ status string }

func (e *monitoringStepFailure) Error() string {
	return "monitoring execution did not succeed: " + e.status
}

func (e *monitoringStepFailure) Unwrap() error { return errMonitoringStepFailed }

// waitMonitoringExecution prevents a later node from starting until the
// previous node reports success. An unknown or non-success state stops it.
func waitMonitoringExecution(ctx context.Context, st store.Store, id string) error {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		execution, err := st.GetExecution(ctx, id)
		if err != nil {
			return err
		}
		switch execution.Status {
		case models.ExecStatusSuccess:
			return nil
		case models.ExecStatusFailed, models.ExecStatusSkipped, models.ExecStatusCanceled, models.ExecStatusTimedOut, models.ExecStatusBlocked:
			return &monitoringStepFailure{status: execution.Status}
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

// SetWebFS 设置内嵌前端
func (h *Hub) SetWebFS(fsys fs.FS) {
	h.webFS = fsys
}

// webMux 构造 Web/API 侧路由（注入内嵌前端时含 SPA 回退）。
// 真实启动路径与 ServeMux 测试路径共用此构造。
func (h *Hub) webMux() http.Handler {
	if h.webFS == nil {
		return h.api.Routes()
	}
	fsHandler := newStaticHandler(h.webFS)
	mux := http.NewServeMux()
	// API 由 api.Server.Routes 处理，前端静态文件用独立 mux 包装
	root := h.api.Routes()
	mux.Handle("/api/", root)
	mux.Handle("/api", root)
	mux.Handle("/healthz", root)
	mux.Handle("/readyz", root)
	mux.Handle("/metrics", root)
	mux.Handle("/", fsHandler)
	return mux
}

// webHandler 返回 Web/API 最外层 handler。
// 单端口模式（GatewayAddr == WebAddr）下在最外层分流：
// Upgrade=websocket 或 /agent/transfers/* 前缀 → Gateway handler
// （其已同时具备 WS 任意路径 Accept 与 transfers 分流），其余 → Web 路由。
// 双端口模式原样返回，行为与既有实现完全一致。
// 启动路径与 ServeMux 共用本方法，保证 httptest 测试与真实监听拿到同一层包装。
func (h *Hub) webHandler() http.Handler {
	next := h.webMux()
	if !h.singlePort {
		return next
	}
	gw := h.gateway.Handler()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Upgrade") == "websocket" || strings.HasPrefix(r.URL.Path, "/agent/transfers/") {
			gw.ServeHTTP(w, r)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// ServeMux 返回合并后的路由（含前端；单端口模式含 Gateway 分流层）
func (h *Hub) ServeMux() http.Handler {
	return h.webHandler()
}

// SetStore 测试用替换 Store
func (h *Hub) SetStore(st store.Store) { h.store = st }

// Store 暴露 Store
func (h *Hub) Store() store.Store { return h.store }

// Revisions 暴露 RevisionManager
func (h *Hub) Revisions() *hub.RevisionManager { return h.revisions }

// Nodes 暴露 NodeManager
func (h *Hub) Nodes() *hub.NodeManager { return h.nodes }

// Sessions 暴露 SessionManager
func (h *Hub) Sessions() *hub.SessionManager { return h.sessions }

// ExecMgr 暴露 ExecutionManager
func (h *Hub) ExecMgr() *hub.ExecutionManager { return h.execMgr }

// SyncMgr 暴露 SyncManager
func (h *Hub) SyncMgr() *hub.SyncManager { return h.syncMgr }

// Schedules 暴露 ScheduleManager
func (h *Hub) Schedules() *hub.ScheduleManager { return h.schedules }

// Apps 暴露 AppManager
func (h *Hub) Apps() *hub.AppManager { return h.apps }

// Artifacts 暴露 ArtifactManager
func (h *Hub) Artifacts() *hub.ArtifactManager { return h.artifacts }

// Transfers 暴露文件传输管理器。
func (h *Hub) Transfers() *hub.FileTransferManager { return h.transfers }

// InboundEnabled 入站连接总开关是否启用。
func (h *Hub) InboundEnabled() bool { return h.inboundEnabled }

// InboundPolicy 暴露入站拨号 endpoint/端口/CIDR 策略。
func (h *Hub) InboundPolicy() *inbound.DialPolicy { return h.inboundPolicy }

// InboundAuthority 暴露入站 PKI；未启用入站时为 nil。
func (h *Hub) InboundAuthority() *inbound.Authority { return h.inboundAuthority }

// InboundDialer 暴露 Hub 主动拨号器；未启用入站时为 nil。
func (h *Hub) InboundDialer() *hub.InboundDialer { return h.inboundDialer }

// GatewayHandler 暴露 Agent Gateway HTTP Handler
func (h *Hub) GatewayHandler() http.Handler { return h.gateway.Handler() }

// EnsureDefaultAdmin 确保默认管理员
func (h *Hub) EnsureDefaultAdmin(ctx context.Context) error {
	return h.auth.EnsureDefaultAdmin(ctx, h.cfg.AdminUsername, h.cfg.AdminPassword)
}

// SetDefaults 设置默认配置项
func (h *Hub) SetDefaults(ctx context.Context) {
	defaults := map[string]string{
		"heartbeat_interval_sec":      "30",
		"revision_check_interval_sec": "45",
		"changelog_window":            "5000",
		"max_log_bytes":               "1048576",
	}
	for k, v := range defaults {
		if _, err := h.store.GetSetting(ctx, k); err != nil {
			h.store.SetSetting(ctx, k, v)
		}
	}
}

// handleHeartbeatTimeout 心跳超时回调：将节点置 offline。
// 走 NodeManager.MarkOffline（与断开清理同语义），maintenance/disabled 不被字面量覆盖。
func (h *Hub) handleHeartbeatTimeout(nodeID string) {
	if err := h.nodes.MarkOffline(h.ctx, nodeID); err != nil {
		h.logger.Warn("mark offline failed", "node", nodeID, "error", err)
		return
	}
	h.logger.Info("node marked offline", "node", nodeID)
}

// resetOrphanOnlineNodes 将「无活跃会话且 status=online」的节点复位为 offline。
// 启动阶段会话表为空，覆盖崩溃/重启残留的孤儿 online；持有活跃会话的 online 节点保留。
func (h *Hub) resetOrphanOnlineNodes(ctx context.Context) {
	nodes, err := h.store.ListNodes(ctx)
	if err != nil {
		h.logger.Warn("list nodes for online reset failed", "error", err)
		return
	}
	active := map[string]bool{}
	for _, id := range h.sessions.ConnectedNodeIDs() {
		active[id] = true
	}
	for _, n := range nodes {
		if n.Status != models.NodeStatusOnline || active[n.ID] {
			continue
		}
		if err := h.nodes.SetNodeStatus(ctx, n.ID, models.NodeStatusOffline); err != nil {
			h.logger.Warn("reset orphan online node failed", "node", n.ID, "error", err)
		}
	}
}

// Start 启动所有服务
func (h *Hub) Start() error {
	lock, err := os.OpenFile(filepath.Join(h.cfg.DataDir, "monitoring.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return fmt.Errorf("open monitoring lock: %w", err)
	}
	if err := unix.Flock(int(lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		lock.Close()
		return fmt.Errorf("monitoring already active: %w", err)
	}
	h.monitorLock, h.monitorDone = lock, make(chan struct{})
	go func() { defer close(h.monitorDone); h.monitoring.Run(h.ctx) }()
	// 原生指标聚合/清理：仅当存储实现 NativeMetricStore 时启动；错误只记日志。
	if nativeStore, ok := h.store.(store.NativeMetricStore); ok {
		if tuner, ok := h.store.(store.NativeMetricTuner); ok {
			tuner.SetNativeLateWindow(h.cfg.NativeMetricsLateWindow)
			tuner.SetNativeAggregateBudget(h.cfg.NativeMetricsAggregateBudget)
		}
		interval := h.cfg.NativeMetricsInterval
		if interval <= 0 {
			interval = nativeMetricMaintenanceInterval
		}
		aggregate := nativeStore.AggregateNodeMetrics
		if reporter, ok := h.store.(store.NativeAggregateReporter); ok {
			aggregate = func(ctx context.Context, now time.Time) error {
				err := nativeStore.AggregateNodeMetrics(ctx, now)
				stats := reporter.LastNativeAggregateStats()
				if h.logger != nil {
					h.logger.Info("native metric aggregate round",
						"duration", stats.Duration,
						"series", stats.Series,
						"transactions", stats.Transactions,
						"busy_errors", stats.BusyErrors,
						"truncated", stats.Truncated)
				}
				return err
			}
		}
		h.metricDone = startNativeMetricMaintenanceWithDelay(h.ctx, interval, nativeMetricInitialDelay,
			aggregate, nativeStore.PruneNodeMetrics, h.logger)
	}
	if h.alertEvaluator != nil {
		h.alertDone = startAlertEvaluation(h.ctx, h.alertEvaluator)
	}
	// 启动复位：进程刚起、内存会话表为空，DB 中残留 status=online 的节点没有
	// 对应活跃会话（孤儿 online），一次性复位 offline；maintenance/disabled 不受影响。
	h.resetOrphanOnlineNodes(h.ctx)

	h.sessions.StartHeartbeatChecker(h.ctx, h.cfg.HeartbeatTimeout, h.handleHeartbeatTimeout)

	// 交付停滞看门狗：终结长期停在 DELIVERING 的目标，避免传输永久非终态
	// （否则 DELETE /api/nodes/{id} 被 409 永久阻断）。
	h.transfers.StartDeliveryWatchdog(h.ctx, time.Minute)

	interval := time.Duration(h.cfg.RevisionCheckSec) * time.Second
	h.schedules.StartHubScheduler(h.ctx, interval)

	if h.inboundDialer != nil {
		h.inboundDialer.Start(h.ctx)
	}

	// Change Log 定期清理（保留 changelog_window 窗口内的记录）
	go func() {
		t := time.NewTicker(15 * time.Minute)
		defer t.Stop()
		for {
			select {
			case <-h.ctx.Done():
				return
			case <-t.C:
				current, err := h.revisions.Current(h.ctx)
				if err != nil {
					continue
				}
				window := h.cfg.ChangelogWindow
				if v, err := h.store.GetSetting(h.ctx, "changelog_window"); err == nil && v != "" {
					if n, err := strconv.ParseInt(v, 10, 64); err == nil && n > 0 {
						window = n
					}
				}
				keepFrom := current - window
				if keepFrom < 0 {
					keepFrom = 0
				}
				if err := h.store.PruneChangeLog(h.ctx, keepFrom); err != nil {
					h.logger.Warn("prune changelog failed", "error", err)
				}
			}
		}
	}()

	// Web/API server（单端口模式下此 handler 已含 Gateway 分流层）
	go func() {
		if h.webFS != nil {
			h.logger.Info("web server listening", "addr", h.cfg.WebAddr)
			if err := h.serve(h.cfg.WebAddr, h.webHandler(), h.cfg.WebTLSCert, h.cfg.WebTLSKey); err != nil {
				h.logger.Error("web server failed", "error", err)
			}
			return
		}
		h.logger.Info("api server listening", "addr", h.cfg.WebAddr)
		if err := h.serve(h.cfg.WebAddr, h.webHandler(), h.cfg.WebTLSCert, h.cfg.WebTLSKey); err != nil {
			h.logger.Error("api server failed", "error", err)
		}
	}()

	// Metrics 更新
	go func() {
		t := time.NewTicker(10 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-h.ctx.Done():
				return
			case <-t.C:
				h.metrics.SetConnectedAgents(h.sessions.Count())
			}
		}
	}()

	// Agent Gateway：双端口模式独立 listener；
	// 单端口模式不启动第二 listener，由 Web 最外层 middleware 分流。
	if h.singlePort {
		h.logger.Info("single-port gateway mode: gateway shares the web listener", "addr", h.cfg.WebAddr)
		return nil
	}
	go func() {
		h.logger.Info("agent gateway listening", "addr", h.cfg.GatewayAddr)
		if err := h.serve(h.cfg.GatewayAddr, h.gateway.Handler(), h.cfg.GatewayTLSCert, h.cfg.GatewayTLSKey); err != nil {
			h.logger.Error("gateway failed", "error", err)
		}
	}()

	return nil
}

func (h *Hub) serve(addr string, handler http.Handler, cert, key string) error {
	if cert != "" && key != "" {
		return http.ListenAndServeTLS(addr, cert, key, handler)
	}
	return http.ListenAndServe(addr, handler)
}

// Close 关闭
func (h *Hub) Close() error {
	h.cancel()
	if h.inboundDialer != nil {
		h.inboundDialer.Stop()
	}
	if h.transfers != nil {
		h.transfers.Close()
	}
	if h.metricDone != nil {
		<-h.metricDone
	}
	if h.alertDone != nil {
		<-h.alertDone
	}
	if h.monitorDone != nil {
		<-h.monitorDone
	}
	if h.monitorLock != nil {
		_ = unix.Flock(int(h.monitorLock.Fd()), unix.LOCK_UN)
		_ = h.monitorLock.Close()
	}
	return h.store.Close()
}

// loadAgentBinaryForArch 返回内置或磁盘上的 Agent 二进制及 SHA-256。
func loadAgentBinaryForArch(cfg Config, arch string) ([]byte, string, error) {
	var data []byte
	if payload := cfg.AgentBinaryPayloads[arch]; len(payload) > 0 {
		data = payload
	} else {
		path := cfg.AgentBinaryAMD64Path
		if arch == "arm64" {
			path = cfg.AgentBinaryARM64Path
		}
		if path == "" {
			return nil, "", fmt.Errorf("agent binary for %s is not configured", arch)
		}
		loaded, err := os.ReadFile(path)
		if err != nil {
			return nil, "", err
		}
		data = loaded
	}
	sum := sha256.Sum256(data)
	return data, hex.EncodeToString(sum[:]), nil
}
