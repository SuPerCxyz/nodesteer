package hubserver

import (
	"context"
	"fmt"
	"io/fs"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/cadentra/cadentra/internal/hub"
	"github.com/cadentra/cadentra/internal/hub/api"
	"github.com/cadentra/cadentra/internal/hub/auth"
	"github.com/cadentra/cadentra/internal/metrics"
	"github.com/cadentra/cadentra/internal/store"
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
	OIDC                 auth.OIDCConfig
}

// Hub 主结构
type Hub struct {
	cfg       Config
	store     store.Store
	auth      *auth.Manager
	revisions *hub.RevisionManager
	sessions  *hub.SessionManager
	nodes     *hub.NodeManager
	scripts   *hub.ScriptManager
	tasks     *hub.TaskManager
	schedules *hub.ScheduleManager
	syncMgr   *hub.SyncManager
	execMgr   *hub.ExecutionManager
	artifacts *hub.ArtifactManager
	apps      *hub.AppManager
	transfers *hub.FileTransferManager
	gateway   *hub.Gateway
	api       *api.Server
	metrics   *metrics.Metrics
	logger    *slog.Logger
	webFS     fs.FS
	ctx       context.Context
	cancel    context.CancelFunc
}

// New 组装 Hub
func New(cfg Config, logger *slog.Logger) (*Hub, error) {
	if (cfg.WebTLSCert == "") != (cfg.WebTLSKey == "") {
		return nil, fmt.Errorf("web_tls_cert and web_tls_key must be configured together")
	}
	if (cfg.GatewayTLSCert == "") != (cfg.GatewayTLSKey == "") {
		return nil, fmt.Errorf("gateway_tls_cert and gateway_tls_key must be configured together")
	}
	st, err := store.OpenSQLite(cfg.DataDir + "/hub.db")
	if err != nil {
		return nil, err
	}

	authMgr := auth.New(st, cfg.SessionTTL)
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
	scripts := hub.NewScriptManager(st, revisions, syncMgr)
	tasks := hub.NewTaskManager(st, revisions, syncMgr)

	artifacts, err := hub.NewArtifactManager(st, cfg.ArtifactDir, sessions, syncMgr)
	if err != nil {
		return nil, err
	}
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

	apiServer := api.New(st, authMgr, nodes, scripts, tasks, schedules, artifacts, apps, execMgr, logger)
	apiServer.SetFileTransfers(transfers)
	apiServer.SetEnrollmentConfig(cfg.RegistrationToken, gatewayBaseURL)
	apiServer.SetBaseURL(cfg.BaseURL)
	apiServer.SetAgentBinaryPaths(cfg.AgentBinaryAMD64Path, cfg.AgentBinaryARM64Path)
	apiServer.SetAgentBinaryPayloads(cfg.AgentBinaryPayloads)
	apiServer.SetMetrics(m)
	apiServer.SetRegistrationToken(cfg.RegistrationToken)
	apiServer.SetSessions(sessions)
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
		metrics: m, logger: logger, ctx: ctx, cancel: cancel,
	}, nil
}

// SetWebFS 设置内嵌前端
func (h *Hub) SetWebFS(fsys fs.FS) {
	h.webFS = fsys
}

// ServeMux 返回合并后的路由（含前端）
func (h *Hub) ServeMux() http.Handler {
	return h.api.Routes()
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

// Start 启动所有服务
func (h *Hub) Start() error {
	h.sessions.StartHeartbeatChecker(h.ctx, h.cfg.HeartbeatTimeout, func(nodeID string) {
		if err := h.nodes.SetNodeStatus(h.ctx, nodeID, "offline"); err != nil {
			h.logger.Warn("mark offline failed", "node", nodeID, "error", err)
		}
		h.logger.Info("node marked offline", "node", nodeID)
	})

	interval := time.Duration(h.cfg.RevisionCheckSec) * time.Second
	h.schedules.StartHubScheduler(h.ctx, interval)

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

	// Web/API server
	go func() {
		if h.webFS != nil {
			fsHandler := http.FileServer(http.FS(h.webFS))
			mux := http.NewServeMux()
			// API 由 api.Server.Routes 处理，前端静态文件用独立 mux 包装
			root := h.api.Routes()
			mux.Handle("/api/", root)
			mux.Handle("/api", root)
			mux.Handle("/healthz", root)
			mux.Handle("/readyz", root)
			mux.Handle("/metrics", root)
			mux.Handle("/", spaHandler(h.webFS, fsHandler))
			h.logger.Info("web server listening", "addr", h.cfg.WebAddr)
			if err := h.serve(h.cfg.WebAddr, mux, h.cfg.WebTLSCert, h.cfg.WebTLSKey); err != nil {
				h.logger.Error("web server failed", "error", err)
			}
			return
		}
		h.logger.Info("api server listening", "addr", h.cfg.WebAddr)
		if err := h.serve(h.cfg.WebAddr, h.api.Routes(), h.cfg.WebTLSCert, h.cfg.WebTLSKey); err != nil {
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

	// Agent Gateway
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
	return h.store.Close()
}

// spaHandler SPA 回退：非 /api 路径若文件不存在则返回 index.html
func spaHandler(fsys fs.FS, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/")
		if path != "" && !strings.Contains(path, ".") {
			if _, err := fs.Stat(fsys, path); err != nil {
				// 回退到 index.html
				data, err := fs.ReadFile(fsys, "index.html")
				if err == nil {
					w.Header().Set("Content-Type", "text/html; charset=utf-8")
					w.Write(data)
					return
				}
			}
		}
		next.ServeHTTP(w, r)
	})
}
