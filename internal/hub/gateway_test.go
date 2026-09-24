package hub

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/coder/websocket"
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
	// syncMgr 与 registration token：handleHello accepted 路径要读 revisions 并校验注册令牌
	g := NewGateway(GatewayConfig{RegistrationToken: "test-token"}, nm, sm, NewSyncManager(st, revisions, sm, nm), nil, slog.New(slog.DiscardHandler))
	return g, nm, sm
}

// newHelloTestConn 建立真实 WebSocket 连接对，返回服务端侧 wsConn，
// 供 handleHello 直接驱动（拒绝分支的 sendSync 需要真实连接写回 ack）。
func newHelloTestConn(t *testing.T, g *Gateway) *wsConn {
	t.Helper()
	peer := make(chan *websocket.Conn, 1)
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		peer <- c
		// Accept 之后不得使用 r.Context()，用独立 context 保持读循环至连接关闭。
		readCtx, cancel := context.WithCancel(context.Background())
		defer cancel()
		for {
			if _, _, err := c.Read(readCtx); err != nil {
				return
			}
		}
	}))
	t.Cleanup(ts.Close)
	client, _, err := websocket.Dial(context.Background(), "ws://"+ts.Listener.Addr().String(), nil)
	if err != nil {
		t.Fatalf("dial websocket: %v", err)
	}
	var server *websocket.Conn
	select {
	case server = <-peer:
	case <-time.After(5 * time.Second):
		_ = client.CloseNow()
		t.Fatal("websocket accept timeout")
	}
	t.Cleanup(func() {
		// CloseNow：测试收尾不做 close handshake，避免最长 5s 阻塞。
		_ = server.CloseNow()
		_ = client.CloseNow()
	})
	return &wsConn{
		c:       server,
		meta:    &connMeta{lastSeen: time.Now()},
		gateway: g,
		sendCh:  make(chan protocol.Envelope, 16),
		done:    make(chan struct{}),
	}
}

// drainHelloAck 取出 accepted 路径经 sendCh 异步写出的 HELLO ack 并校验。
func drainHelloAck(t *testing.T, conn *wsConn) protocol.HelloAckPayload {
	t.Helper()
	select {
	case msg := <-conn.sendCh:
		if msg.Type != protocol.MsgHelloAck {
			t.Fatalf("expected HELLO_ACK, got %s", msg.Type)
		}
		var ack protocol.HelloAckPayload
		if err := json.Unmarshal(msg.Payload, &ack); err != nil {
			t.Fatalf("decode ack: %v", err)
		}
		if !ack.Accepted {
			t.Fatalf("expected accepted ack, got message %q", ack.Message)
		}
		return ack
	case <-time.After(2 * time.Second):
		t.Fatal("expected HELLO ack on send channel")
		return protocol.HelloAckPayload{}
	}
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

// TestHelloRejectedInvalidCredentialDemotesOnline 验证拒绝分支防滞留：
// invalid credential 可定位到节点且其当前 online → 拒绝返回前置 offline。
func TestHelloRejectedInvalidCredentialDemotesOnline(t *testing.T) {
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)
	node, cred, _, err := nm.RegisterOrUpdate(ctx, "agent-reject", "host-reject", "10.0.0.81", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	if node.Status != models.NodeStatusOnline {
		t.Fatalf("setup should leave node online, got %s", node.Status)
	}
	conn := newHelloTestConn(t, g)
	env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		AgentID:         node.AgentID,
		AgentCredential: cred + "-wrong",
	})
	if err := g.handleHello(ctx, conn, env); err == nil || err.Error() != "invalid credential" {
		t.Fatalf("expected invalid credential rejection, got %v", err)
	}
	got, err := nm.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.Status != models.NodeStatusOffline {
		t.Fatalf("rejected HELLO must demote online node to offline before closing, got %s", got.Status)
	}
}

