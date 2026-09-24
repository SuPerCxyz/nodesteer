package hub

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/coder/websocket"
)

// Gateway 配置
type GatewayConfig struct {
	RegistrationToken string
	HeartbeatTimeout  time.Duration
	MaxMsgBytes       int64
}

// Gateway Agent Gateway 服务
type Gateway struct {
	cfg             GatewayConfig
	nodes           *NodeManager
	sessions        *SessionManager
	syncMgr         *SyncManager
	execMgr         *ExecutionManager
	onAgentConn     func(nodeID string)
	transferHandler http.Handler
	fileTransfers   *FileTransferManager
	logger          *slog.Logger
	mu              sync.Mutex
	connMeta        map[string]*connMeta
}

type connMeta struct {
	lastSeen time.Time
	nodeID   string
	agentID  string
}

// NewGateway 创建 Gateway
func NewGateway(cfg GatewayConfig, nm *NodeManager, sm *SessionManager, syncMgr *SyncManager, execMgr *ExecutionManager, logger *slog.Logger) *Gateway {
	return &Gateway{
		cfg:      cfg,
		nodes:    nm,
		sessions: sm,
		syncMgr:  syncMgr,
		execMgr:  execMgr,
		logger:   logger,
		connMeta: map[string]*connMeta{},
	}
}

// SetOnAgentConn 设置连接回调
func (g *Gateway) SetOnAgentConn(f func(nodeID string)) {
	g.onAgentConn = f
}

// SetTransferHandler 注入 Agent Gateway 文件数据处理器。
func (g *Gateway) SetTransferHandler(h http.Handler) { g.transferHandler = h }

// SetFileTransferManager 注入文件传输状态处理器。
func (g *Gateway) SetFileTransferManager(m *FileTransferManager) { g.fileTransfers = m }

// LastSeen 实现 AgentConn 接口需要的 LastSeen
func (g *Gateway) LastSeen() time.Time {
	g.mu.Lock()
	defer g.mu.Unlock()
	// 占位，实际由 conn 维护
	return time.Now()
}

// Handler 返回 HTTP Handler
func (g *Gateway) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if g.transferHandler != nil && strings.HasPrefix(r.URL.Path, "/agent/transfers/") {
			g.transferHandler.ServeHTTP(w, r)
			return
		}
		g.handle(w, r)
	})
}

func (g *Gateway) handle(w http.ResponseWriter, r *http.Request) {
	c, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		OriginPatterns: []string{"*"},
	})
	if err != nil {
		g.logger.Error("websocket accept failed", "error", err)
		return
	}
	defer c.Close(websocket.StatusInternalError, "closed")

	ctx := r.Context()
	// 默认读取限制需覆盖 max_log_bytes 级别的执行结果帧（JSON 编码后有开销）；
	// 单条消息过大时再按 MaxMsgBytes 显式覆盖。
	msgBytes := int64(8 << 20) // 8MB
	if g.cfg.MaxMsgBytes > 0 {
		msgBytes = g.cfg.MaxMsgBytes
	}
	c.SetReadLimit(msgBytes)

	// 建立会话包装
	conn := &wsConn{
		c:       c,
		meta:    &connMeta{lastSeen: time.Now()},
		gateway: g,
		sendCh:  make(chan protocol.Envelope, 256),
		done:    make(chan struct{}),
	}
	go conn.writeLoop(ctx)
	defer func() {
		close(conn.done)
		g.handleDisconnect(conn.meta.nodeID, conn)
	}()

	for {
		typ, data, err := c.Read(ctx)
		if err != nil {
			return
		}
		if typ != websocket.MessageText {
			continue
		}
		var env protocol.Envelope
		if err := json.Unmarshal(data, &env); err != nil {
			g.logger.Warn("bad envelope", "error", err)
			continue
		}
		conn.meta.lastSeen = time.Now()
		if err := g.dispatch(ctx, conn, env); err != nil {
			if g.dropOrphanSession(ctx, conn.meta.nodeID, env.Type, err) {
				return
			}
			g.logger.Warn("dispatch error", "type", env.Type, "error", err)
			// 协议错误则关闭
			if env.Type == protocol.MsgHello {
				return
			}
		}
	}
}

