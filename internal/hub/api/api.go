package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/metrics"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// Server REST API 服务器
type Server struct {
	store     store.Store
	auth      *auth.Manager
	nodes     *hub.NodeManager
	scripts   *hub.ScriptManager
	tasks     *hub.TaskManager
	schedules *hub.ScheduleManager
	artifacts *hub.ArtifactManager
	apps      *hub.AppManager
	execs     *hub.ExecutionManager
	metrics   *metrics.Metrics
	logger    *slog.Logger

	// OIDC 认证客户端（仅 SSO-only 模式注入，本地模式恒为 nil）
	oidc *auth.OIDC

	// loginFails 登录失败计数（简单限速：连续失败递增延迟，成功后重置）
	loginFails loginFailTracker

	// RegistrationToken Agent 注册令牌，用于 Agent 身份访问（如 Artifact 下载）
	RegistrationToken string
	sessions          *hub.SessionManager
	transfers         *hub.FileTransferManager
	gatewayBaseURL    string
	baseURL           string
	agentBinaryPaths  map[string]string
	agentBinaryData   map[string][]byte
	readyCheck        func(context.Context) error
}

// SetMetrics 注入指标收集器
func (s *Server) SetMetrics(m *metrics.Metrics) { s.metrics = m }

// SetRegistrationToken 设置 Agent 注册令牌
func (s *Server) SetRegistrationToken(token string) { s.RegistrationToken = token }

// SetOIDC 设置 OIDC 客户端
func (s *Server) SetOIDC(o *auth.OIDC) { s.oidc = o }

// SetSessions 设置在线 Agent 会话，供运行期设置即时下发。
func (s *Server) SetSessions(sm *hub.SessionManager) { s.sessions = sm }

// SetFileTransfers 注入文件传输管理器。
func (s *Server) SetFileTransfers(m *hub.FileTransferManager) { s.transfers = m }

// SetEnrollmentConfig 设置管理员节点纳管信息。
func (s *Server) SetEnrollmentConfig(registrationToken, gatewayBaseURL string) {
	s.RegistrationToken = registrationToken
	s.gatewayBaseURL = gatewayBaseURL
}

// SetBaseURL 设置 Web/API 基址，供生成的二进制下载命令使用。
func (s *Server) SetBaseURL(baseURL string) { s.baseURL = strings.TrimRight(baseURL, "/") }

// SetAgentBinaryPaths 设置不同架构的 Agent 二进制路径。
func (s *Server) SetAgentBinaryPaths(amd64Path, arm64Path string) {
	s.agentBinaryPaths = map[string]string{
		"amd64": amd64Path,
		"arm64": arm64Path,
	}
}

// SetAgentBinaryPayloads 设置嵌入 Hub 可执行文件的 Agent 二进制。
func (s *Server) SetAgentBinaryPayloads(payloads map[string][]byte) {
	s.agentBinaryData = payloads
}

// SetReadyCheck 注入运行时就绪检查；未注入时用于纯 API 测试，默认就绪。
func (s *Server) SetReadyCheck(check func(context.Context) error) { s.readyCheck = check }

// New 创建 API 服务器
func New(st store.Store, am *auth.Manager, nm *hub.NodeManager, sm *hub.ScriptManager,
	tm *hub.TaskManager, scm *hub.ScheduleManager, artm *hub.ArtifactManager,
	appm *hub.AppManager, exm *hub.ExecutionManager, logger *slog.Logger) *Server {
	return &Server{
		store: st, auth: am, nodes: nm, scripts: sm, tasks: tm,
		schedules: scm, artifacts: artm, apps: appm, execs: exm, logger: logger,
		agentBinaryPaths: map[string]string{},
		agentBinaryData:  map[string][]byte{},
		loginFails:       loginFailTracker{fails: map[string]*loginFailEntry{}},
	}
}

// Routes 注册路由
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()

	// 认证
	mux.HandleFunc("/api/login", s.handleLogin)
	mux.HandleFunc("/api/logout", s.withAuth(s.handleLogout, "read"))
	mux.HandleFunc("/api/me", s.withAuth(s.handleMe, "read"))

	// OIDC
	mux.HandleFunc("/api/oidc/state", s.handleOIDCState)
	mux.HandleFunc("/api/oidc/login", s.handleOIDCLogin)
	mux.HandleFunc("/api/oidc/callback", s.handleOIDCCallback)

	// Nodes
	mux.HandleFunc("/api/nodes/enrollment", s.withAuth(s.handleNodeEnrollment, "read"))
	mux.HandleFunc("/api/nodes", s.withAuth(s.handleNodes, "read"))
	mux.HandleFunc("/api/nodes/", s.withAuth(s.handleNodeByID, "read"))

	// Public Agent binary payload used by generated enrollment commands.
	mux.HandleFunc("/api/agent/binary", s.handleAgentBinary)

	// File transfers
	mux.HandleFunc("/api/transfers", s.withAuth(s.handleTransfers, "read"))
	mux.HandleFunc("/api/transfers/", s.withAuth(s.handleTransferByID, "read"))

	// Groups
	mux.HandleFunc("/api/groups", s.withAuth(s.handleGroups, "read"))
	mux.HandleFunc("/api/groups/", s.withAuth(s.handleGroupByID, "read"))

	// Scripts
	mux.HandleFunc("/api/scripts", s.withAuth(s.handleScripts, "read"))
	mux.HandleFunc("/api/scripts/", s.withAuth(s.handleScriptByID, "read"))

	// Tasks
	mux.HandleFunc("/api/tasks", s.withAuth(s.handleTasks, "read"))
	mux.HandleFunc("/api/tasks/", s.withAuth(s.handleTaskByID, "read"))

	// Schedules
	mux.HandleFunc("/api/schedules", s.withAuth(s.handleSchedules, "read"))
	mux.HandleFunc("/api/schedules/", s.withAuth(s.handleScheduleByID, "read"))

	// Artifacts
	mux.HandleFunc("/api/artifacts", s.withAuth(s.handleArtifacts, "read"))
	mux.HandleFunc("/api/artifacts/{id}/download", s.withAgentOrUserAuth(s.handleArtifactDownload))
	mux.HandleFunc("/api/artifacts/", s.withAuth(s.handleArtifactByID, "read"))

	// Applications
	mux.HandleFunc("/api/applications", s.withAuth(s.handleApplications, "read"))
	mux.HandleFunc("/api/applications/", s.withAuth(s.handleApplicationByID, "read"))

	// Executions
	mux.HandleFunc("/api/executions", s.withAuth(s.handleExecutions, "read"))
	mux.HandleFunc("/api/executions/", s.withAuth(s.handleExecutionByID, "read"))

	// Audit
	mux.HandleFunc("/api/audit", s.withAuth(s.handleAudit, "read"))

	// Settings
	mux.HandleFunc("/api/settings", s.withAuth(s.handleSettings, "read"))

	// Users
	mux.HandleFunc("/api/users", s.withAuth(s.handleUsers, "read"))
	mux.HandleFunc("/api/users/", s.withAuth(s.handleUserByID, "read"))

	// Health
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	})
	mux.HandleFunc("/readyz", func(w http.ResponseWriter, r *http.Request) {
		if s.readyCheck != nil {
			if err := s.readyCheck(r.Context()); err != nil {
				writeErr(w, http.StatusServiceUnavailable, "not ready")
				return
			}
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ready"))
	})
	mux.HandleFunc("/metrics", s.withAuth(s.handleMetrics, "read"))

	return s.withCORS(mux)
}

