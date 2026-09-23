package connection

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
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

// Manager Agent 连接管理器
type Manager struct {
	url        string
	token      string
	agentID    string
	handler    Handler
	logger     *slog.Logger
	conn       *websocket.Conn
	mu         sync.Mutex
	connected  bool
	credential string
	httpClient *http.Client
	sendCh     chan protocol.Envelope
	stop       chan struct{}
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
	m.mu.Lock()
	m.httpClient = &http.Client{Transport: transport}
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

// HTTPClient 返回沿用 Agent TLS CA 配置的 HTTP 客户端。
func (m *Manager) HTTPClient() *http.Client {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.httpClient != nil {
		return m.httpClient
	}
	return http.DefaultClient
}

// New 创建连接管理器
func New(url, token, agentID string, handler Handler, logger *slog.Logger) *Manager {
	return &Manager{
		url: url, token: token, agentID: agentID,
		handler: handler, logger: logger,
		sendCh: make(chan protocol.Envelope, 128),
		stop:   make(chan struct{}),
	}
}

// IsConnected 是否已连接
func (m *Manager) IsConnected() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.connected
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
			m.logger.Warn("connection lost", "error", err)
		}
		m.mu.Lock()
		m.connected = false
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
		case <-time.After(reconnectDelay()):
		}
	}
}

func reconnectDelay() time.Duration {
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