// handleDisconnect 处理 Agent 连接断开：移除会话并将节点标记为 offline。
// 仅当该连接仍是当前活跃会话时处理；若已被新连接替换（重连），则交由新会话维持 online。
// 会话已被清理（如 heartbeat checker）时确保状态为 offline。
func (g *Gateway) handleDisconnect(nodeID string, conn AgentConn) {
	if nodeID == "" {
		return
	}
	cur, ok := g.sessions.Get(nodeID)
	if !ok {
		// 会话已不存在（例如已被 heartbeat checker 清理），确保状态标记为 offline
		if err := g.markDisconnected(context.Background(), nodeID); err != nil {
			g.logger.Warn("mark offline on disconnect failed", "node", nodeID, "error", err)
		}
		return
	}
	if cur != conn {
		// 该连接已被更新的会话替换，不处理，避免误标离线
		return
	}
	g.sessions.Unregister(nodeID)
	if err := g.markDisconnected(context.Background(), nodeID); err != nil {
		g.logger.Warn("mark offline on disconnect failed", "node", nodeID, "error", err)
	}
}

func (g *Gateway) markDisconnected(ctx context.Context, nodeID string) error {
	// 统一走 NodeManager.MarkOffline：含 maintenance/disabled 保护与已删除节点容忍。
	return g.nodes.MarkOffline(ctx, nodeID)
}

// markConnected accepted HELLO 后显式将节点置为 online（不依赖心跳写入的副作用）。
// 与 UpdateNodeHeartbeat 的 CASE 语义一致：maintenance/disabled 为人工设置状态，不被会话接受覆盖。
func (g *Gateway) markConnected(ctx context.Context, n *models.Node) error {
	if n.Status == models.NodeStatusOnline ||
		n.Status == models.NodeStatusMaintenance ||
		n.Status == models.NodeStatusDisabled {
		return nil
	}
	if err := g.nodes.SetNodeStatus(ctx, n.ID, models.NodeStatusOnline); err != nil {
		return err
	}
	n.Status = models.NodeStatusOnline
	return nil
}

// markDisconnectedIfOnline 仅当节点当前为 online 时在拒绝返回前降级 offline。
// 用于 HELLO 拒绝分支（credential 无效 / token 不匹配）防滞留：能定位到节点但该连接
// 不合法时不残留 online；从未成功会话的新节点（pending/offline）保持原状态不动。
// 活跃会话守卫：拒绝只证明「该连接不合法」，不证明「节点无会话」——存在活跃合法会话时
// 不降级，避免 ≤30s 假离线。注意本守卫只属于拒绝降级路径：
// markDisconnected（连接断开 defer 路径，断开即会话终结）不得引入此守卫。
func (g *Gateway) markDisconnectedIfOnline(ctx context.Context, nodeID string) error {
	if _, ok := g.sessions.Get(nodeID); ok {
		// 节点仍有活跃合法会话，其 online 是真实状态，拒绝不构成降级依据。
		return nil
	}
	node, err := g.nodes.GetNode(ctx, nodeID)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		return err
	}
	if node.Status != models.NodeStatusOnline {
		return nil
	}
	return g.nodes.SetNodeStatus(ctx, nodeID, models.NodeStatusOffline)
}

// nodeMissing 判定节点的记录是否已不存在。
func (g *Gateway) nodeMissing(ctx context.Context, nodeID string) bool {
	if nodeID == "" {
		return false
	}
	_, err := g.nodes.GetNode(ctx, nodeID)
	return errors.Is(err, sql.ErrNoRows)
}

// dropOrphanSession 回收“节点记录已删除但会话仍存活”的遗留会话。
// 这类会话若不复用，Agent 每个上报周期都会查询已删除节点并重复报错。
// 返回 true 表示该错误已按会话回收处理，调用方无需再记录通用 dispatch error。
func (g *Gateway) dropOrphanSession(ctx context.Context, nodeID, envType string, err error) bool {
	if !errors.Is(err, sql.ErrNoRows) || !g.nodeMissing(ctx, nodeID) {
		return false
	}
	g.logger.Warn("drop session for deleted node", "node", nodeID, "type", envType)
	g.sessions.Unregister(nodeID)
	return true
}