// ---------- 中间件 ----------

func (s *Server) withCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-NodeSteer-Agent-Token, X-NodeSteer-Agent-ID")
		w.Header().Set("Access-Control-Expose-Headers", "X-Total-Count")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// withAuth 认证与鉴权中间件
func (s *Server) withAuth(next http.HandlerFunc, action string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token := bearerToken(r)
		if token == "" {
			writeErr(w, http.StatusUnauthorized, "unauthorized")
			return
		}
		sess, ok := s.auth.Authenticate(r.Context(), token)
		if !ok {
			writeErr(w, http.StatusUnauthorized, "invalid or expired session")
			return
		}
		if !auth.HasPermission(sess.Role, action) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		// 设置当前用户上下文
		r = r.WithContext(withUser(r.Context(), sess))
		r = r.WithContext(hub.WithAuditInfo(r.Context(), sess.UserID, sess.Username))
		next(w, r)
	}
}

// withWriteAuth 写操作（需 run/修改权限）
func (s *Server) withWriteAuth(next http.HandlerFunc, action string) http.HandlerFunc {
	return s.withAuth(next, action)
}

// withAgentOrUserAuth Agent 身份（registration token）或用户会话认证
// 用于 Agent 部署时需要访问的端点（如 Artifact 下载）。
func (s *Server) withAgentOrUserAuth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		// 先尝试用户会话
		if token := bearerToken(r); token != "" {
			if sess, ok := s.auth.Authenticate(r.Context(), token); ok {
				if auth.HasPermission(sess.Role, "read") {
					r = r.WithContext(withUser(r.Context(), sess))
					next(w, r)
					return
				}
			}
		}
		// Agent 身份：X-NodeSteer-Agent-Token 或 Authorization 携带 registration token
		agentToken := r.Header.Get("X-NodeSteer-Agent-Token")
		if agentToken == "" {
			agentToken = r.URL.Query().Get("agent_token")
		}
		if s.RegistrationToken != "" && agentToken == s.RegistrationToken {
			next(w, r)
			return
		}
		agentID := r.Header.Get("X-NodeSteer-Agent-ID")
		if agentID != "" && agentToken != "" {
			if node, err := s.store.GetNodeByAgentID(r.Context(), agentID); err == nil &&
				s.nodes.AuthenticateAgent(r.Context(), node.ID, agentToken) {
				next(w, r)
				return
			}
		}
		writeErr(w, http.StatusUnauthorized, "unauthorized")
	}
}

func bearerToken(r *http.Request) string {
	h := r.Header.Get("Authorization")
	if strings.HasPrefix(h, "Bearer ") {
		return strings.TrimPrefix(h, "Bearer ")
	}
	return ""
}

// ---------- 响应工具 ----------

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func readJSON(r *http.Request, v any) error {
	return json.NewDecoder(r.Body).Decode(v)
}

// ---------- 认证 Handlers ----------

// oidcActive OIDC 是否作为当前登录方式生效。
// 仅当 allow_local_login=false（SSO-only 模式）且 OIDC 客户端就绪时为 true；
// 本地模式（默认 true）下恒为 false —— 两种登录方式互斥，OIDC 完全失效。
func (s *Server) oidcActive() bool {
	return !s.auth.LocalLoginAllowed() && s.oidc != nil && s.oidc.Enabled()
}

// 登录失败限速（最小实现，无第三方依赖）：同 IP+用户名连续失败达到阈值后
// 递增延迟，成功后重置，记录超时后视为新的计数窗口。
const (
	loginFailThreshold = 3 // 连续失败达到该次数后开始延迟
	loginFailBaseDelay = 200 * time.Millisecond
	loginFailMaxDelay  = 2 * time.Second
	loginFailEntryTTL  = 10 * time.Minute
)

type loginFailEntry struct {
	count    int
	lastFail time.Time
}

type loginFailTracker struct {
	mu    sync.Mutex
	fails map[string]*loginFailEntry
}

// delay 按当前连续失败次数返回本次请求需等待的时长（首个窗口返回 0）。
func (t *loginFailTracker) delay(key string) time.Duration {
	t.mu.Lock()
	defer t.mu.Unlock()
	e, ok := t.fails[key]
	if !ok {
		return 0
	}
	if time.Since(e.lastFail) > loginFailEntryTTL {
		delete(t.fails, key)
		return 0
	}
	if e.count < loginFailThreshold {
		return 0
	}
	shift := e.count - loginFailThreshold
	if shift > 8 {
		shift = 8
	}
	d := time.Duration(1<<shift) * loginFailBaseDelay
	if d > loginFailMaxDelay {
		d = loginFailMaxDelay
	}
	return d
}

// fail 记录一次登录失败。
func (t *loginFailTracker) fail(key string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.fails == nil {
		t.fails = map[string]*loginFailEntry{}
	}
	e, ok := t.fails[key]
	if !ok || time.Since(e.lastFail) > loginFailEntryTTL {
		e = &loginFailEntry{}
		t.fails[key] = e
	}
	e.count++
	e.lastFail = time.Now()
}

// reset 登录成功后清零该 key 的失败计数。
func (t *loginFailTracker) reset(key string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	delete(t.fails, key)
}

