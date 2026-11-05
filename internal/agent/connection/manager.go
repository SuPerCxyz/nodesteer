package connection

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"os"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

// Handler 消息处理回调
type Handler interface {
	OnHelloAck(msg protocol.Envelope)
	OnHeartbeatAck(msg protocol.Envelope)
	OnChangeNotification(msg protocol.Envelope)
	OnRevisionCheckAck(msg protocol.Envelope)
	OnSyncResponse(msg protocol.Envelope)
	OnSyncAck(msg protocol.Envelope)
	OnRunExecution(msg protocol.Envelope)
	OnCancelExecution(msg protocol.Envelope)
	OnArtifactPrefetch(msg protocol.Envelope)
	OnFileUploadRequest(msg protocol.Envelope)
	OnFileDeliveryRequest(msg protocol.Envelope)
	OnFileTransferCancel(msg protocol.Envelope)
	OnDeployRequest(msg protocol.Envelope)
	OnExecutionAck(msg protocol.Envelope)
	OnSettings(msg protocol.Envelope)
	OnNodeStatus(msg protocol.Envelope)
	OnRemoteState(msg protocol.Envelope)
	OnError(msg protocol.Envelope)
}

// Agent 侧 HTTP 下载（Artifact / 自升级二进制）超时策略：
// 不设超时时，服务端发头后不给 body 会让 io.Copy 永久阻塞，0B .tmp 长期残留
// （缺陷 OBS-20261014-ARTIFACT-DOWNLOAD-NO-READ-TIMEOUT）。
// 选择「总时限 + 响应头超时」而不是纯读空闲超时：Go 标准库没有现成的读空闲超时，
// 而下载体量由 Hub 侧制品决定（应用二进制/Agent 二进制，通常几十~几百 MiB）。
// 15 分钟按 1 MiB/s 保守链路仍可传完约 900 MiB（与文件传输的保守速率下限一致），
// 30s 响应头超时与 Go 默认拨号超时同量级，覆盖「连上但不响应」。
const (
	defaultHTTPTimeout               = 15 * time.Minute
	defaultHTTPResponseHeaderTimeout = 30 * time.Second
)

// newAgentHTTPClient 构造带超时的 Agent 下载客户端；transport 为 nil 时
// 基于 DefaultTransport 克隆并补响应头超时。
func newAgentHTTPClient(transport http.RoundTripper) *http.Client {
	if transport == nil {
		t := http.DefaultTransport.(*http.Transport).Clone()
		t.ResponseHeaderTimeout = defaultHTTPResponseHeaderTimeout
		transport = t
	}
	return &http.Client{Transport: transport, Timeout: defaultHTTPTimeout}
}

// defaultAgentHTTPClient 未配置 TLS CA 时 HTTPClient() 返回的兜底客户端。
var defaultAgentHTTPClient = newAgentHTTPClient(nil)

// Manager Agent 连接管理器：同时管理出站 WebSocket 与可选的入站 TLS listener。
type Manager struct {
	url        string
	token      string
	agentID    string
	nodeID     string
	handler    Handler
	logger     *slog.Logger
	mu         sync.Mutex
	outbound   *hubSession
	inbound    *hubSession
	credential string
	httpClient *http.Client
	stop       chan struct{}
	stopOnce   sync.Once
	// stateWake 唤醒出站循环重新评估自动模式门禁/退避。
	stateWake chan struct{}
	// inboundListener 配置了 bundle 时非 nil；未配置时不监听任何端口。
	inboundListener *InboundListener
	// dataPlaneHandler /agent-data/v1/* 业务处理器（Agent 注入）。
	dataPlaneHandler http.Handler
	// helloFn 在 Run 时注入，入站握手复用同一 HELLO 载荷构造。
	helloFn func() *protocol.HelloPayload
	// logState 共享日志节奏状态机（与 agent 侧 hello rejected/accepted 同一实例）
	logState *ConnectionLogState
	// delayFn 可注入重连退避（测试加速）；nil 用默认 2s+0~2s jitter
	delayFn func() time.Duration
}

// hubSession 单条已建立的 Hub 控制连接（出站或被 Hub 拨号的入站）。
type hubSession struct {
	conn   *websocket.Conn
	origin string
	sendCh chan protocol.Envelope
	done   chan struct{}
	once   sync.Once
	// accepted 表示收到 accepted 的 HELLO_ACK；由 Manager.mu 保护。
	accepted  bool
	sessionID string
	nodeID    string
}

func newHubSession(conn *websocket.Conn, origin string) *hubSession {
	return &hubSession{
		conn: conn, origin: origin,
		sendCh: make(chan protocol.Envelope, 128),
		done:   make(chan struct{}),
	}
}