func (g *Gateway) dispatch(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	switch env.Type {
	case protocol.MsgHello:
		return g.handleHello(ctx, conn, env)
	case protocol.MsgHeartbeat:
		return g.handleHeartbeat(ctx, conn, env)
	case protocol.MsgRevisionCheck:
		return g.handleRevisionCheck(ctx, conn, env)
	case protocol.MsgSyncRequest:
		return g.handleSyncRequest(ctx, conn, env)
	case protocol.MsgSyncAck:
		return g.handleSyncAck(ctx, conn, env)
	case protocol.MsgExecStarted:
		return g.handleExecStarted(ctx, conn, env)
	case protocol.MsgExecFinished:
		return g.handleExecFinished(ctx, conn, env)
	case protocol.MsgLogChunk:
		return g.handleLogChunk(ctx, conn, env)
	case protocol.MsgRemoteState:
		return g.handleRemoteState(ctx, conn, env)
	case protocol.MsgRemoteStateReq:
		return g.handleRemoteStateReq(ctx, conn, env)
	case protocol.MsgInventory:
		return g.handleInventory(ctx, conn, env)
	case protocol.MsgDeployResult:
		return g.handleDeployResult(ctx, conn, env)
	case protocol.MsgFileUploadResult:
		return g.handleFileUploadResult(ctx, conn, env)
	case protocol.MsgFileDeliveryResult:
		return g.handleFileDeliveryResult(ctx, conn, env)
	case protocol.MsgError:
		g.logger.Warn("agent error", "id", env.ID, "payload", string(env.Payload))
		return nil
	}
	return fmt.Errorf("unknown message type %q", env.Type)
}

func (g *Gateway) handleFileUploadResult(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.FileUploadResultPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	if g.fileTransfers == nil {
		return nil
	}
	return g.fileTransfers.HandleUploadResult(ctx, conn.meta.nodeID, p)
}

func (g *Gateway) handleFileDeliveryResult(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.FileDeliveryResultPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	if g.fileTransfers == nil {
		return nil
	}
	return g.fileTransfers.HandleDeliveryResult(ctx, conn.meta.nodeID, p)
}

func (g *Gateway) handleHello(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.HelloPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	if p.ProtocolVersion != protocol.ProtocolVersion {
		conn.sendSync(ctx, protocol.NewEnvelope(protocol.MsgHelloAck, env.ID, protocol.HelloAckPayload{
			Accepted: false,
			Message:  fmt.Sprintf("protocol version %d not supported", p.ProtocolVersion),
		}))
		return fmt.Errorf("unsupported protocol version %d", p.ProtocolVersion)
	}

	var node *NodeWithCred
	if p.AgentCredential != "" && p.RegistrationKey == "" {
		// 已注册 Agent 认证
		existing, err2 := g.nodes.store.GetNodeByAgentID(ctx, p.AgentID)
		if err2 != nil {
			conn.sendSync(ctx, protocol.NewEnvelope(protocol.MsgHelloAck, env.ID, protocol.HelloAckPayload{Accepted: false, Message: "agent not registered"}))
			return fmt.Errorf("agent %s not registered", p.AgentID)
		}
		ok := g.nodes.AuthenticateAgent(ctx, existing.ID, p.AgentCredential)
		if !ok {
			// 拒绝返回前先解除滞留 online：能定位到节点且其当前 online → 置 offline；
			// 从未成功会话的 pending 节点保持 pending。
			if err := g.markDisconnectedIfOnline(ctx, existing.ID); err != nil {
				g.logger.Warn("demote online on rejected hello failed", "node", existing.ID, "error", err)
			}
			conn.sendSync(ctx, protocol.NewEnvelope(protocol.MsgHelloAck, env.ID, protocol.HelloAckPayload{Accepted: false, Message: "invalid credential"}))
			return fmt.Errorf("invalid credential")
		}
		node = &NodeWithCred{Node: existing, Cred: p.AgentCredential}
		// accepted：显式置 online（不依赖心跳副作用），maintenance/disabled 不被覆盖
		if err := g.markConnected(ctx, node.Node); err != nil {
			return err
		}
		if err := g.nodes.UpdateHeartbeat(ctx, existing.ID, time.Now()); err != nil {
			return err
		}
	} else {
		// 首次注册
		if p.RegistrationKey == "" || p.RegistrationKey != g.cfg.RegistrationToken {
			// token 拒绝同样可归属既有节点：HELLO 携带的 agent_id 是纳管预分配身份，
			// 查到行才走降级（与 invalid credential 分支同语义，含活跃会话守卫）；
			// agent_id 查无行（含空值）则不写。降级须在拒绝返回前完成。
			if existing, err := g.nodes.store.GetNodeByAgentID(ctx, p.AgentID); err == nil {
				if err := g.markDisconnectedIfOnline(ctx, existing.ID); err != nil {
					g.logger.Warn("demote online on rejected hello failed", "node", existing.ID, "error", err)
				}
			} else if !errors.Is(err, sql.ErrNoRows) {
				g.logger.Warn("locate node on rejected hello failed", "agent", p.AgentID, "error", err)
			}
			conn.sendSync(ctx, protocol.NewEnvelope(protocol.MsgHelloAck, env.ID, protocol.HelloAckPayload{Accepted: false, Message: "invalid registration token"}))
			return fmt.Errorf("invalid registration token")
		}
		n, cred, isNew, err := g.nodes.RegisterOrUpdate(ctx, p.AgentID, p.Hostname, p.IP, p.OS, p.Arch,
			p.AgentVersion, p.DeploymentMode, p.HostIntegration, p.Capabilities)
		if err != nil {
			return err
		}
		node = &NodeWithCred{Node: n, Cred: cred}
		_ = isNew
		// accepted：显式置 online（注册路径已写库，此处幂等），maintenance/disabled 不被覆盖
		if err := g.markConnected(ctx, node.Node); err != nil {
			return err
		}
	}

	conn.meta.nodeID = node.Node.ID
	conn.meta.agentID = node.Node.AgentID
	g.sessions.Register(conn)
	g.mu.Lock()
	g.connMeta[node.Node.ID] = conn.meta
	g.mu.Unlock()

	cur, _ := g.syncMgr.revisions.Current(ctx)
	conn.send(protocol.NewEnvelope(protocol.MsgHelloAck, env.ID, protocol.HelloAckPayload{
		Accepted:         true,
		NodeID:           node.Node.ID,
		AgentID:          node.Node.AgentID,
		AgentCredential:  node.Cred,
		DesiredGlobalRev: cur,
		Settings:         g.settings(ctx),
		NodeStatus:       node.Node.Status,
	}))
	if g.onAgentConn != nil {
		g.onAgentConn(node.Node.ID)
	}
	return nil
}