// TestHelloRejectedKeepsPendingNode 验证从未成功会话的新节点（pending）被拒后保持 pending。
func TestHelloRejectedKeepsPendingNode(t *testing.T) {
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)
	pending, err := nm.PrepareEnrollment(ctx, "pending-reject", "10.0.0.82", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("prepare enrollment: %v", err)
	}
	if pending.Status != models.NodeStatusPending {
		t.Fatalf("enrollment should create pending node, got %s", pending.Status)
	}
	conn := newHelloTestConn(t, g)
	env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		AgentID:         pending.AgentID,
		AgentCredential: "wrong-credential",
	})
	if err := g.handleHello(ctx, conn, env); err == nil {
		t.Fatal("expected rejection for wrong credential")
	}
	got, err := nm.GetNode(ctx, pending.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.Status != models.NodeStatusPending {
		t.Fatalf("never-connected pending node must stay pending, got %s", got.Status)
	}
}

// TestHelloAcceptedCredentialMarksOnline 验证 credential 路径 accepted 后显式置 online。
func TestHelloAcceptedCredentialMarksOnline(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)
	node, cred, _, err := nm.RegisterOrUpdate(ctx, "agent-accept", "host-accept", "10.0.0.83", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	// 复位为 pending：模拟纳管后从未接入的节点首次凭 credential 接入
	if err := nm.SetNodeStatus(ctx, node.ID, models.NodeStatusPending); err != nil {
		t.Fatalf("reset to pending: %v", err)
	}
	conn := newHelloTestConn(t, g)
	env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		AgentID:         node.AgentID,
		AgentCredential: cred,
	})
	if err := g.handleHello(ctx, conn, env); err != nil {
		t.Fatalf("expected accepted HELLO, got %v", err)
	}
	ack := drainHelloAck(t, conn)
	if ack.NodeID != node.ID {
		t.Fatalf("ack node id = %q, want %q", ack.NodeID, node.ID)
	}
	got, err := nm.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.Status != models.NodeStatusOnline {
		t.Fatalf("accepted credential HELLO must set node online, got %s", got.Status)
	}
	if _, ok := sm.Get(node.ID); !ok {
		t.Fatal("accepted HELLO should register session")
	}
}

// TestHelloAcceptedRegistrationMarksOnline 验证注册路径 accepted 后置 online。
func TestHelloAcceptedRegistrationMarksOnline(t *testing.T) {
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)
	conn := newHelloTestConn(t, g)
	env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		RegistrationKey: "test-token",
		Hostname:        "fresh-node",
	})
	if err := g.handleHello(ctx, conn, env); err != nil {
		t.Fatalf("expected accepted registration HELLO, got %v", err)
	}
	ack := drainHelloAck(t, conn)
	if ack.NodeID == "" {
		t.Fatal("expected node id in ack")
	}
	got, err := nm.GetNode(ctx, ack.NodeID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.Status != models.NodeStatusOnline {
		t.Fatalf("accepted registration HELLO must set node online, got %s", got.Status)
	}
}