func (s *hubSession) send(env protocol.Envelope) bool {
	select {
	case <-s.done:
		return false
	default:
	}
	select {
	case s.sendCh <- env:
		return true
	case <-s.done:
		return false
	}
}

func (s *hubSession) sendSync(ctx context.Context, env protocol.Envelope) error {
	return wsjson.Write(ctx, s.conn, env)
}

func (s *hubSession) close() {
	s.once.Do(func() {
		close(s.done)
		_ = s.conn.Close(websocket.StatusNormalClosure, "close")
	})
}

// SetTLSCA 使用指定 CA 验证 wss:// Hub 证书。
func (m *Manager) SetTLSCA(caFile string) error {
	if caFile == "" {
		return nil
	}
	data, err := os.ReadFile(caFile)
	if err != nil {
		return err
	}
	pool, err := x509.SystemCertPool()
	if err != nil || pool == nil {
		pool = x509.NewCertPool()
	}
	if !pool.AppendCertsFromPEM(data) {
		return fmt.Errorf("no certificates found in %s", caFile)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}
	transport.ResponseHeaderTimeout = defaultHTTPResponseHeaderTimeout
	m.mu.Lock()
	m.httpClient = newAgentHTTPClient(transport)
	m.mu.Unlock()
	return nil
}

// SetAgentID 设置持久化 Agent ID，供重连 HELLO 使用。
func (m *Manager) SetAgentID(id string) {
	m.mu.Lock()
	m.agentID = id
	m.mu.Unlock()
}

// SetNodeID 设置已确认的 Node ID，用于入站 HUB_HELLO 身份校验。
func (m *Manager) SetNodeID(id string) {
	m.mu.Lock()
	m.nodeID = id
	m.mu.Unlock()
}

// SetCredential 设置 Hub 返回的稳定凭证。
func (m *Manager) SetCredential(credential string) {
	m.mu.Lock()
	m.credential = credential
	m.mu.Unlock()
}

// HTTPClient 返回沿用 Agent TLS CA 配置的 HTTP 客户端（均带下载超时，
// 见 defaultHTTPTimeout/defaultHTTPResponseHeaderTimeout）。
func (m *Manager) HTTPClient() *http.Client {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.httpClient != nil {
		return m.httpClient
	}
	return defaultAgentHTTPClient
}

// New 创建连接管理器。logState 为共享日志节奏状态机，nil 时内部兜底创建
// （保证失败打点与节流始终可用）。
func New(url, token, agentID string, handler Handler, logger *slog.Logger, logState *ConnectionLogState) *Manager {
	if logState == nil {
		logState = NewConnectionLogState()
	}
	if logger == nil {
		logger = slog.Default()
	}
	return &Manager{
		url: url, token: token, agentID: agentID,
		handler: handler, logger: logger,
		stop:      make(chan struct{}),
		stateWake: make(chan struct{}, 1),
		logState:  logState,
	}
}

// IsConnected 是否至少有一条传输连接（Dial/TLS 成功即 true，早于 HELLO 往返）。
func (m *Manager) IsConnected() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.outbound != nil || m.inbound != nil
}

// HandshakeComplete 是否至少有一条连接被 Hub 接受（HELLO_ACK accepted）。
// 这是唯一可作「Hub 在线可用」依据的标志：传输层连接早于 HELLO 发送。
func (m *Manager) HandshakeComplete() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.acceptedLocked(m.outbound) || m.acceptedLocked(m.inbound)
}

func (m *Manager) acceptedLocked(session *hubSession) bool {
	return session != nil && session.accepted
}

// ConnectionOrigin 返回当前优先会话的建立方向；无会话时返回空。
func (m *Manager) ConnectionOrigin() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	if session := m.preferredSessionLocked(); session != nil {
		return session.origin
	}
	return ""
}

// helloAckAccepted 解析 HELLO_ACK 是否 accepted；解析失败按未接受处理（Fail Closed）。
func helloAckAccepted(payload []byte) bool {
	var p protocol.HelloAckPayload
	if err := json.Unmarshal(payload, &p); err != nil {
		return false
	}
	return p.Accepted
}

// Stop 停止出站循环与入站 listener，并关闭所有会话。
func (m *Manager) Stop() {
	m.stopOnce.Do(func() { close(m.stop) })
	if m.inboundListener != nil {
		m.inboundListener.Close()
	}
	m.mu.Lock()
	out, in := m.outbound, m.inbound
	m.mu.Unlock()
	if out != nil {
		out.close()
	}
	if in != nil && in != out {
		in.close()
	}
}

// Send 优先走已认证的出站会话，其次入站会话；未握手时优先出站连接。
func (m *Manager) Send(env protocol.Envelope) bool {
	m.mu.Lock()
	session := m.preferredSessionLocked()
	m.mu.Unlock()
	if session == nil {
		return false
	}
	return session.send(env)
}