func (g *Gateway) handleHeartbeat(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.HeartbeatPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	p.NodeID = conn.meta.nodeID
	if err := g.nodes.UpdateHeartbeat(ctx, p.NodeID, time.Now()); err != nil {
		return err
	}
	if p.GlobalRev > 0 {
		cur, _ := g.syncMgr.revisions.Current(ctx)
		syncStatus := "synced"
		if p.GlobalRev < cur {
			syncStatus = "outdated"
		}
		g.nodes.UpdateSyncState(ctx, p.NodeID, p.GlobalRev, syncStatus)
	}
	conn.send(protocol.NewEnvelope(protocol.MsgHeartbeatAck, env.ID, g.settingsPayload(ctx)))
	return nil
}

func (g *Gateway) settings(ctx context.Context) map[string]string {
	values := map[string]string{}
	for _, key := range []string{"heartbeat_interval_sec", "revision_check_interval_sec", "max_log_bytes"} {
		if v, err := g.nodes.store.GetSetting(ctx, key); err == nil {
			values[key] = v
		}
	}
	return values
}

func (g *Gateway) settingsPayload(ctx context.Context) protocol.SettingsPayload {
	values := g.settings(ctx)
	p := protocol.SettingsPayload{}
	if n, err := strconv.Atoi(values["heartbeat_interval_sec"]); err == nil {
		p.HeartbeatSec = n
	}
	if n, err := strconv.Atoi(values["revision_check_interval_sec"]); err == nil {
		p.RevisionCheckSec = n
	}
	if n, err := strconv.Atoi(values["max_log_bytes"]); err == nil {
		p.MaxLogBytes = n
	}
	return p
}

func (g *Gateway) handleRevisionCheck(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.HeartbeatPayload
	json.Unmarshal(env.Payload, &p)
	cur, err := g.syncMgr.revisions.Current(ctx)
	if err != nil {
		return err
	}
	p.NodeID = conn.meta.nodeID
	syncStatus := "synced"
	if p.GlobalRev < cur {
		syncStatus = "outdated"
		g.nodes.UpdateSyncState(ctx, p.NodeID, p.GlobalRev, syncStatus)
	}
	conn.send(protocol.NewEnvelope(protocol.MsgRevisionCheckAck, env.ID, protocol.HeartbeatPayload{
		NodeID:    p.NodeID,
		GlobalRev: cur,
	}))
	return nil
}

