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

// Manager Agent 连接管理器
type Manager struct {
	url       string
	token     string
	agentID   string
	handler   Handler
	logger    *slog.Logger
	conn      *websocket.Conn
	mu        sync.Mutex
	connected bool
	// handshake 协议级握手完成标志：仅在收到 accepted 的 HELLO_ACK 后为 true，
	// 建立传输连接/断开时复位。transport 层 connected 早于 HELLO 往返，
	// 不能作为「Hub 已接受本 Agent」的依据（缺陷 OBS-20261016-G23-RECONNECT-WINDOW）。
	handshake  bool
	credential string
	httpClient *http.Client
	sendCh     chan protocol.Envelope
	stop       chan struct{}
	// logState 共享日志节奏状态机（与 agent 侧 hello rejected/accepted 同一实例）
	logState *ConnectionLogState
	// delayFn 可注入重连退避（测试加速）；nil 用默认 2s+0~2s jitter
	delayFn func() time.Duration
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
	return &Manager{
		url: url, token: token, agentID: agentID,
		handler: handler, logger: logger,
		sendCh:   make(chan protocol.Envelope, 128),
		stop:     make(chan struct{}),
		logState: logState,
	}
}

// IsConnected 传输层是否已连接（Dial 成功即为 true，早于 HELLO 往返）。
func (m *Manager) IsConnected() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.connected
}

// HandshakeComplete Hub 是否已接受本次连接（HELLO_ACK accepted）。
// 这是唯一可作「Hub 在线可用」依据的标志：传输层 connected 早于 HELLO 发送，
// 在其上放行 hub_online_required 调度会落在重连窗口内。
func (m *Manager) HandshakeComplete() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.handshake
}

// markHandshake 由 dispatch 在收到 HELLO_ACK 时调用（仅 accepted 置真）。
func (m *Manager) markHandshake(accepted bool) {
	if !accepted {
		return
	}
	m.mu.Lock()
	m.handshake = true
	m.mu.Unlock()
}

// helloAckAccepted 解析 HELLO_ACK 是否 accepted；解析失败按未接受处理（Fail Closed）。
func helloAckAccepted(payload []byte) bool {
	var p protocol.HelloAckPayload
	if err := json.Unmarshal(payload, &p); err != nil {
		return false
	}
	return p.Accepted
}

// Stop 停止
func (m *Manager) Stop() {
	close(m.stop)
	m.mu.Lock()
	if m.conn != nil {
		m.conn.Close(websocket.StatusNormalClosure, "stopping")
	}
	m.mu.Unlock()
}

// Send 发送消息（已连接时）
func (m *Manager) Send(env protocol.Envelope) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	if !m.connected {
		return false
	}
	select {
	case m.sendCh <- env:
		return true
	default:
		return false
	}
}

// Run 启动连接循环（阻塞）
func (m *Manager) Run(ctx context.Context, helloFn func() *protocol.HelloPayload) {
	for {
		select {
		case <-ctx.Done():
			return
		case <-m.stop:
			return
		default:
		}
		if err := m.connectAndServe(ctx, helloFn); err != nil {
			// 正常停机（ctx 取消 / Stop 触发）不是连接失败：不打失败日志、不进入失败态。
			if !m.shuttingDown(ctx) &&
				!errors.Is(err, context.Canceled) && !errors.Is(err, context.DeadlineExceeded) {
				m.logFailure(err)
			}
		}
		m.mu.Lock()
		m.connected = false
		m.handshake = false
		if m.conn != nil {
			m.conn.Close(websocket.StatusAbnormalClosure, "reconnect")
			m.conn = nil
		}
		m.mu.Unlock()
		// 退避重连
		select {
		case <-ctx.Done():
			return
		case <-m.stop:
			return
		case <-time.After(m.reconnectDelay()):
		}
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
	m.mu.Lock()
	m.conn = conn
	m.connected = true
	// 新连接一律从未握手态开始：HELLO 往返完成并被 accepted 前，
	// 不得作为 Hub 在线依据。
	m.handshake = false
	m.mu.Unlock()

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
	if err := m.sendRaw(ctx, protocol.NewEnvelope(protocol.MsgHello, "", hello)); err != nil {
		return err
	}

	// 写循环
	writeCtx, writeCancel := context.WithCancel(ctx)
	defer writeCancel()
	go m.writeLoop(writeCtx)

	// 读循环
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-m.stop:
			return nil
		default:
		}
		_, data, err := conn.Read(ctx)
		if err != nil {
			return err
		}
		var env protocol.Envelope
		if err := json.Unmarshal(data, &env); err != nil {
			continue
		}
		m.dispatch(env)
	}
}

func (m *Manager) writeLoop(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case env := <-m.sendCh:
			if err := m.sendRaw(ctx, env); err != nil {
				return
			}
		}
	}
}

func (m *Manager) sendRaw(ctx context.Context, env protocol.Envelope) error {
	conn := m.currentConn()
	if conn == nil {
		return nil
	}
	return wsjson.Write(ctx, conn, env)
}

func (m *Manager) currentConn() *websocket.Conn {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.conn
}

func (m *Manager) dispatch(env protocol.Envelope) {
	// 握手标志先于业务 handler 处理：即使 handler 缺失也必须正确反映协议状态。
	if env.Type == protocol.MsgHelloAck {
		m.markHandshake(helloAckAccepted(env.Payload))
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
