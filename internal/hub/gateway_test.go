package hub

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"testing"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/protocol"
	"github.com/cadentra/cadentra/internal/store"
)

type fakeAgentConn struct {
	nodeID string
	closed bool
}

func (f *fakeAgentConn) NodeID() string { return f.nodeID }
func (f *fakeAgentConn) Send(msg protocol.Envelope) error {
	return nil
}
func (f *fakeAgentConn) Close() error { f.closed = true; return nil }

func newTestGateway(t *testing.T) (*Gateway, *NodeManager, *SessionManager) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	revisions := NewRevisionManager(st)
	nm := NewNodeManager(st, revisions)
	sm := NewSessionManager()
	nm.SetSessionManager(sm)
	g := NewGateway(GatewayConfig{}, nm, sm, nil, nil, slog.New(slog.DiscardHandler))
	return g, nm, sm
}

// TestDeleteNodeDropsAgentSession 验证删除节点会立即回收其 Agent 会话。
// 此前删除节点仅删库，会话仍存活并持续上报，导致每个周期重复 dispatch error。
func TestDeleteNodeDropsAgentSession(t *testing.T) {
	ctx := context.Background()
	_, nm, sm := newTestGateway(t)

	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-del", "host-del", "10.0.0.9", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	conn := &fakeAgentConn{nodeID: node.ID}
	sm.Register(conn)

	if err := nm.Delete(ctx, node.ID); err != nil {
		t.Fatalf("delete node: %v", err)
	}
	if _, ok := sm.Get(node.ID); ok {
		t.Fatalf("session should be unregistered after node delete")
	}
	if !conn.closed {
		t.Fatalf("agent connection should be closed after node delete")
	}
}

// TestDropOrphanSession 验证兜底逻辑：节点记录已不存在时，
// 查询返回 no-rows 会回收遗留会话，而不是每个周期重复报错。
func TestDropOrphanSession(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)

	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-orphan", "host-o", "10.0.0.10", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	sm.Register(&fakeAgentConn{nodeID: node.ID})

	// 节点仍存在：真实业务错误不应被当作孤儿会话处理
	if g.dropOrphanSession(ctx, node.ID, protocol.MsgSyncRequest, sql.ErrNoRows) {
		t.Fatalf("should not drop session while node still exists")
	}
	if _, ok := sm.Get(node.ID); !ok {
		t.Fatalf("session should remain while node exists")
	}
	// 非 no-rows 错误也不应触发回收
	if g.dropOrphanSession(ctx, node.ID, protocol.MsgSyncRequest, fmt.Errorf("other")) {
		t.Fatalf("should not drop session for unrelated error")
	}

	// 直接删库模拟遗留会话（绕过 NodeManager.Delete 的会话回收）
	if err := nm.store.DeleteNode(ctx, node.ID); err != nil {
		t.Fatalf("delete node row: %v", err)
	}
	if !g.dropOrphanSession(ctx, node.ID, protocol.MsgSyncRequest, sql.ErrNoRows) {
		t.Fatalf("expected orphan session to be dropped")
	}
	if _, ok := sm.Get(node.ID); ok {
		t.Fatalf("orphan session should be unregistered")
	}
}

// TestMarkDisconnectedToleratesDeletedNode 验证删除节点后连接退出
// 不再产生 "mark offline on disconnect failed" 噪音告警。
func TestMarkDisconnectedToleratesDeletedNode(t *testing.T) {
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)

	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-gone", "host-g", "10.0.0.11", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	if err := nm.store.DeleteNode(ctx, node.ID); err != nil {
		t.Fatalf("delete node row: %v", err)
	}
	if err := g.markDisconnected(ctx, node.ID); err != nil {
		t.Fatalf("markDisconnected should tolerate deleted node, got %v", err)
	}
}

// TestHandleDisconnectMarksOffline 验证 Bug F：Agent 连接断开后
// 节点状态应被标记为 offline（此前仅 Unregister 会话，状态滞留 online）。
func TestHandleDisconnectMarksOffline(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)

	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-1", "host-a", "10.0.0.1", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	if node.Status != models.NodeStatusOnline {
		t.Fatalf("expect online after register, got %s", node.Status)
	}

	conn := &fakeAgentConn{nodeID: node.ID}
	sm.Register(conn)
	g.handleDisconnect(node.ID, conn)

	got, err := nm.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.Status != models.NodeStatusOffline {
		t.Fatalf("expect offline after disconnect, got %s", got.Status)
	}
	if _, ok := sm.Get(node.ID); ok {
		t.Fatalf("session should be unregistered after disconnect")
	}
}

// TestHandleDisconnectSkipsActiveReconnect 验证重连竞态：旧连接断开时
// 若已有新连接替换为当前活跃会话，不得把节点误标为 offline。
func TestHandleDisconnectSkipsActiveReconnect(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)

	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-2", "host-b", "10.0.0.2", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatalf("register: %v", err)
	}

	oldConn := &fakeAgentConn{nodeID: node.ID}
	sm.Register(oldConn)
	// 新连接建立（SessionManager 注册时旧连接被替换）
	newConn := &fakeAgentConn{nodeID: node.ID}
	sm.Register(newConn)

	// 旧连接断开，但当前活跃会话已是新连接
	g.handleDisconnect(node.ID, oldConn)

	got, err := nm.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.Status != models.NodeStatusOnline {
		t.Fatalf("expect node stays online with active session, got %s", got.Status)
	}
	if _, ok := sm.Get(node.ID); !ok {
		t.Fatalf("new session should remain registered")
	}
}