// loginFailKey 限速维度：客户端 IP + 小写用户名（用户名为空也计入）。
func loginFailKey(r *http.Request, username string) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	return host + "|" + strings.ToLower(username)
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := readJSON(r, &req); err != nil {
		writeErr(w, http.StatusBadRequest, "invalid request")
		return
	}
	// SSO-only 模式（allow_local_login=false）：密码登录一律 403，
	// 与本地账户是否存在、密码是否正确无关（绝对语义）。
	if s.auth.LocalLoginDisabled() {
		_ = s.store.AddAudit(r.Context(), &models.AuditLog{
			Username: req.Username, Action: "login", Resource: "auth",
			Detail: "local login disabled",
		})
		writeErr(w, http.StatusForbidden, "local login disabled")
		return
	}
	key := loginFailKey(r, req.Username)
	if d := s.loginFails.delay(key); d > 0 {
		time.Sleep(d)
	}
	sess, err := s.auth.Login(r.Context(), req.Username, req.Password)
	if err != nil {
		s.loginFails.fail(key)
		// 失败登录写审计：detail 不得包含密码
		_ = s.store.AddAudit(r.Context(), &models.AuditLog{
			Username: req.Username, Action: "login", Resource: "auth",
			Detail: "failed: invalid credentials",
		})
		writeErr(w, http.StatusUnauthorized, "invalid credentials")
		return
	}
	s.loginFails.reset(key)
	s.store.AddAudit(r.Context(), &models.AuditLog{
		UserID: sess.UserID, Username: sess.Username, Action: "login", Resource: "auth",
		Detail: "local",
	})
	writeJSON(w, http.StatusOK, map[string]any{
		"token": sess.Token, "user": sess,
	})
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	token := bearerToken(r)
	if token != "" {
		s.auth.Logout(token)
	}
	writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	sess := currentUser(r.Context())
	if sess == nil {
		writeErr(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	writeJSON(w, http.StatusOK, sess)
}

// ---------- OIDC Handlers ----------

// handleOIDCState 返回当前登录方式互斥状态：
// enabled=SSO 是否为当前登录方式；local_fallback=本地密码登录是否可用（字段名保留兼容旧前端）。
func (s *Server) handleOIDCState(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{
		"enabled":        s.oidcActive(),
		"local_fallback": s.auth.LocalLoginAllowed(),
	})
}

// handleOIDCLogin 生成授权跳转 URL（本地模式下 OIDC 完全失效 → 404）
func (s *Server) handleOIDCLogin(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if !s.oidcActive() {
		writeErr(w, http.StatusNotFound, "oidc not enabled")
		return
	}
	url, err := s.oidc.AuthCodeURL()
	if err != nil {
		s.logger.Error("oidc auth url", "error", err)
		writeErr(w, http.StatusInternalServerError, "failed to start oidc login")
		return
	}
	http.Redirect(w, r, url, http.StatusFound)
}

// handleOIDCCallback 处理 IdP 回调：验证并建立会话
func (s *Server) handleOIDCCallback(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if !s.oidcActive() {
		writeErr(w, http.StatusNotFound, "oidc not enabled")
		return
	}
	state := r.URL.Query().Get("state")
	code := r.URL.Query().Get("code")
	if state == "" || code == "" {
		http.Redirect(w, r, oidcFailureURL(s.oidc), http.StatusFound)
		return
	}
	username, role, err := s.oidc.Exchange(r.Context(), state, code)
	if err != nil {
		s.logger.Error("oidc exchange", "error", err)
		http.Redirect(w, r, oidcFailureURL(s.oidc), http.StatusFound)
		return
	}
	sess, err := s.auth.SSOLogin(r.Context(), username, role)
	if err != nil {
		s.logger.Error("oidc sso login", "user", username, "error", err)
		http.Redirect(w, r, oidcFailureURL(s.oidc), http.StatusFound)
		return
	}
	s.store.AddAudit(r.Context(), &models.AuditLog{
		UserID: sess.UserID, Username: sess.Username, Action: "login", Resource: "auth",
		Detail: "oidc",
	})
	// 返回内嵌 JS 的 HTML：将 token 写入 localStorage 后跳转前端（token 不进 URL，避免日志泄露）
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	tokenJSON, _ := json.Marshal(sess.Token)
	baseJSON, _ := json.Marshal(s.oidc.BaseURL())
	w.Header().Set("Cache-Control", "no-store")
	fmt.Fprintf(w, `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Sign in</title></head>
<body><script>
localStorage.setItem('nodesteer_token', %s);
window.location.replace(%s);
</script><p>Signing in...</p></body></html>`,
		tokenJSON, baseJSON)
}

func oidcFailureURL(o *auth.OIDC) string {
	return strings.TrimRight(o.BaseURL(), "/") + "/sign-in?error=oidc_failed"
}

// ---------- Nodes ----------