// TestHelloAcceptedPreservesMaintenance 验证 maintenance 节点 accepted 后保持 maintenance，
// credential 与注册两条路径均不得覆盖人工设置状态。
func TestHelloAcceptedPreservesMaintenance(t *testing.T) {
	ctx := context.Background()

	t.Run("credential path", func(t *testing.T) {
		g, nm, _ := newTestGateway(t)
		node, cred, _, err := nm.RegisterOrUpdate(ctx, "agent-maint-c", "host-maint-c", "10.0.0.84", "linux", "amd64", "1.0", "native", false, nil)
		if err != nil {
			t.Fatalf("register: %v", err)
		}
		if err := nm.SetNodeStatus(ctx, node.ID, models.NodeStatusMaintenance); err != nil {
			t.Fatalf("set maintenance: %v", err)
		}
		conn := newHelloTestConn(t, g)
		env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
			ProtocolVersion: protocol.ProtocolVersion,
			AgentID:         node.AgentID,
			AgentCredential: cred,
		})
		if err := g.handleHello(ctx, conn, env); err != nil {
			t.Fatalf("expected accepted HELLO, got %v", err)
		}
		drainHelloAck(t, conn)
		got, err := nm.GetNode(ctx, node.ID)
		if err != nil {
			t.Fatalf("get node: %v", err)
		}
		if got.Status != models.NodeStatusMaintenance {
			t.Fatalf("maintenance must survive accepted HELLO, got %s", got.Status)
		}
	})

	t.Run("registration path", func(t *testing.T) {
		g, nm, _ := newTestGateway(t)
		node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-maint-r", "host-maint-r", "10.0.0.85", "linux", "amd64", "1.0", "native", false, nil)
		if err != nil {
			t.Fatalf("register: %v", err)
		}
		if err := nm.SetNodeStatus(ctx, node.ID, models.NodeStatusMaintenance); err != nil {
			t.Fatalf("set maintenance: %v", err)
		}
		conn := newHelloTestConn(t, g)
		env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
			ProtocolVersion: protocol.ProtocolVersion,
			RegistrationKey: "test-token",
			AgentID:         node.AgentID,
			Hostname:        "host-maint-r",
		})
		if err := g.handleHello(ctx, conn, env); err != nil {
			t.Fatalf("expected accepted HELLO, got %v", err)
		}
		drainHelloAck(t, conn)
		got, err := nm.GetNode(ctx, node.ID)
		if err != nil {
			t.Fatalf("get node: %v", err)
		}
		if got.Status != models.NodeStatusMaintenance {
			t.Fatalf("maintenance must survive accepted registration HELLO, got %s", got.Status)
		}
	})
}

// TestHelloRejectedInvalidTokenDemotesOnline 验证 token 拒绝分支同样防滞留（裁决 1）：
// 经 HELLO 携带的预分配 agent_id 可定位到既有节点且其 online → 拒绝返回前置 offline；
// pending 节点保持 pending；agent_id 查无行时不写。
func TestHelloRejectedInvalidTokenDemotesOnline(t *testing.T) {
	ctx := context.Background()

	t.Run("locatable online node demoted", func(t *testing.T) {
		g, nm, _ := newTestGateway(t)
		node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-token-rej", "host-token-rej", "10.0.0.87", "linux", "amd64", "1.0", "native", false, nil)
		if err != nil {
			t.Fatalf("register: %v", err)
		}
		conn := newHelloTestConn(t, g)
		env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
			ProtocolVersion: protocol.ProtocolVersion,
			RegistrationKey: "wrong-token",
			AgentID:         node.AgentID,
			Hostname:        "host-token-rej",
		})
		if err := g.handleHello(ctx, conn, env); err == nil || err.Error() != "invalid registration token" {
			t.Fatalf("expected invalid registration token rejection, got %v", err)
		}
		got, err := nm.GetNode(ctx, node.ID)
		if err != nil {
			t.Fatalf("get node: %v", err)
		}
		if got.Status != models.NodeStatusOffline {
			t.Fatalf("token-rejected HELLO must demote online node to offline, got %s", got.Status)
		}
	})

	t.Run("pending node stays pending", func(t *testing.T) {
		g, nm, _ := newTestGateway(t)
		pending, err := nm.PrepareEnrollment(ctx, "pending-token-rej", "10.0.0.88", models.DeploymentModeNative)
		if err != nil {
			t.Fatalf("prepare enrollment: %v", err)
		}
		conn := newHelloTestConn(t, g)
		env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
			ProtocolVersion: protocol.ProtocolVersion,
			RegistrationKey: "wrong-token",
			AgentID:         pending.AgentID,
			Hostname:        "pending-token-rej",
		})
		if err := g.handleHello(ctx, conn, env); err == nil {
			t.Fatal("expected rejection for wrong token")
		}
		got, err := nm.GetNode(ctx, pending.ID)
		if err != nil {
			t.Fatalf("get node: %v", err)
		}
		if got.Status != models.NodeStatusPending {
			t.Fatalf("never-connected pending node must stay pending, got %s", got.Status)
		}
	})

	t.Run("unknown agent id writes nothing", func(t *testing.T) {
		g, _, _ := newTestGateway(t)
		conn := newHelloTestConn(t, g)
		env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
			ProtocolVersion: protocol.ProtocolVersion,
			RegistrationKey: "wrong-token",
			AgentID:         "agent-does-not-exist",
			Hostname:        "ghost-node",
		})
		if err := g.handleHello(ctx, conn, env); err == nil || err.Error() != "invalid registration token" {
			t.Fatalf("expected invalid registration token rejection, got %v", err)
		}
		// 查无行：不得创建节点、不得写任何 status
		nodes, err := g.nodes.ListNodes(ctx)
		if err != nil {
			t.Fatalf("list nodes: %v", err)
		}
		if len(nodes) != 0 {
			t.Fatalf("rejected HELLO with unknown agent id must not write nodes, got %+v", nodes)
		}
	})
}

