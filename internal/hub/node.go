package hub

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/google/uuid"
)

// ErrNodeNotFound 节点不存在
var ErrNodeNotFound = errors.New("node not found")

// NodeManager 节点管理
type NodeManager struct {
	store     store.Store
	revisions *RevisionManager
	syncMgr   *SyncManager
	sessions  *SessionManager
}

// SetSyncManager 注入同步管理器，供节点目标变化触发全局收敛。
func (nm *NodeManager) SetSyncManager(sm *SyncManager) { nm.syncMgr = sm }

// SetSessionManager 注入会话管理器，供节点删除时回收 Agent 连接。
func (nm *NodeManager) SetSessionManager(sm *SessionManager) { nm.sessions = sm }

// NewNodeManager 创建节点管理器
func NewNodeManager(st store.Store, rm *RevisionManager) *NodeManager {
	return &NodeManager{store: st, revisions: rm}
}

// RegisterAgent 注册新 Agent（返回 node/agent id 与凭证）
func (nm *NodeManager) RegisterAgent(ctx context.Context, hostname, ip, os, arch, agentVersion, mode string, hostInt bool, caps map[string]bool) (*models.Node, string, error) {
	return nm.createNode(ctx, hostname, ip, os, arch, agentVersion, mode, hostInt, caps, models.NodeStatusOnline, "outdated")
}

// PrepareEnrollment 创建待接入节点，并预分配 Agent 身份供首次 HELLO 绑定。
// 版本、架构和部署模式必须等首次 HELLO 后再以 Agent 实际上报值为准。
// 若同名（忽略大小写）或同地址的节点已存在，则复用其 Node ID / Agent ID 并重新签发凭证，
// 避免「撤销凭证 → 重新纳管」产生重复节点记录；既有标签与历史保留。
func (nm *NodeManager) PrepareEnrollment(ctx context.Context, hostname, ip string, _ string) (*models.Node, error) {
	if existing := nm.findEnrollableNode(ctx, hostname, ip); existing != nil {
		existing.Hostname = hostname
		if ip != "" {
			existing.IP = ip
		}
		// 纳管创建 = 等待接入：状态进入 pending，等首次 HELLO 被接受后再置 online
		existing.Status = models.NodeStatusPending
		existing.SyncStatus = "pending"
		existing.AgentVersion = ""
		existing.OS = ""
		existing.Arch = ""
		existing.DeploymentMode = ""
		existing.HostIntegration = false
		existing.Capabilities = nil
		if err := nm.store.UpsertNode(ctx, existing); err != nil {
			return nil, err
		}
		cred := "cred-" + uuid.NewString()
		if err := nm.store.SetSetting(ctx, "cred:"+existing.ID, cred); err != nil {
			return nil, err
		}
		// 重新纳管即重新授权：清除撤销标记，允许 Agent 用新凭证接入
		_ = nm.store.DeleteSetting(ctx, "revoked:"+existing.ID)
		return existing, nil
	}
	n, _, err := nm.createNode(ctx, hostname, ip, "", "", "", "", false, nil, models.NodeStatusPending, "pending")
	return n, err
}

// findEnrollableNode 按节点名（忽略大小写）或节点地址查找可复用的既有节点。
func (nm *NodeManager) findEnrollableNode(ctx context.Context, hostname, ip string) *models.Node {
	nodes, err := nm.store.ListNodes(ctx)
	if err != nil {
		return nil
	}
	name := strings.ToLower(strings.TrimSpace(hostname))
	if name != "" {
		for _, n := range nodes {
			if strings.ToLower(strings.TrimSpace(n.Hostname)) == name {
				return n
			}
		}
	}
	if ip != "" {
		for _, n := range nodes {
			if n.IP == ip {
				return n
			}
		}
	}
	return nil
}

func (nm *NodeManager) createNode(ctx context.Context, hostname, ip, os, arch, agentVersion, mode string, hostInt bool, caps map[string]bool, status, syncStatus string) (*models.Node, string, error) {
	now := time.Now()
	n := &models.Node{
		ID:              uuid.NewString(),
		AgentID:         "agent-" + uuid.NewString(),
		Hostname:        hostname,
		IP:              ip,
		OS:              os,
		Arch:            arch,
		AgentVersion:    agentVersion,
		DeploymentMode:  mode,
		HostIntegration: hostInt,
		Status:          status,
		Labels:          map[string]string{},
		Capabilities:    caps,
		FirstSeen:       now,
		SyncStatus:      syncStatus,
	}
	if status == models.NodeStatusOnline {
		n.LastSeen = now
	}
	if err := nm.store.UpsertNode(ctx, n); err != nil {
		return nil, "", err
	}
	cred := "cred-" + uuid.NewString()
	if err := nm.store.SetSetting(ctx, "cred:"+n.ID, cred); err != nil {
		return nil, "", err
	}
	return n, cred, nil
}