func (g *Gateway) handleSyncRequest(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.SyncRequestPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	nodeID := conn.meta.nodeID
	resp, err := g.syncMgr.BuildSyncResponse(ctx, nodeID, p.Since)
	if err != nil {
		return err
	}
	conn.send(protocol.NewEnvelope(protocol.MsgSyncResponse, env.ID, resp))
	return nil
}

func (g *Gateway) handleSyncAck(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.SyncAckPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	nodeID := conn.meta.nodeID
	status := "synced"
	if !p.OK {
		status = "error"
	}
	g.nodes.UpdateSyncState(ctx, nodeID, p.AppliedRev, status)
	return nil
}

func (g *Gateway) handleExecStarted(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.ExecutionStartedPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	if g.execMgr != nil {
		g.execMgr.MarkStarted(ctx, conn.meta.nodeID, p.ExecutionID, p.StartTime)
	}
	return nil
}

func (g *Gateway) handleExecFinished(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.ExecutionFinishedPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	if g.execMgr != nil {
		if err := g.execMgr.MarkFinished(ctx, conn.meta.nodeID, p); err != nil {
			conn.send(protocol.NewEnvelope(protocol.MsgExecutionAck, p.ExecutionID, protocol.ExecutionAckPayload{
				ExecutionID: p.ExecutionID, Error: err.Error(),
			}))
			return err
		}
	}
	conn.send(protocol.NewEnvelope(protocol.MsgExecutionAck, p.ExecutionID, protocol.ExecutionAckPayload{
		ExecutionID: p.ExecutionID, OK: true,
	}))
	return nil
}

func (g *Gateway) handleLogChunk(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.LogChunkPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	if g.execMgr != nil {
		g.execMgr.AppendLog(ctx, conn.meta.nodeID, p)
	}
	return nil
}

func (g *Gateway) handleRemoteState(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.RemoteStatePayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	if p.Property != "online" && p.Property != "last_execution" {
		return fmt.Errorf("unsupported remote state property: %s", p.Property)
	}
	// Agent 上报的 NodeID 不能跨节点伪造，以连接握手身份为准。
	return g.nodes.store.SetRemoteState(ctx, conn.meta.nodeID, p.Property, p.Value, time.Now(), p.TTL)
}

// handleRemoteStateReq 处理 Agent 对远程节点状态的查询（Remote Node Condition）
func (g *Gateway) handleRemoteStateReq(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.RemoteStateReqPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	value, observedAt, ttl := g.resolveRemoteState(ctx, p)
	reply := protocol.NewEnvelope(protocol.MsgRemoteState, env.ID, protocol.RemoteStatePayload{
		NodeID:     p.TargetNodeID,
		Property:   p.Property,
		Value:      value,
		ObservedAt: observedAt.Format(time.RFC3339Nano),
		TTL:        ttl,
	})
	return conn.send(reply)
}

// resolveRemoteState 解析远程状态：online → 节点在线状态；last_execution → 最近执行结果
func (g *Gateway) resolveRemoteState(ctx context.Context, p protocol.RemoteStateReqPayload) (string, time.Time, int64) {
	now := time.Now()
	// 先查缓存（Agent 主动上报）
	if value, observed, expires, err := g.nodes.store.GetRemoteState(ctx, p.TargetNodeID, p.Property); err == nil {
		if expires.After(now) {
			return value, observed, int64(expires.Sub(observed).Seconds())
		}
	}
	switch p.Property {
	case "online":
		node, err := g.nodes.GetNode(ctx, p.TargetNodeID)
		if err != nil {
			return protocol.RemoteStateUnknown, now, 60
		}
		return strings.ToLower(node.Status), now, 60
	case "last_execution":
		if p.TaskID == "" {
			return protocol.RemoteStateUnknown, now, 60
		}
		execs, err := g.nodes.store.ListExecutions(ctx, store.ExecutionFilter{TaskID: p.TaskID, NodeID: p.TargetNodeID, Limit: 1})
		if err != nil || len(execs) == 0 {
			return protocol.RemoteStateUnknown, now, 60
		}
		return execs[0].Status, now, 60
	default:
		return protocol.RemoteStateUnknown, now, 60
	}
}