func (m *Manager) preferredSessionLocked() *hubSession {
	if m.acceptedLocked(m.outbound) {
		return m.outbound
	}
	if m.acceptedLocked(m.inbound) {
		return m.inbound
	}
	if m.outbound != nil {
		return m.outbound
	}
	return m.inbound
}

// Run 启动入站 listener（如已配置）与出站连接循环（阻塞）。
func (m *Manager) Run(ctx context.Context, helloFn func() *protocol.HelloPayload) {
	m.mu.Lock()
	m.helloFn = helloFn
	m.mu.Unlock()
	if m.inboundListener != nil {
		go m.inboundListener.Serve(ctx)
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-m.stop:
			return
		default:
		}
		if !m.waitOutboundAllowed(ctx) {
			return
		}
		if err := m.connectAndServe(ctx, helloFn); err != nil {
			if !m.shuttingDown(ctx) &&
				!errors.Is(err, context.Canceled) && !errors.Is(err, context.DeadlineExceeded) {
				m.logFailure(err)
			}
		}
		if !m.waitReconnect(ctx) {
			return
		}
	}
}

// waitOutboundAllowed 入站已认证会话存活期间暂停出站拨号，避免双连接抖动。
func (m *Manager) waitOutboundAllowed(ctx context.Context) bool {
	for {
		m.mu.Lock()
		paused := m.acceptedLocked(m.inbound)
		m.mu.Unlock()
		if !paused {
			return true
		}
		select {
		case <-ctx.Done():
			return false
		case <-m.stop:
			return false
		case <-m.stateWake:
		}
	}
}

// waitReconnect 出站断开后的退避；入站状态变化会提前唤醒重新评估。
func (m *Manager) waitReconnect(ctx context.Context) bool {
	timer := time.NewTimer(m.reconnectDelay())
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-m.stop:
		return false
	case <-timer.C:
		return true
	case <-m.stateWake:
		return true
	}
}

// signalState 非阻塞唤醒出站循环。
func (m *Manager) signalState() {
	select {
	case m.stateWake <- struct{}{}:
	default:
	}
}

// shuttingDown 判定当前返回是否由 ctx 取消或 Stop 触发（正常停机，不进入失败态）。
func (m *Manager) shuttingDown(ctx context.Context) bool {
	if ctx.Err() != nil {
		return true
	}
	select {
	case <-m.stop:
		return true
	default:
		return false
	}
}

// logFailure 失败打点：汇入共享节流状态机（首错立即、分钟/小时级限频、
// 跨分钟错误变化放行），放行时输出 ERROR（替代原每 2–4s 的 WARN 刷屏）。
func (m *Manager) logFailure(err error) {
	snap, ok := m.logState.RecordFailure(err.Error(), time.Now())
	if !ok {
		return
	}
	m.logger.Error("connection lost",
		"error", snap.Error,
		"fail_duration", snap.FailDuration,
		"failures_this_minute", snap.FailuresThisMinute,
		"failures_total", snap.FailuresTotal)
}

// reconnectDelay 重连退避间隔；delayFn 可注入（测试加速），默认 2s+0~2s jitter。
func (m *Manager) reconnectDelay() time.Duration {
	if m.delayFn != nil {
		return m.delayFn()
	}
	return defaultReconnectDelay()
}

func defaultReconnectDelay() time.Duration {
	// 基础 2s + 0~2s 随机抖动，避免多 Agent 同时重连
	return 2*time.Second + time.Duration(rand.Intn(2000))*time.Millisecond
}

func (m *Manager) connectAndServe(ctx context.Context, helloFn func() *protocol.HelloPayload) error {
	if !m.waitOutboundAllowed(ctx) {
		return ctx.Err()
	}
	m.mu.Lock()
	client := m.httpClient
	m.mu.Unlock()
	conn, _, err := websocket.Dial(ctx, m.url, &websocket.DialOptions{
		HTTPClient:   client,
		Subprotocols: []string{"nodesteer"},
	})
	if err != nil {
		return err
	}
	// 与网关读取限制保持一致，避免接收大消息时被默认限制断开
	conn.SetReadLimit(8 << 20)
	session := newHubSession(conn, models.ConnectionModeOutbound)
	m.mu.Lock()
	m.outbound = session
	m.mu.Unlock()
	defer func() {
		session.close()
		m.removeSession(session)
	}()

	// 发送 HELLO
	hello := helloFn()
	m.mu.Lock()
	agentID, credential := m.agentID, m.credential
	m.mu.Unlock()
	hello.AgentID = agentID
	if credential != "" {
		hello.AgentCredential = credential
	} else if m.token != "" {
		hello.RegistrationKey = m.token
	}
	if err := session.sendSync(ctx, protocol.NewEnvelope(protocol.MsgHello, "", hello)); err != nil {
		return err
	}
	return m.serveSession(ctx, session)
}

