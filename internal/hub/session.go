package hub

import (
	"context"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// DataPlaneConn 入站会话的数据面 HTTP 客户端能力。
type DataPlaneConn interface {
	AgentConn
	DataPlaneAvailable() bool
	DataPlaneRequest(ctx context.Context, method, path string, body io.Reader, contentLength int64) (*http.Response, error)
}

// AgentConn 抽象 Agent 连接（由 Gateway 实现）
type AgentConn interface {
	// NodeID 当前连接对应节点
	NodeID() string
	// Send 发送消息
	Send(msg protocol.Envelope) error
	// Close 关闭连接
	Close() error
}

// SessionOrigin 返回会话建立方向。实现 Origin() string 的连接（入站拨号）按
// 声明值；现有出站 Gateway 连接默认 outbound。
func SessionOrigin(conn AgentConn) string {
	if conn == nil {
		return models.ConnectionModeOffline
	}
	if origin, ok := conn.(interface{ Origin() string }); ok {
		switch origin.Origin() {
		case models.ConnectionModeInbound:
			return models.ConnectionModeInbound
		case models.ConnectionModeOutbound:
			return models.ConnectionModeOutbound
		}
	}
	return models.ConnectionModeOutbound
}

// SessionIsInbound 当前会话是否由 Hub 主动拨号建立。
func SessionIsInbound(conn AgentConn) bool {
	return SessionOrigin(conn) == models.ConnectionModeInbound
}

// DataPlaneFor 返回可用的入站数据面客户端；出站会话或无数据面能力时 false。
func DataPlaneFor(conn AgentConn) (DataPlaneConn, bool) {
	dp, ok := conn.(DataPlaneConn)
	if !ok || !dp.DataPlaneAvailable() {
		return nil, false
	}
	return dp, true
}

// RequireInboundDataPlane 校验入站会话是否具备数据面；出站会话返回 nil。
func RequireInboundDataPlane(conn AgentConn) (DataPlaneConn, error) {
	if !SessionIsInbound(conn) {
		return nil, nil
	}
	dp, ok := DataPlaneFor(conn)
	if !ok {
		return nil, ErrInboundDataPlaneUnavailable
	}
	return dp, nil
}

// SessionManager 维护 Agent 会话（内存态，非业务唯一来源）
type SessionManager struct {
	mu       sync.RWMutex
	sessions map[string]AgentConn // key: nodeID
}

// NewSessionManager 创建会话管理器
func NewSessionManager() *SessionManager {
	return &SessionManager{sessions: map[string]AgentConn{}}
}

// Register 注册会话
func (sm *SessionManager) Register(conn AgentConn) {
	if !sm.RegisterChecked(conn) {
		_ = conn.Close()
	}
}

// CanRegister 仅检查所有权策略，不改变当前会话；用于在发送 HELLO_ACK 前
// 预判入站会话是否允许注册，保证握手响应先于 NODE_STATUS 等业务消息。
func (sm *SessionManager) CanRegister(conn AgentConn) bool {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	if _, ok := sm.sessions[conn.NodeID()]; !ok {
		return true
	}
	return SessionOrigin(conn) == models.ConnectionModeOutbound
}

// RegisterChecked 按所有权策略注册会话：
//   - 无会话：注册；
//   - 已有会话且新会话为出站：替换旧会话（返回 true，旧连接由调用方关闭）；
//   - 已有会话且新会话为入站：拒绝（返回 false），保留现有出站/入站会话。
//
// 旧连接必须在解锁后关闭，避免 websocket close 阻塞会话表。
func (sm *SessionManager) RegisterChecked(conn AgentConn) bool {
	var retired AgentConn
	sm.mu.Lock()
	if old, ok := sm.sessions[conn.NodeID()]; ok {
		if SessionOrigin(conn) != models.ConnectionModeOutbound {
			sm.mu.Unlock()
			return false
		}
		retired = old
	}
	sm.sessions[conn.NodeID()] = conn
	sm.mu.Unlock()
	if retired != nil && retired != conn {
		_ = retired.Close()
	}
	return true
}

// Unregister 移除会话
func (sm *SessionManager) Unregister(nodeID string) {
	sm.mu.Lock()
	delete(sm.sessions, nodeID)
	sm.mu.Unlock()
}

// Get 获取会话
func (sm *SessionManager) Get(nodeID string) (AgentConn, bool) {
	sm.mu.RLock()
	c, ok := sm.sessions[nodeID]
	sm.mu.RUnlock()
	return c, ok
}

// ConnectedNodeIDs 在线节点列表
func (sm *SessionManager) ConnectedNodeIDs() []string {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	out := make([]string, 0, len(sm.sessions))
	for id := range sm.sessions {
		out = append(out, id)
	}
	return out
}

// Count 在线数
func (sm *SessionManager) Count() int {
	sm.mu.RLock()
	defer sm.mu.RUnlock()
	return len(sm.sessions)
}

// Notify 向节点发送消息
func (sm *SessionManager) Notify(nodeID string, msg protocol.Envelope) bool {
	c, ok := sm.Get(nodeID)
	if !ok {
		return false
	}
	if err := c.Send(msg); err != nil {
		return false
	}
	return true
}

// StartHeartbeatChecker 周期性检测离线
func (sm *SessionManager) StartHeartbeatChecker(ctx context.Context, timeout time.Duration, onTimeout func(nodeID string)) {
	go func() {
		t := time.NewTicker(30 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				now := time.Now()
				sm.mu.RLock()
				var stale []string
				for id, c := range sm.sessions {
					if hc, ok := c.(interface{ LastSeen() time.Time }); ok && now.Sub(hc.LastSeen()) > timeout {
						stale = append(stale, id)
					}
				}
				sm.mu.RUnlock()
				for _, id := range stale {
					if onTimeout != nil {
						onTimeout(id)
					}
					if c, ok := sm.Get(id); ok {
						c.Close()
					}
					sm.Unregister(id)
				}
			}
		}
	}()
}