// AuthenticateAgent 校验 Agent 凭证
func (nm *NodeManager) AuthenticateAgent(ctx context.Context, nodeID, cred string) bool {
	stored, err := nm.store.GetSetting(ctx, "cred:"+nodeID)
	if err != nil {
		return false
	}
	return stored == cred
}

// RegisterOrUpdate 注册或更新节点（幂等）
func (nm *NodeManager) RegisterOrUpdate(ctx context.Context, agentID, hostname, ip, os, arch, agentVersion, mode string, hostInt bool, caps map[string]bool) (*models.Node, string, bool, error) {
	existing, err := nm.store.GetNodeByAgentID(ctx, agentID)
	if err == nil {
		if revoked, _ := nm.store.GetSetting(ctx, "revoked:"+existing.ID); revoked == "true" {
			return nil, "", false, errors.New("node credential revoked; re-enrollment required")
		}
		existing.Hostname = hostname
		existing.IP = ip
		existing.OS = os
		existing.Arch = arch
		existing.AgentVersion = agentVersion
		existing.Capabilities = caps
		existing.DeploymentMode = mode
		existing.HostIntegration = hostInt
		existing.LastSeen = time.Now()
		// accepted HELLO 不得覆盖 maintenance/disabled（与心跳 CASE 语义一致）
		if existing.Status != models.NodeStatusMaintenance && existing.Status != models.NodeStatusDisabled {
			existing.Status = models.NodeStatusOnline
		}
		if err := nm.store.UpsertNode(ctx, existing); err != nil {
			return nil, "", false, err
		}
		cred, _ := nm.store.GetSetting(ctx, "cred:"+existing.ID)
		return existing, cred, false, nil
	}
	n, cred, err := nm.RegisterAgent(ctx, hostname, ip, os, arch, agentVersion, mode, hostInt, caps)
	if err != nil {
		return nil, "", false, err
	}
	return n, cred, true, nil
}

// GetNode 获取节点
func (nm *NodeManager) GetNode(ctx context.Context, id string) (*models.Node, error) {
	return nm.store.GetNode(ctx, id)
}

// ListNodes 节点列表
func (nm *NodeManager) ListNodes(ctx context.Context) ([]*models.Node, error) {
	return nm.store.ListNodes(ctx)
}

// SetNodeStatus 设置节点状态
func (nm *NodeManager) SetNodeStatus(ctx context.Context, id, status string) error {
	if !validNodeStatus(status) {
		return fmt.Errorf("invalid node status: %s", status)
	}
	if _, err := nm.store.GetNode(ctx, id); err != nil {
		return err
	}
	if err := nm.store.UpdateNodeStatus(ctx, id, status); err != nil {
		return err
	}
	if nm.syncMgr != nil {
		nm.syncMgr.NotifyNodeStatus(ctx, id, status)
	}
	return nil
}

func validNodeStatus(status string) bool {
	switch status {
	case models.NodeStatusPending, models.NodeStatusOnline, models.NodeStatusOffline, models.NodeStatusMaintenance, models.NodeStatusDisabled:
		return true
	default:
		return false
	}
}

// UpdateHeartbeat 心跳更新
func (nm *NodeManager) UpdateHeartbeat(ctx context.Context, id string, lastSeen time.Time) error {
	return nm.store.UpdateNodeHeartbeat(ctx, id, lastSeen)
}

// MarkOffline 将节点置为 offline，语义与 Gateway.markDisconnected 一致：
// maintenance/disabled 为人工设置状态不被覆盖；节点记录已删除时视为无需处理。
func (nm *NodeManager) MarkOffline(ctx context.Context, id string) error {
	node, err := nm.store.GetNode(ctx, id)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		return err
	}
	if node.Status == models.NodeStatusMaintenance || node.Status == models.NodeStatusDisabled {
		return nil
	}
	return nm.SetNodeStatus(ctx, id, models.NodeStatusOffline)
}

// UpdateSyncState 同步状态更新
func (nm *NodeManager) UpdateSyncState(ctx context.Context, id string, rev int64, syncStatus string) error {
	return nm.store.UpdateNodeSyncState(ctx, id, rev, syncStatus)
}

