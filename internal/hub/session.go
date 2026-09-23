package hub

import (
	"context"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// AgentConn 抽象 Agent 连接（由 Gateway 实现）
type AgentConn interface {
	// NodeID 当前连接对应节点
	NodeID() string
	// Send 发送消息
	Send(msg protocol.Envelope) error
	// Close 关闭连接
	Close() error
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
	sm.mu.Lock()
	if old, ok := sm.sessions[conn.NodeID()]; ok {
		old.Close()
	}
	sm.sessions[conn.NodeID()] = conn
	sm.mu.Unlock()
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