// TestHelloRejectionKeepsActiveSessionOnline 验证活跃会话守卫（裁决 2）：
// 节点存在活跃合法会话时，credential 或 token 拒绝都不得降级；
// 无活跃会话时照常降级（由两个 demotes 用例覆盖）。
func TestHelloRejectionKeepsActiveSessionOnline(t *testing.T) {
	ctx := context.Background()

	t.Run("credential rejection with active session", func(t *testing.T) {
		g, nm, sm := newTestGateway(t)
		node, cred, _, err := nm.RegisterOrUpdate(ctx, "agent-guard-c", "host-guard-c", "10.0.0.89", "linux", "amd64", "1.0", "native", false, nil)
		if err != nil {
			t.Fatalf("register: %v", err)
		}
		sm.Register(&fakeAgentConn{nodeID: node.ID})
		conn := newHelloTestConn(t, g)
		env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
			ProtocolVersion: protocol.ProtocolVersion,
			AgentID:         node.AgentID,
			AgentCredential: cred + "-wrong",
		})
		if err := g.handleHello(ctx, conn, env); err == nil {
			t.Fatal("expected rejection for wrong credential")
		}
		got, err := nm.GetNode(ctx, node.ID)
		if err != nil {
			t.Fatalf("get node: %v", err)
		}
		if got.Status != models.NodeStatusOnline {
			t.Fatalf("active session must keep node online despite rejected HELLO, got %s", got.Status)
		}
		if _, ok := sm.Get(node.ID); !ok {
			t.Fatal("active session must survive rejected HELLO")
		}
	})

	t.Run("token rejection with active session", func(t *testing.T) {
		g, nm, sm := newTestGateway(t)
		node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-guard-t", "host-guard-t", "10.0.0.90", "linux", "amd64", "1.0", "native", false, nil)
		if err != nil {
			t.Fatalf("register: %v", err)
		}
		sm.Register(&fakeAgentConn{nodeID: node.ID})
		conn := newHelloTestConn(t, g)
		env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
			ProtocolVersion: protocol.ProtocolVersion,
			RegistrationKey: "wrong-token",
			AgentID:         node.AgentID,
			Hostname:        "host-guard-t",
		})
		if err := g.handleHello(ctx, conn, env); err == nil {
			t.Fatal("expected rejection for wrong token")
		}
		got, err := nm.GetNode(ctx, node.ID)
		if err != nil {
			t.Fatalf("get node: %v", err)
		}
		if got.Status != models.NodeStatusOnline {
			t.Fatalf("active session must keep node online despite token rejection, got %s", got.Status)
		}
		if _, ok := sm.Get(node.ID); !ok {
			t.Fatal("active session must survive rejected HELLO")
		}
	})
}