// SetLabels 设置标签
func (nm *NodeManager) SetLabels(ctx context.Context, id string, labels map[string]string) error {
	var rev int64
	if err := runMutationTx(ctx, nm.store, func(txctx context.Context) error {
		if err := nm.store.SetNodeLabels(txctx, id, labels); err != nil {
			return err
		}
		var err error
		rev, err = nm.recordTargetChange(txctx, models.ObjectNode, id, "labels")
		return err
	}); err != nil {
		return err
	}
	_ = rev
	if nm.syncMgr != nil {
		nm.syncMgr.NotifyAll(ctx)
	}
	return nil
}

// CreateGroup 创建节点组并推进 Desired State Revision。
func (nm *NodeManager) CreateGroup(ctx context.Context, g *models.Group) error {
	var rev int64
	if err := runMutationTx(ctx, nm.store, func(txctx context.Context) error {
		if err := nm.store.CreateGroup(txctx, g); err != nil {
			return err
		}
		var err error
		rev, err = nm.recordTargetChangeDetail(txctx, models.ObjectGroup, g.ID, "create", g.Name)
		return err
	}); err != nil {
		return err
	}
	_ = rev
	if nm.syncMgr != nil {
		nm.syncMgr.NotifyAll(ctx)
	}
	return nil
}

// UpdateGroup 更新节点组并推进 Desired State Revision。
func (nm *NodeManager) UpdateGroup(ctx context.Context, g *models.Group) error {
	var rev int64
	if err := runMutationTx(ctx, nm.store, func(txctx context.Context) error {
		if err := nm.store.UpdateGroup(txctx, g); err != nil {
			return err
		}
		var err error
		rev, err = nm.recordTargetChangeDetail(txctx, models.ObjectGroup, g.ID, "update", g.Name)
		return err
	}); err != nil {
		return err
	}
	_ = rev
	if nm.syncMgr != nil {
		nm.syncMgr.NotifyAll(ctx)
	}
	return nil
}

// DeleteGroup 删除节点组并推进 Desired State Revision。
func (nm *NodeManager) DeleteGroup(ctx context.Context, id string) error {
	groupName := ""
	if g, err := nm.store.GetGroup(ctx, id); err == nil {
		groupName = g.Name
	}
	tasks, err := nm.store.ListTasks(ctx)
	if err != nil {
		return err
	}
	for _, task := range tasks {
		if task.Target.Type == "group" {
			for _, groupID := range task.Target.GroupIDs {
				if groupID == id {
					return fmt.Errorf("group %s is referenced by task %s", id, task.ID)
				}
			}
		}
	}
	var rev int64
	if err := runMutationTx(ctx, nm.store, func(txctx context.Context) error {
		if err := nm.store.DeleteGroup(txctx, id); err != nil {
			return err
		}
		var err error
		rev, err = nm.recordTargetChangeDetail(txctx, models.ObjectGroup, id, "delete", groupName)
		return err
	}); err != nil {
		return err
	}
	_ = rev
	if nm.syncMgr != nil {
		nm.syncMgr.NotifyAll(ctx)
	}
	return nil
}

func (nm *NodeManager) recordTargetChange(ctx context.Context, objectType, objectID, operation string) (int64, error) {
	return nm.recordTargetChangeDetail(ctx, objectType, objectID, operation, "")
}

// recordTargetChangeDetail 与 recordTargetChange 相同，但写入可读 detail（如分组名）。
func (nm *NodeManager) recordTargetChangeDetail(ctx context.Context, objectType, objectID, operation, detail string) (int64, error) {
	if nm.syncMgr == nil {
		return 0, nil
	}
	rev, err := nm.revisions.Next(ctx)
	if err != nil {
		return 0, err
	}
	if err := nm.revisions.RecordChange(ctx, rev, objectType, objectID, 0, operation); err != nil {
		return 0, err
	}
	if err := recordMutationAuditDetail(ctx, nm.store, objectType, objectID, operation, detail); err != nil {
		return 0, err
	}
	return rev, nil
}

// RevokeCredential 撤销节点当前 Agent Credential。
func (nm *NodeManager) RevokeCredential(ctx context.Context, nodeID string) error {
	if _, err := nm.store.GetNode(ctx, nodeID); err != nil {
		return err
	}
	if err := nm.store.DeleteSetting(ctx, "cred:"+nodeID); err != nil {
		return err
	}
	if err := nm.store.SetSetting(ctx, "revoked:"+nodeID, "true"); err != nil {
		return err
	}
	return nm.SetNodeStatus(ctx, nodeID, models.NodeStatusDisabled)
}