func (s *Server) handleNodes(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		nodes, err := s.nodes.ListNodes(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, nodes)
	case http.MethodPost:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var req struct {
			ID     string            `json:"id"`
			Status string            `json:"status"`
			Labels map[string]string `json:"labels"`
		}
		if err := readJSON(r, &req); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if req.ID == "" {
			writeErr(w, http.StatusBadRequest, "id required")
			return
		}
		if req.Status != "" {
			if err := s.nodes.SetNodeStatus(r.Context(), req.ID, req.Status); err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
		}
		if req.Labels != nil {
			if err := s.nodes.SetLabels(r.Context(), req.ID, req.Labels); err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
		}
		s.audit(r, "node", req.ID, "update")
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleNodeEnrollment(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if s.RegistrationToken == "" || s.gatewayBaseURL == "" {
		writeErr(w, http.StatusServiceUnavailable, "agent gateway enrollment is not configured")
		return
	}
	nodeName, nodeIP, hubAddress, err := parseEnrollmentOptions(r)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	nodeID, agentID := "", ""
	if r.Method == http.MethodPost {
		node, err := s.nodes.PrepareEnrollment(r.Context(), nodeName, nodeIP, models.DeploymentModeNative)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		nodeID = node.ID
		agentID = node.AgentID
	}
	base := strings.TrimRight(s.gatewayBaseURL, "/")
	if hubAddress != "" {
		// configured 优先传入已解析的 Gateway 基址：显式配置（或其派生值）优先于请求携带的
		// hub_address，避免反代部署下 hub_address 把纳管地址改写成错误端口；
		// 仅 configured 为空时 hub_address 才作为 hostname 来源参与派生。
		base = hub.ResolveGatewayBaseURL(s.gatewayBaseURL, hubAddress, configuredGatewayAddr(s.gatewayBaseURL))
	}
	wsURL := base
	if strings.HasPrefix(wsURL, "https://") {
		wsURL = "wss://" + strings.TrimPrefix(wsURL, "https://")
	} else if strings.HasPrefix(wsURL, "http://") {
		wsURL = "ws://" + strings.TrimPrefix(wsURL, "http://")
	}
	downloadBase := strings.TrimRight(hubAddress, "/")
	if downloadBase == "" {
		downloadBase = s.baseURL
	}
	if downloadBase == "" {
		writeErr(w, http.StatusServiceUnavailable, "Hub web base URL is not configured")
		return
	}
	binaryURLBase := fmt.Sprintf("%s/api/agent/binary?architecture=", downloadBase)
	agentIDConfig := ""
	if agentID != "" {
		agentIDConfig = fmt.Sprintf("agent_id: %q\n", agentID)
	}
	config := fmt.Sprintf("hub_url: %q\nregistration_token: %q\n%snode_name: %q\nnode_ip: %q\ndeployment_mode: native\nhost_integration: false\ndata_dir: /var/lib/nodesteer\nagent_version: 0.1.0\n", wsURL, s.RegistrationToken, agentIDConfig, nodeName, nodeIP)
	native := fmt.Sprintf(`set -eu
case "$(uname -m)" in
  x86_64|amd64) detected_arch=amd64 ;;
  aarch64|arm64) detected_arch=arm64 ;;
  *) echo "unsupported Linux architecture: $(uname -m)" >&2; exit 1 ;;
esac
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "sha256sum is required" >&2; exit 1; }
agent_tmp=$(mktemp)
header_tmp=$(mktemp)
trap 'rm -f "$agent_tmp" "$header_tmp"' EXIT
binary_url=%s"$detected_arch"
curl --fail --silent --show-error --location --retry 3 --dump-header "$header_tmp" --output "$agent_tmp" "$binary_url"
binary_sha256=$(awk 'tolower($1) == "x-agent-binary-sha256:" {print $2}' "$header_tmp" | tr -d '\r' | tail -n 1)
test -n "$binary_sha256" || { echo "Hub did not provide Agent binary SHA256" >&2; exit 1; }
printf '%%s  %%s\n' "$binary_sha256" "$agent_tmp" | sha256sum -c -
sudo install -d /etc/nodesteer
sudo install -m 0755 "$agent_tmp" /usr/local/bin/nodesteer-agent
sudo sh -c 'cat > /etc/systemd/system/nodesteer-agent.service' <<'EOF'
%sEOF
sudo sh -c 'cat > /etc/nodesteer/agent.yaml' <<'EOF'
%sEOF
sudo systemctl daemon-reload && sudo systemctl enable nodesteer-agent && sudo systemctl restart nodesteer-agent`,
		shellQuote(binaryURLBase), agentServiceUnit, config)
	agentImage := "ghcr.io/supercxyz/nodesteer-agent:latest"
	agentIDEnv := ""
	if agentID != "" {
		agentIDEnv = fmt.Sprintf(" -e NODESTEER_AGENT_ID=%s", shellQuote(agentID))
	}
	dockerRun := fmt.Sprintf("docker rm -f nodesteer-agent 2>/dev/null || true; docker run -d --name nodesteer-agent --restart unless-stopped -e NODESTEER_HUB_URL=%s -e NODESTEER_REGISTRATION_TOKEN=%s -e NODESTEER_NODE_NAME=%s -e NODESTEER_NODE_IP=%s%s -e NODESTEER_DEPLOYMENT_MODE=docker -v nodesteer-agent-data:/var/lib/nodesteer %s", shellQuote(wsURL), shellQuote(s.RegistrationToken), shellQuote(nodeName), shellQuote(nodeIP), agentIDEnv, agentImage)
	agentIDCompose := ""
	if agentID != "" {
		agentIDCompose = fmt.Sprintf("      NODESTEER_AGENT_ID: %q\n", agentID)
	}
	compose := fmt.Sprintf("services:\n  nodesteer-agent:\n    image: %s\n    restart: unless-stopped\n    environment:\n      NODESTEER_HUB_URL: %q\n      NODESTEER_REGISTRATION_TOKEN: %q\n      NODESTEER_NODE_NAME: %q\n      NODESTEER_NODE_IP: %q\n%s      NODESTEER_DEPLOYMENT_MODE: docker\n    volumes:\n      - nodesteer-agent-data:/var/lib/nodesteer\n\nvolumes:\n  nodesteer-agent-data:\n", agentImage, wsURL, s.RegistrationToken, nodeName, nodeIP, agentIDCompose)
	writeJSON(w, http.StatusOK, map[string]any{
		"gateway_url": wsURL, "gateway_base_url": base, "agent_image": agentImage, "node_id": nodeID, "agent_id": agentID,
		"native": native, "docker_run": dockerRun, "docker_compose": compose,
	})
}

const agentServiceUnit = `[Unit]
Description=NodeSteer Agent
Documentation=file:///usr/share/doc/nodesteer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/nodesteer-agent --config /etc/nodesteer/agent.yaml
Restart=on-failure
RestartSec=5
StateDirectory=nodesteer
StateDirectoryMode=0750
NoNewPrivileges=false

[Install]
WantedBy=multi-user.target
`

func (s *Server) handleAgentBinary(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	architecture, err := normalizeArchitecture(r.URL.Query().Get("architecture"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	data, digest, err := s.agentBinaryDataFor(architecture)
	if err != nil {
		writeErr(w, http.StatusServiceUnavailable, fmt.Sprintf("agent binary for %s is unavailable", architecture))
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.Header().Set("X-Agent-Binary-SHA256", digest)
	http.ServeContent(w, r, "nodesteer-agent-"+architecture, time.Time{}, bytes.NewReader(data))
}

func (s *Server) agentBinaryDataFor(architecture string) ([]byte, string, error) {
	if data := s.agentBinaryData[architecture]; len(data) > 0 {
		digest := sha256.Sum256(data)
		return data, hex.EncodeToString(digest[:]), nil
	}
	path := s.agentBinaryPaths[architecture]
	if path == "" {
		return nil, "", os.ErrNotExist
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, "", err
	}
	hash := sha256.New()
	_, _ = hash.Write(data)
	return data, hex.EncodeToString(hash.Sum(nil)), nil
}

func normalizeArchitecture(value string) (string, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "amd64":
		return "amd64", nil
	case "arm64":
		return "arm64", nil
	default:
		return "", errors.New("architecture must be amd64 or arm64")
	}
}

func parseEnrollmentOptions(r *http.Request) (string, string, string, error) {
	nodeName := strings.TrimSpace(r.URL.Query().Get("node_name"))
	nodeIP := strings.TrimSpace(r.URL.Query().Get("node_ip"))
	hubAddress := strings.TrimSpace(r.URL.Query().Get("hub_address"))
	if r.Method == http.MethodPost {
		var req struct {
			NodeName   string `json:"node_name"`
			NodeIP     string `json:"node_ip"`
			HubAddress string `json:"hub_address"`
		}
		if err := readJSON(r, &req); err != nil {
			return "", "", "", errors.New("invalid request")
		}
		nodeName = strings.TrimSpace(req.NodeName)
		nodeIP = strings.TrimSpace(req.NodeIP)
		hubAddress = strings.TrimSpace(req.HubAddress)
	}
	if nodeName == "" || len(nodeName) > 128 {
		return "", "", "", errors.New("node_name is required and must be at most 128 characters")
	}
	for _, ch := range nodeName {
		if (ch < 'a' || ch > 'z') && (ch < 'A' || ch > 'Z') &&
			(ch < '0' || ch > '9') && ch != '-' && ch != '_' && ch != '.' {
			return "", "", "", errors.New("node_name may contain only letters, numbers, '-', '_' and '.'")
		}
	}
	if !validNodeAddress(nodeIP) {
		return "", "", "", errors.New("node_ip must be a valid IP address or DNS hostname")
	}
	if hubAddress != "" {
		u, err := url.Parse(hubAddress)
		if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
			return "", "", "", errors.New("hub_address must be an http or https URL")
		}
	}
	return nodeName, nodeIP, hubAddress, nil
}

func validNodeAddress(value string) bool {
	if net.ParseIP(value) != nil {
		return true
	}
	name := strings.TrimSuffix(value, ".")
	if name == "" || len(name) > 253 {
		return false
	}
	for _, label := range strings.Split(name, ".") {
		if len(label) == 0 || len(label) > 63 ||
			!isHostnameLetterOrDigit(label[0]) ||
			!isHostnameLetterOrDigit(label[len(label)-1]) {
			return false
		}
		for i := 1; i < len(label)-1; i++ {
			if !isHostnameLetterOrDigit(label[i]) && label[i] != '-' {
				return false
			}
		}
	}
	return true
}

func isHostnameLetterOrDigit(value byte) bool {
	return (value >= 'a' && value <= 'z') ||
		(value >= 'A' && value <= 'Z') ||
		(value >= '0' && value <= '9')
}

// configuredGatewayAddr 返回配置的 Gateway 基址对应的端口地址：
// 显式端口优先；URL 未写端口时按 scheme 补隐式端口（https→:443、http→:80）；
// 解析失败或无 scheme 时兜底 :8443。
func configuredGatewayAddr(base string) string {
	u, err := url.Parse(base)
	if err == nil {
		if u.Port() != "" {
			return ":" + u.Port()
		}
		switch u.Scheme {
		case "https":
			return ":443"
		case "http":
			return ":80"
		}
	}
	return ":8443"
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func (s *Server) handleNodeByID(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/nodes/")
	parts := strings.Split(path, "/")
	id := parts[0]
	if len(parts) >= 2 && parts[1] == "revoke-credential" {
		if r.Method != http.MethodPost || !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.nodes.RevokeCredential(r.Context(), id); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		s.audit(r, "node", id, "revoke_credential")
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
		return
	}
	node, err := s.nodes.GetNode(r.Context(), id)
	if err != nil {
		writeErr(w, http.StatusNotFound, "node not found")
		return
	}
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, http.StatusOK, node)
	case http.MethodDelete:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.nodes.Delete(r.Context(), id); err != nil {
			writeErr(w, http.StatusConflict, err.Error())
			return
		}
		s.audit(r, "node", id, "delete")
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	case http.MethodPost, http.MethodPut:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var req struct {
			Status string            `json:"status"`
			Labels map[string]string `json:"labels"`
		}
		if err := readJSON(r, &req); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if req.Status != "" {
			if err := s.nodes.SetNodeStatus(r.Context(), id, req.Status); err != nil {
				writeErr(w, http.StatusBadRequest, err.Error())
				return
			}
		}
		if req.Labels != nil {
			if err := s.nodes.SetLabels(r.Context(), id, req.Labels); err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
		}
		s.audit(r, "node", id, "update")
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// handleTransfers 文件中继任务列表与创建。
func (s *Server) handleTransfers(w http.ResponseWriter, r *http.Request) {
	if s.transfers == nil {
		writeErr(w, http.StatusServiceUnavailable, "file transfer is not configured")
		return
	}
	switch r.Method {
	case http.MethodGet:
		items, err := s.transfers.List(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, items)
	case http.MethodPost:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var req struct {
			SourceNodeID string                          `json:"source_node_id"`
			SourcePath   string                          `json:"source_path"`
			Targets      []hub.FileTransferTargetRequest `json:"targets"`
		}
		if err := readJSON(r, &req); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		item, err := s.transfers.Create(r.Context(), req.SourceNodeID, req.SourcePath, req.Targets)
		if err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		s.audit(r, "file_transfer", item.ID, "create")
		writeJSON(w, http.StatusOK, item)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleTransferByID(w http.ResponseWriter, r *http.Request) {
	if s.transfers == nil {
		writeErr(w, http.StatusServiceUnavailable, "file transfer is not configured")
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/api/transfers/")
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) == 0 || parts[0] == "" {
		writeErr(w, http.StatusNotFound, "transfer not found")
		return
	}
	id := parts[0]
	if len(parts) == 1 && r.Method == http.MethodGet {
		item, err := s.transfers.Get(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusNotFound, "transfer not found")
			return
		}
		writeJSON(w, http.StatusOK, item)
		return
	}
	if !s.canAdmin(r) || r.Method != http.MethodPost || len(parts) != 2 {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	var item *models.FileTransfer
	var err error
	switch parts[1] {
	case "retry":
		targetID := r.URL.Query().Get("target_id")
		item, err = s.transfers.Retry(r.Context(), id, targetID)
		s.audit(r, "file_transfer", id, "retry")
	case "cancel":
		item, err = s.transfers.Cancel(r.Context(), id)
		s.audit(r, "file_transfer", id, "cancel")
	default:
		writeErr(w, http.StatusNotFound, "transfer action not found")
		return
	}
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

// ---------- Groups ----------

func (s *Server) handleGroups(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		groups, err := s.store.ListGroups(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, groups)
	case http.MethodPost:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var g models.Group
		if err := readJSON(r, &g); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if g.Type == "" {
			g.Type = "static"
		}
		if err := s.nodes.CreateGroup(r.Context(), &g); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, g)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleGroupByID(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/groups/")
	switch r.Method {
	case http.MethodGet:
		g, err := s.store.GetGroup(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusNotFound, "group not found")
			return
		}
		writeJSON(w, http.StatusOK, g)
	case http.MethodPut:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var g models.Group
		if err := readJSON(r, &g); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		g.ID = id
		if err := s.nodes.UpdateGroup(r.Context(), &g); err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, g)
	case http.MethodDelete:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.nodes.DeleteGroup(r.Context(), id); err != nil {
			writeErr(w, http.StatusConflict, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// ---------- Scripts ----------

func (s *Server) handleScripts(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		list, err := s.scripts.List(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, list)
	case http.MethodPost:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var sc models.Script
		if err := readJSON(r, &sc); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if sc.Name == "" {
			writeErr(w, http.StatusBadRequest, "name required")
			return
		}
		if sc.Interpreter == "" {
			sc.Interpreter = "shell"
		}
		if err := s.scripts.Create(r.Context(), &sc); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, sc)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleScriptByID(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/scripts/")
	parts := strings.Split(path, "/")
	id := parts[0]
	switch r.Method {
	case http.MethodGet:
		if len(parts) >= 2 && parts[1] == "revisions" {
			if len(parts) == 2 {
				// 列出全部历史版本
				entries, err := s.scripts.ListRevisions(r.Context(), id)
				if err != nil {
					writeErr(w, http.StatusInternalServerError, err.Error())
					return
				}
				writeJSON(w, http.StatusOK, entries)
				return
			}
			rev, err := strconv.ParseInt(parts[2], 10, 64)
			if err != nil {
				writeErr(w, http.StatusBadRequest, "invalid revision")
				return
			}
			entry, err := s.scripts.GetRevision(r.Context(), id, rev)
			if err != nil {
				writeErr(w, http.StatusNotFound, "revision not found")
				return
			}
			writeJSON(w, http.StatusOK, entry)
			return
		}
		sc, err := s.scripts.Get(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusNotFound, "script not found")
			return
		}
		writeJSON(w, http.StatusOK, sc)
	case http.MethodPut:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var sc models.Script
		if err := readJSON(r, &sc); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		sc.ID = id
		if err := s.scripts.Update(r.Context(), &sc); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, sc)
	case http.MethodDelete:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.scripts.Delete(r.Context(), id); err != nil {
			writeErr(w, http.StatusConflict, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// ---------- Tasks ----------

func (s *Server) handleTasks(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		list, err := s.tasks.List(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, list)
	case http.MethodPost:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var t models.Task
		if err := readJSON(r, &t); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if t.Name == "" {
			writeErr(w, http.StatusBadRequest, "name required")
			return
		}
		if err := s.tasks.Create(r.Context(), &t); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, t)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleTaskByID(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/tasks/")
	parts := strings.Split(path, "/")
	id := parts[0]

	// Run Now
	if len(parts) >= 2 && parts[1] == "run" {
		if !s.canRun(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		s.handleRunNow(w, r, id)
		return
	}

	switch r.Method {
	case http.MethodGet:
		t, err := s.tasks.Get(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusNotFound, "task not found")
			return
		}
		writeJSON(w, http.StatusOK, t)
	case http.MethodPut:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var t models.Task
		if err := readJSON(r, &t); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		t.ID = id
		if err := s.tasks.Update(r.Context(), &t); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, t)
	case http.MethodDelete:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.tasks.Delete(r.Context(), id); err != nil {
			writeErr(w, http.StatusConflict, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// handleRunNow Run Now
func (s *Server) handleRunNow(w http.ResponseWriter, r *http.Request, taskID string) {
	var req struct {
		NodeIDs map[string]any    `json:"node_ids"`
		Params  map[string]string `json:"params"`
		All     bool              `json:"all"`
	}
	if err := readJSON(r, &req); err != nil && err != io.EOF {
		writeErr(w, http.StatusBadRequest, "invalid request")
		return
	}

	task, err := s.tasks.Get(r.Context(), taskID)
	if err != nil {
		writeErr(w, http.StatusNotFound, "task not found")
		return
	}

	// 解析目标
	nodeIDs, err := s.resolveTargetNodeIDs(r.Context(), task)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	if len(req.NodeIDs) > 0 {
		var explicit []string
		for k := range req.NodeIDs {
			explicit = append(explicit, k)
		}
		if len(explicit) > 0 {
			nodeIDs = explicit
		}
	}

	if len(nodeIDs) == 0 {
		writeErr(w, http.StatusBadRequest, "no target nodes")
		return
	}

	// 应用 Task 走应用管理器
	if task.Type == models.TaskTypeAppDeploy || task.Type == models.TaskTypeAppOperation {
		op := task.AppOperation
		if op == "" {
			op = "deploy"
		}
		execs, err := s.apps.DeployTask(r.Context(), task, nodeIDs, op)
		if err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		s.audit(r, "task", taskID, "execute")
		writeJSON(w, http.StatusOK, execs)
		return
	}

	execs, err := s.execs.RunManual(r.Context(), task, nodeIDs, req.Params)
	if err != nil {
		writeErr(w, http.StatusBadRequest, err.Error())
		return
	}
	s.audit(r, "task", taskID, "execute")
	writeJSON(w, http.StatusOK, execs)
}

// resolveTargetNodeIDs 解析任务目标
func (s *Server) resolveTargetNodeIDs(ctx context.Context, task *models.Task) ([]string, error) {
	return s.nodes.ResolveTarget(ctx, task.Target)
}

// ---------- Schedules ----------

func (s *Server) handleSchedules(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		list, err := s.schedules.List(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, list)
	case http.MethodPost:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var sc models.Schedule
		if err := readJSON(r, &sc); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if sc.TaskID == "" {
			writeErr(w, http.StatusBadRequest, "task_id required")
			return
		}
		if err := s.schedules.Create(r.Context(), &sc); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, sc)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleScheduleByID(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/schedules/")
	switch r.Method {
	case http.MethodGet:
		sc, err := s.schedules.Get(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusNotFound, "schedule not found")
			return
		}
		writeJSON(w, http.StatusOK, sc)
	case http.MethodPut:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var sc models.Schedule
		if err := readJSON(r, &sc); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		sc.ID = id
		if err := s.schedules.Update(r.Context(), &sc); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, sc)
	case http.MethodDelete:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.schedules.Delete(r.Context(), id); err != nil {
			writeErr(w, http.StatusConflict, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// ---------- Artifacts ----------

func (s *Server) handleArtifacts(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		list, err := s.artifacts.List(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, list)
	case http.MethodPost:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		name := r.FormValue("name")
		version := r.FormValue("version")
		arch := r.FormValue("architecture")
		if arch == "" {
			arch = "amd64"
		}
		if name == "" || version == "" {
			writeErr(w, http.StatusBadRequest, "name and version required")
			return
		}
		file, header, err := r.FormFile("file")
		if err != nil {
			writeErr(w, http.StatusBadRequest, "file required")
			return
		}
		defer file.Close()
		a, err := s.artifacts.Upload(r.Context(), name, version, arch, header.Filename, file)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		s.audit(r, "artifact", a.ID, "upload")
		writeJSON(w, http.StatusOK, a)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleArtifactByID(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/artifacts/")
	parts := strings.Split(path, "/")
	id := parts[0]

	switch r.Method {
	case http.MethodGet:
		a, err := s.artifacts.Get(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusNotFound, "artifact not found")
			return
		}
		writeJSON(w, http.StatusOK, a)
	case http.MethodDelete:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.artifacts.Delete(r.Context(), id); err != nil {
			writeErr(w, http.StatusConflict, err.Error())
			return
		}
		s.audit(r, "artifact", id, "delete")
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// handleArtifactDownload Artifact 下载端点，供 Agent 部署时以 Agent 身份访问
func (s *Server) handleArtifactDownload(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	a, rc, err := s.artifacts.Open(r.Context(), id)
	if err != nil {
		writeErr(w, http.StatusNotFound, "artifact not found")
		return
	}
	defer rc.Close()
	if s.metrics != nil {
		s.metrics.AddArtifactBytes(a.Size)
	}
	if rs, ok := rc.(io.ReadSeeker); ok {
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Content-Length", strconv.FormatInt(a.Size, 10))
		w.Header().Set("X-Artifact-SHA256", a.SHA256)
		http.ServeContent(w, r, a.Filename, a.UploadedAt, rs)
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(a.Size, 10))
	w.Header().Set("X-Artifact-SHA256", a.SHA256)
	io.Copy(w, rc)
}

// ---------- Applications ----------

func (s *Server) handleApplications(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		list, err := s.apps.List(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, list)
	case http.MethodPost:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var a models.Application
		if err := readJSON(r, &a); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if a.Name == "" {
			writeErr(w, http.StatusBadRequest, "name required")
			return
		}
		if err := s.apps.Create(r.Context(), &a); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, a)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleApplicationByID(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/applications/")
	parts := strings.Split(path, "/")
	id := parts[0]

	if len(parts) >= 2 {
		switch parts[1] {
		case "state":
			if r.Method != http.MethodGet {
				writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
				return
			}
			states, err := s.store.ListApplicationNodeStates(r.Context(), id)
			if err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
			writeJSON(w, http.StatusOK, states)
			return
		case "executions":
			if r.Method != http.MethodGet {
				writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
				return
			}
			execs, err := s.applicationExecutions(r.Context(), id)
			if err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
			writeJSON(w, http.StatusOK, execs)
			return
		case "revisions":
			entries, err := s.store.ListApplicationRevisions(r.Context(), id)
			if err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
			writeJSON(w, http.StatusOK, entries)
			return
		case "assign":
			if !s.canWrite(r) {
				writeErr(w, http.StatusForbidden, "permission denied")
				return
			}
			var req struct {
				NodeIDs []string `json:"node_ids"`
				Remove  bool     `json:"remove"`
			}
			if err := readJSON(r, &req); err != nil {
				writeErr(w, http.StatusBadRequest, "invalid request")
				return
			}
			if err := s.apps.Assign(r.Context(), id, req.NodeIDs, !req.Remove); err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
			writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
			return
		case "deploy":
			if !s.canRun(r) {
				writeErr(w, http.StatusForbidden, "permission denied")
				return
			}
			var req struct {
				NodeIDs   []string `json:"node_ids"`
				Operation string   `json:"operation"`
			}
			if err := readJSON(r, &req); err != nil {
				writeErr(w, http.StatusBadRequest, "invalid request")
				return
			}
			if req.Operation == "" {
				req.Operation = "deploy"
			}
			execs, err := s.apps.Deploy(r.Context(), id, req.NodeIDs, req.Operation)
			if err != nil {
				writeErr(w, http.StatusBadRequest, err.Error())
				return
			}
			s.audit(r, "application", id, "deploy")
			writeJSON(w, http.StatusOK, execs)
			return
		case "nodes":
			nodes, err := s.apps.GetNodes(r.Context(), id)
			if err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
			writeJSON(w, http.StatusOK, nodes)
			return
		}
	}

	switch r.Method {
	case http.MethodGet:
		a, err := s.apps.Get(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusNotFound, "application not found")
			return
		}
		writeJSON(w, http.StatusOK, a)
	case http.MethodPut:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var a models.Application
		if err := readJSON(r, &a); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		a.ID = id
		if err := s.apps.Update(r.Context(), &a); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, a)
	case http.MethodDelete:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.apps.Delete(r.Context(), id); err != nil {
			writeErr(w, http.StatusConflict, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) applicationExecutions(ctx context.Context, appID string) ([]*models.Execution, error) {
	tasks, err := s.tasks.List(ctx)
	if err != nil {
		return nil, err
	}
	ids := map[string]bool{appID: true}
	for _, task := range tasks {
		if task.ApplicationID == appID {
			ids[task.ID] = true
		}
	}
	execs, err := s.execs.ListExecutions(ctx, store.ExecutionFilter{Limit: 500})
	if err != nil {
		return nil, err
	}
	out := make([]*models.Execution, 0)
	for _, ex := range execs {
		if ids[ex.TaskID] {
			out = append(out, ex)
		}
	}
	return out, nil
}

// ---------- Executions ----------

func (s *Server) handleExecutions(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		filter := store.ExecutionFilter{
			NodeID: r.URL.Query().Get("node_id"),
			TaskID: r.URL.Query().Get("task_id"),
			Status: r.URL.Query().Get("status"),
		}
		if lim := r.URL.Query().Get("limit"); lim != "" {
			if n, err := strconv.Atoi(lim); err == nil {
				filter.Limit = n
			}
		}
		if off := r.URL.Query().Get("offset"); off != "" {
			if n, err := strconv.Atoi(off); err == nil {
				filter.Offset = n
			}
		}
		list, err := s.execs.ListExecutions(r.Context(), filter)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		// 计数失败不影响列表返回：省略 X-Total-Count 并记录日志。
		if total, err := s.store.CountExecutions(r.Context(), filter); err != nil {
			s.logger.Error("count executions", "error", err)
		} else {
			w.Header().Set("X-Total-Count", strconv.FormatInt(total, 10))
		}
		writeJSON(w, http.StatusOK, list)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleExecutionByID(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/executions/")
	parts := strings.Split(path, "/")
	id := parts[0]

	if len(parts) >= 2 && parts[1] == "logs" {
		if r.Method != http.MethodGet {
			writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		chunks, err := s.execs.ListLogChunks(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, chunks)
		return
	}

	if len(parts) >= 2 && parts[1] == "cancel" {
		if r.Method != http.MethodPost {
			writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		if !s.canRun(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.execs.Cancel(r.Context(), id); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		detail := ""
		if ex, err := s.execs.GetExecution(r.Context(), id); err == nil && ex != nil {
			detail = ex.TaskID
			if t, err := s.tasks.Get(r.Context(), ex.TaskID); err == nil && t != nil {
				detail = t.Name
			}
		}
		s.auditDetail(r, "execution", id, "cancel", detail)
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
		return
	}

	switch r.Method {
	case http.MethodGet:
		ex, err := s.execs.GetExecution(r.Context(), id)
		if err != nil {
			writeErr(w, http.StatusNotFound, "execution not found")
			return
		}
		writeJSON(w, http.StatusOK, ex)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// ---------- Audit ----------

func (s *Server) handleMetrics(w http.ResponseWriter, r *http.Request) {
	if s.metrics == nil {
		writeErr(w, http.StatusNotFound, "metrics not enabled")
		return
	}
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	w.Write([]byte(s.metrics.Render()))
}

func (s *Server) handleAudit(w http.ResponseWriter, r *http.Request) {
	filter := store.AuditFilter{
		UserID: r.URL.Query().Get("user_id"),
		Action: r.URL.Query().Get("action"),
	}
	if lim := r.URL.Query().Get("limit"); lim != "" {
		if n, err := strconv.Atoi(lim); err == nil {
			filter.Limit = n
		}
	}
	if off := r.URL.Query().Get("offset"); off != "" {
		if n, err := strconv.Atoi(off); err == nil {
			filter.Offset = n
		}
	}
	list, err := s.store.ListAudit(r.Context(), filter)
	if err != nil {
		writeErr(w, http.StatusInternalServerError, err.Error())
		return
	}
	// 计数失败不影响列表返回：省略 X-Total-Count 并记录日志。
	if total, err := s.store.CountAudit(r.Context(), filter); err != nil {
		s.logger.Error("count audit", "error", err)
	} else {
		w.Header().Set("X-Total-Count", strconv.FormatInt(total, 10))
	}
	writeJSON(w, http.StatusOK, list)
}

// ---------- Settings ----------

func (s *Server) handleSettings(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		keys := []string{"heartbeat_interval_sec", "revision_check_interval_sec", "changelog_window", "agent_gateway_base_url", "max_log_bytes"}
		out := map[string]string{}
		for _, k := range keys {
			v, err := s.store.GetSetting(r.Context(), k)
			if err == nil {
				out[k] = v
			}
		}
		writeJSON(w, http.StatusOK, out)
	case http.MethodPut:
		if !s.canWrite(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var body map[string]string
		if err := readJSON(r, &body); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if err := validateSettings(body); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		for k, v := range body {
			if err := s.store.SetSetting(r.Context(), k, v); err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
		}
		keys := make([]string, 0, len(body))
		for k := range body {
			keys = append(keys, k)
		}
		if len(keys) > 0 {
			sort.Strings(keys)
			s.auditDetail(r, "setting", "", "update", "keys: "+strings.Join(keys, ","))
		}
		s.notifySettings()
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func validateSettings(values map[string]string) error {
	for key, value := range values {
		switch key {
		case "heartbeat_interval_sec":
			n, err := strconv.Atoi(value)
			if err != nil || n < 1 || n > 86400 {
				return fmt.Errorf("heartbeat_interval_sec must be between 1 and 86400")
			}
		case "revision_check_interval_sec":
			n, err := strconv.Atoi(value)
			if err != nil || n < 5 || n > 86400 {
				return fmt.Errorf("revision_check_interval_sec must be between 5 and 86400")
			}
		case "changelog_window":
			n, err := strconv.ParseInt(value, 10, 64)
			if err != nil || n < 1 || n > 1000000000 {
				return fmt.Errorf("changelog_window must be between 1 and 1000000000")
			}
		case "max_log_bytes":
			n, err := strconv.ParseInt(value, 10, 64)
			if err != nil || n < 1024 || n > 1<<34 {
				return fmt.Errorf("max_log_bytes must be between 1024 and 17179869184")
			}
		case "agent_gateway_base_url":
			u, err := url.Parse(value)
			if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
				return fmt.Errorf("agent_gateway_base_url must be an http or https URL")
			}
		default:
			return fmt.Errorf("unsupported setting: %s", key)
		}
	}
	return nil
}

func (s *Server) notifySettings() {
	if s.sessions == nil {
		return
	}
	p := protocol.SettingsPayload{}
	if v, err := s.store.GetSetting(context.Background(), "heartbeat_interval_sec"); err == nil {
		p.HeartbeatSec, _ = strconv.Atoi(v)
	}
	if v, err := s.store.GetSetting(context.Background(), "revision_check_interval_sec"); err == nil {
		p.RevisionCheckSec, _ = strconv.Atoi(v)
	}
	if v, err := s.store.GetSetting(context.Background(), "max_log_bytes"); err == nil {
		p.MaxLogBytes, _ = strconv.Atoi(v)
	}
	msg := protocol.NewEnvelope(protocol.MsgSettings, "", p)
	for _, nodeID := range s.sessions.ConnectedNodeIDs() {
		s.sessions.Notify(nodeID, msg)
	}
}

// ---------- Users ----------

func (s *Server) handleUsers(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		list, err := s.store.ListUsers(r.Context())
		if err != nil {
			writeErr(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, list)
	case http.MethodPost:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var req struct {
			Username string `json:"username"`
			Password string `json:"password"`
			Role     string `json:"role"`
		}
		if err := readJSON(r, &req); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		hash, err := auth.HashPassword(req.Password)
		if err != nil {
			writeErr(w, http.StatusInternalServerError, "hash failed")
			return
		}
		if req.Role == "" {
			req.Role = models.RoleViewer
		}
		u := &models.User{Username: req.Username, PasswordHash: hash, Role: req.Role}
		if err := s.store.CreateUser(r.Context(), u); err != nil {
			writeErr(w, http.StatusBadRequest, err.Error())
			return
		}
		s.audit(r, "user", u.ID, "create")
		writeJSON(w, http.StatusOK, u)
	default:
		writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleUserByID(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/users/")
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if r.Method == http.MethodPut {
		var req struct {
			Role     string `json:"role"`
			Password string `json:"password"`
		}
		if err := readJSON(r, &req); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if req.Role == "" && req.Password == "" {
			writeErr(w, http.StatusBadRequest, "role or password required")
			return
		}
		if req.Role != "" {
			if err := s.store.UpdateUserRole(r.Context(), id, req.Role); err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
		}
		if req.Password != "" {
			hash, err := auth.HashPassword(req.Password)
			if err != nil {
				writeErr(w, http.StatusInternalServerError, "password hash failed")
				return
			}
			if err := s.store.UpdateUserPassword(r.Context(), id, hash); err != nil {
				writeErr(w, http.StatusInternalServerError, err.Error())
				return
			}
		}
		s.audit(r, "user", id, "update")
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
		return
	}
	writeErr(w, http.StatusMethodNotAllowed, "method not allowed")
}

// ---------- 辅助 ----------

func (s *Server) canWrite(r *http.Request) bool {
	// 定义类写操作仅限 Administrator；Operator 仅可查看/运行/部署/操作应用
	sess := currentUser(r.Context())
	return sess != nil && sess.Role == models.RoleAdministrator
}

func (s *Server) canRun(r *http.Request) bool {
	sess := currentUser(r.Context())
	return sess != nil && (sess.Role == models.RoleAdministrator || sess.Role == models.RoleOperator)
}

func (s *Server) canAdmin(r *http.Request) bool {
	sess := currentUser(r.Context())
	return sess != nil && sess.Role == models.RoleAdministrator
}

func (s *Server) audit(r *http.Request, resource, resourceID, action string) {
	s.auditDetail(r, resource, resourceID, action, "")
}

// auditDetail 写入带 detail 的审计记录
func (s *Server) auditDetail(r *http.Request, resource, resourceID, action, detail string) {
	sess := currentUser(r.Context())
	if sess == nil {
		return
	}
	_ = s.store.AddAudit(r.Context(), &models.AuditLog{
		UserID: sess.UserID, Username: sess.Username,
		Action: action, Resource: resource, ResourceID: resourceID, Detail: detail,
	})
}

// ctxUserKey 当前用户上下文键
type ctxUserKey struct{}

func withUser(ctx context.Context, sess *auth.Session) context.Context {
	return context.WithValue(ctx, ctxUserKey{}, sess)
}

func currentUser(ctx context.Context) *auth.Session {
	if v, ok := ctx.Value(ctxUserKey{}).(*auth.Session); ok {
		return v
	}
	return nil
}

var _ = errors.Is
var _ = time.Now