// handleInventory 处理 Inventory 上报
func (g *Gateway) handleInventory(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.InventoryPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	nodeID := conn.meta.nodeID
	node, err := g.nodes.GetNode(ctx, nodeID)
	if err != nil {
		return err
	}
	if !p.Available {
		node.Inventory = nil
	} else if p.Inventory != nil {
		node.Inventory = convertInventory(p.Inventory)
	}
	return g.nodes.store.UpsertNode(ctx, node)
}

// convertInventory protocol Inventory → models Inventory
func convertInventory(inv *protocol.Inventory) *models.Inventory {
	m := &models.Inventory{
		OS: inv.OS, OSVersion: inv.OSVersion, Kernel: inv.Kernel, Arch: inv.Arch,
	}
	for _, c := range inv.CPU {
		m.CPU = append(m.CPU, models.CPUInfo{Model: c.Model, Cores: c.Cores, MHz: c.MHz})
	}
	if inv.Memory != nil {
		m.Memory = &models.MemoryInfo{TotalKB: inv.Memory.TotalKB, AvailableKB: inv.Memory.AvailableKB}
	}
	for _, f := range inv.Filesystem {
		m.Filesystem = append(m.Filesystem, models.FilesystemInfo{
			Mount: f.Mount, Device: f.Device, FSType: f.FSType, TotalKB: f.TotalKB, FreeKB: f.FreeKB,
		})
	}
	for _, n := range inv.Network {
		m.Network = append(m.Network, models.NetworkInfo{Interface: n.Interface, Addresses: n.Addresses, MAC: n.MAC})
	}
	return m
}

func (g *Gateway) handleDeployResult(ctx context.Context, conn *wsConn, env protocol.Envelope) error {
	var p protocol.DeployResultPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return err
	}
	if g.execMgr != nil {
		if err := g.execMgr.HandleDeployResult(ctx, conn.meta.nodeID, p); err != nil {
			conn.send(protocol.NewEnvelope(protocol.MsgExecutionAck, p.ExecutionID, protocol.ExecutionAckPayload{
				ExecutionID: p.ExecutionID, Error: err.Error(),
			}))
			return err
		}
	}
	conn.send(protocol.NewEnvelope(protocol.MsgExecutionAck, p.ExecutionID, protocol.ExecutionAckPayload{
		ExecutionID: p.ExecutionID, OK: true,
	}))
	return nil
}

// NodeWithCred 节点与凭证
type NodeWithCred struct {
	Node *models.Node
	Cred string
}

// wsConn Agent 连接包装
type wsConn struct {
	c       *websocket.Conn
	meta    *connMeta
	gateway *Gateway
	sendCh  chan protocol.Envelope
	done    chan struct{}
	once    sync.Once
}

func (w *wsConn) send(env protocol.Envelope) error {
	select {
	case w.sendCh <- env:
		return nil
	case <-w.done:
		return fmt.Errorf("conn closed")
	}
}

// sendSync 同步发送（握手阶段确保送达）
func (w *wsConn) sendSync(ctx context.Context, env protocol.Envelope) error {
	data, err := json.Marshal(env)
	if err != nil {
		return err
	}
	return w.c.Write(ctx, websocket.MessageText, data)
}

func (w *wsConn) writeLoop(ctx context.Context) {
	for {
		select {
		case env := <-w.sendCh:
			data, err := json.Marshal(env)
			if err != nil {
				continue
			}
			if err := w.c.Write(ctx, websocket.MessageText, data); err != nil {
				return
			}
		case <-w.done:
			return
		}
	}
}

// NodeID 实现 AgentConn
func (w *wsConn) NodeID() string {
	if w.meta == nil {
		return ""
	}
	return w.meta.nodeID
}

// Send 实现 AgentConn
func (w *wsConn) Send(msg protocol.Envelope) error { return w.send(msg) }

// Close 实现 AgentConn
func (w *wsConn) Close() error {
	w.once.Do(func() {
		w.c.Close(websocket.StatusNormalClosure, "close")
	})
	return nil
}

// LastSeen 连接最后活动时间
func (w *wsConn) LastSeen() time.Time {
	if w.meta == nil {
		return time.Now()
	}
	return w.meta.lastSeen
}