// serveSession 写/读循环与消息分发；出站和入站会话共用。
func (m *Manager) serveSession(ctx context.Context, session *hubSession) error {
	writeCtx, writeCancel := context.WithCancel(ctx)
	defer writeCancel()
	go m.writeLoop(writeCtx, session)
	defer session.close()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-m.stop:
			return nil
		default:
		}
		_, data, err := session.conn.Read(ctx)
		if err != nil {
			return err
		}
		var env protocol.Envelope
		if err := json.Unmarshal(data, &env); err != nil {
			continue
		}
		m.dispatch(session, env)
	}
}

func (m *Manager) writeLoop(ctx context.Context, session *hubSession) {
	for {
		select {
		case <-ctx.Done():
			return
		case <-session.done:
			return
		case env := <-session.sendCh:
			if err := session.sendSync(ctx, env); err != nil {
				session.close()
				return
			}
		}
	}
}

// removeSession 清理会话并唤醒出站循环重新评估自动模式。
func (m *Manager) removeSession(session *hubSession) {
	inboundChanged := false
	m.mu.Lock()
	if m.outbound == session {
		m.outbound = nil
	}
	if m.inbound == session {
		m.inbound = nil
		inboundChanged = true
	}
	m.mu.Unlock()
	session.close()
	if inboundChanged {
		m.signalState()
	}
}

// acceptSession 标记 HELLO_ACK accepted。出站优先：出站接受后关闭入站会话；
// 入站接受在已有出站已接受会话时拒绝（防御性，正常由 Hub 所有权策略拦截）。
func (m *Manager) acceptSession(session *hubSession) bool {
	var closeSession *hubSession
	notify := false
	m.mu.Lock()
	if session.origin == models.ConnectionModeInbound && m.acceptedLocked(m.outbound) {
		m.mu.Unlock()
		session.close()
		return false
	}
	session.accepted = true
	if session.origin == models.ConnectionModeOutbound && m.inbound != nil && m.inbound != session {
		closeSession = m.inbound
	}
	if session.origin == models.ConnectionModeInbound {
		notify = true
	}
	m.mu.Unlock()
	if closeSession != nil {
		closeSession.close()
	}
	if notify {
		m.signalState()
	}
	return true
}

func (m *Manager) dispatch(session *hubSession, env protocol.Envelope) {
	if env.Type == protocol.MsgHelloAck {
		if helloAckAccepted(env.Payload) {
			m.acceptSession(session)
		}
	}
	if m.handler == nil {
		return
	}
	switch env.Type {
	case protocol.MsgHelloAck:
		m.handler.OnHelloAck(env)
	case protocol.MsgHeartbeatAck:
		m.handler.OnHeartbeatAck(env)
	case protocol.MsgChangeNotif:
		m.handler.OnChangeNotification(env)
	case protocol.MsgRevisionCheckAck:
		m.handler.OnRevisionCheckAck(env)
	case protocol.MsgSyncResponse:
		m.handler.OnSyncResponse(env)
	case protocol.MsgSyncAck:
		m.handler.OnSyncAck(env)
	case protocol.MsgRunExecution:
		m.handler.OnRunExecution(env)
	case protocol.MsgMonitorProbe:
		if !m.HandshakeComplete() {
			return
		}
		if handler, ok := m.handler.(interface{ OnMonitorProbe(protocol.Envelope) }); ok {
			handler.OnMonitorProbe(env)
		}
	case protocol.MsgCancelExecution:
		m.handler.OnCancelExecution(env)
	case protocol.MsgArtifactPrefetch:
		m.handler.OnArtifactPrefetch(env)
	case protocol.MsgFileUploadRequest:
		m.handler.OnFileUploadRequest(env)
	case protocol.MsgFileDeliveryRequest:
		m.handler.OnFileDeliveryRequest(env)
	case protocol.MsgFileTransferCancel:
		m.handler.OnFileTransferCancel(env)
	case protocol.MsgDeployRequest:
		m.handler.OnDeployRequest(env)
	case protocol.MsgExecutionAck:
		m.handler.OnExecutionAck(env)
	case protocol.MsgSettings:
		m.handler.OnSettings(env)
	case protocol.MsgNodeStatus:
		m.handler.OnNodeStatus(env)
	case protocol.MsgRemoteState:
		m.handler.OnRemoteState(env)
	case protocol.MsgError:
		m.handler.OnError(env)
	}
}