// Delete 删除节点记录。运行中的任务、应用分配、组成员或文件传输存在时拒绝，避免悬空引用。
func (nm *NodeManager) Delete(ctx context.Context, nodeID string) error {
	if _, err := nm.store.GetNode(ctx, nodeID); err != nil {
		return err
	}
	groups, err := nm.store.ListGroups(ctx)
	if err != nil {
		return err
	}
	for _, g := range groups {
		for _, member := range g.Members {
			if g.Type == "static" && member == nodeID {
				return fmt.Errorf("node %s is referenced by group %s", nodeID, g.ID)
			}
		}
	}
	apps, err := nm.store.ListApplications(ctx)
	if err != nil {
		return err
	}
	for _, app := range apps {
		nodes, err := nm.store.GetApplicationNodes(ctx, app.ID)
		if err != nil {
			return err
		}
		for _, id := range nodes {
			if id == nodeID {
				return fmt.Errorf("node %s is assigned to application %s", nodeID, app.ID)
			}
		}
	}
	transfers, err := nm.store.ListFileTransfers(ctx)
	if err != nil {
		return err
	}
	for _, transfer := range transfers {
		if transfer.Status != models.FileTransferSuccess && transfer.Status != models.FileTransferFailed && transfer.Status != models.FileTransferCanceled && transfer.SourceNodeID == nodeID {
			return fmt.Errorf("node %s has active file transfer %s", nodeID, transfer.ID)
		}
		for _, target := range transfer.Targets {
			if target.NodeID == nodeID && target.Status != models.FileTargetSuccess && target.Status != models.FileTargetFailed && target.Status != models.FileTargetCanceled {
				return fmt.Errorf("node %s has active file transfer %s", nodeID, transfer.ID)
			}
		}
	}
	if err := nm.store.DeleteNode(ctx, nodeID); err != nil {
		return err
	}
	_ = nm.store.DeleteSetting(ctx, "cred:"+nodeID)
	_ = nm.store.DeleteSetting(ctx, "revoked:"+nodeID)
	// 节点记录已删除，必须立即回收其 Agent 会话。
	// 否则会话仍持有 nodeID，Agent 继续周期性上报时查询已删除节点，会持续产生 dispatch error。
	nm.dropSession(nodeID)
	if nm.syncMgr != nil {
		nm.syncMgr.NotifyAll(ctx)
	}
	return nil
}

// dropSession 移除并关闭节点会话（无会话时为空操作）。
func (nm *NodeManager) dropSession(nodeID string) {
	if nm.sessions == nil || nodeID == "" {
		return
	}
	conn, ok := nm.sessions.Get(nodeID)
	if !ok {
		return
	}
	// 先注销再关闭：连接退出时的 handleDisconnect 会走“会话已不存在”分支。
	nm.sessions.Unregister(nodeID)
	_ = conn.Close()
}

// ResolveTarget 解析任务目标为节点 ID 集合
func (nm *NodeManager) ResolveTarget(ctx context.Context, tgt models.Target) ([]string, error) {
	seen := map[string]bool{}
	var out []string
	add := func(ids []string) {
		for _, id := range ids {
			if !seen[id] {
				seen[id] = true
				out = append(out, id)
			}
		}
	}
	switch tgt.Type {
	case "node":
		// 目标节点可能已被删除：给出可读错误而不是原始 SQL 错误（缺陷 FAIL-A-009）
		for _, id := range tgt.NodeIDs {
			if _, err := nm.store.GetNode(ctx, id); err != nil {
				return nil, fmt.Errorf("target node %s no longer exists; update the task targets", id)
			}
			add([]string{id})
		}
	case "group":
		for _, gid := range tgt.GroupIDs {
			g, err := nm.store.GetGroup(ctx, gid)
			if err != nil {
				return nil, err
			}
			if g.Type == "label" {
				// 标签分组为动态成员，按 label 解析命中节点
				nodes, err := nm.store.ListNodes(ctx)
				if err != nil {
					return nil, err
				}
				for _, n := range nodes {
					if v, ok := n.Labels[g.LabelKey]; ok && (g.LabelValue == "" || v == g.LabelValue) {
						add([]string{n.ID})
					}
				}
			} else {
				members, err := nm.store.GroupMemberIDs(ctx, gid)
				if err != nil {
					return nil, err
				}
				add(members)
			}
		}
	case "label":
		nodes, err := nm.store.ListNodes(ctx)
		if err != nil {
			return nil, err
		}
		for _, n := range nodes {
			if v, ok := n.Labels[tgt.LabelKey]; ok && (tgt.LabelValue == "" || v == tgt.LabelValue) {
				add([]string{n.ID})
			}
		}
	default:
		add(tgt.NodeIDs)
	}
	return out, nil
}
