package hub

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/protocol"
	"github.com/cadentra/cadentra/internal/store"
)

// SyncManager Desired State 计算与同步
type SyncManager struct {
	store     store.Store
	revisions *RevisionManager
	sessions  *SessionManager
	nodes     *NodeManager
	// baseURL Hub Web 基址（构造 Artifact 下载 URL）
	baseURL string
	// changelogWindow 保留窗口（全局 Revision 数）
	changelogWindow int64
}

// NewSyncManager 创建同步管理器
func NewSyncManager(st store.Store, rm *RevisionManager, sm *SessionManager, nm *NodeManager) *SyncManager {
	return &SyncManager{
		store:           st,
		revisions:       rm,
		sessions:        sm,
		nodes:           nm,
		baseURL:         "",
		changelogWindow: 5000,
	}
}

// SetBaseURL 设置 Hub Web 基址（构造 Artifact 下载 URL）
func (m *SyncManager) SetBaseURL(u string) { m.baseURL = u }

// SetChangelogWindow 设置 changelog 窗口
func (m *SyncManager) SetChangelogWindow(w int64) {
	if w > 0 {
		m.changelogWindow = w
	}
}

// ChangelogWindow 返回 changelog 窗口
func (m *SyncManager) ChangelogWindow() int64 { return m.changelogWindow }

// NodeDesiredObjects 计算指定节点应同步的对象
type NodeDesiredObjects struct {
	Scripts   []*models.Script
	Tasks     []*models.Task
	Schedules []*models.Schedule
	Apps      []*models.Application
}

// ComputeDesiredState 计算节点 Desired State
func (sm *SyncManager) ComputeDesiredState(ctx context.Context, nodeID string) (*NodeDesiredObjects, error) {
	node, err := sm.store.GetNode(ctx, nodeID)
	if err != nil {
		return nil, err
	}

	groups, err := sm.store.ListGroups(ctx)
	if err != nil {
		return nil, err
	}
	// 计算该节点命中的 label 组
	myGroupIDs := map[string]bool{}
	for _, g := range groups {
		if g.Type == "label" {
			if v, ok := node.Labels[g.LabelKey]; ok && (g.LabelValue == "" || v == g.LabelValue) {
				myGroupIDs[g.ID] = true
			}
		}
	}

	// 该节点所属的所有组
	for _, g := range groups {
		for _, m := range g.Members {
			if m == nodeID {
				myGroupIDs[g.ID] = true
			}
		}
	}
	// 判断任务是否命中该节点
	taskMatches := func(t *models.Task) bool {
		tgt := t.Target
		switch tgt.Type {
		case "node":
			for _, id := range tgt.NodeIDs {
				if id == nodeID {
					return true
				}
			}
		case "group":
			for _, gid := range tgt.GroupIDs {
				if myGroupIDs[gid] {
					return true
				}
			}
		case "label":
			if v, ok := node.Labels[tgt.LabelKey]; ok && (tgt.LabelValue == "" || v == tgt.LabelValue) {
				return true
			}
		}
		return false
	}

	// 任务集合并按 script 依赖收集 scripts
	neededScripts := map[string]*models.Script{}
	var tasks []*models.Task
	allTasks, err := sm.store.ListTasks(ctx)
	if err != nil {
		return nil, err
	}
	for _, t := range allTasks {
		if !t.Enabled {
			continue
		}
		if !taskMatches(t) {
			continue
		}
		tasks = append(tasks, t)
		if t.ScriptID != "" {
			if s, err := sm.store.GetScript(ctx, t.ScriptID); err == nil && s.Enabled {
				neededScripts[s.ID] = s
			}
		}
	}

	// Schedules：属于命中任务且 execution_owner 或 offline 相关
	allSchedules, err := sm.store.ListSchedules(ctx)
	if err != nil {
		return nil, err
	}
	allowedTasks := map[string]bool{}
	for _, t := range tasks {
		allowedTasks[t.ID] = true
	}
	var schedules []*models.Schedule
	for _, s := range allSchedules {
		// 同步所有属于命中任务的调度（含禁用的），
		// 让 Agent 本地 enabled 状态与 Hub 一致（禁用/启用切换可收敛）。
		if !allowedTasks[s.TaskID] {
			continue
		}
		schedules = append(schedules, s)
	}

	// Applications：assignment 指定
	var apps []*models.Application
	allApps, err := sm.store.ListApplications(ctx)
	if err != nil {
		return nil, err
	}
	for _, a := range allApps {
		assignedNodes, err := sm.store.GetApplicationNodes(ctx, a.ID)
		if err != nil {
			continue
		}
		for _, n := range assignedNodes {
			if n == nodeID {
				// 填充派生字段（Artifact 下载 URL / SHA256），供 Agent-owned 调度部署使用
				apps = append(apps, sm.withArtifactInfo(ctx, a))
				break
			}
		}
	}

	var scripts []*models.Script
	for _, s := range neededScripts {
		scripts = append(scripts, s)
	}

	return &NodeDesiredObjects{
		Scripts:   scripts,
		Tasks:     tasks,
		Schedules: schedules,
		Apps:      apps,
	}, nil
}

// withArtifactInfo 为应用填充 Artifact 下载 URL 与 SHA256（不持久化，仅同步载荷）
func (sm *SyncManager) withArtifactInfo(ctx context.Context, a *models.Application) *models.Application {
	cp := *a
	if a.ArtifactID != "" {
		if art, err := sm.store.GetArtifact(ctx, a.ArtifactID); err == nil {
			cp.ArtifactSHA256 = art.SHA256
			if sm.baseURL != "" {
				cp.ArtifactURL = fmt.Sprintf("%s/api/artifacts/%s/download", sm.baseURL, art.ID)
			}
		}
	}
	return &cp
}

// NotifyChange 变更后通知受影响 Agent
func (sm *SyncManager) NotifyChange(ctx context.Context, objectType, objectID string, objectRev, globalRev int64) {
	notif := protocol.NewEnvelope(protocol.MsgChangeNotif, "", protocol.ChangeNotificationPayload{
		ObjectType:     objectType,
		ObjectID:       objectID,
		ObjectRevision: objectRev,
		GlobalRevision: globalRev,
	})
	for _, nodeID := range sm.sessions.ConnectedNodeIDs() {
		sm.sessions.Notify(nodeID, notif)
	}
}

// NotifyAll 通知所有在线 Agent
func (sm *SyncManager) NotifyAll(ctx context.Context) {
	rev, err := sm.revisions.Current(ctx)
	if err != nil {
		return
	}
	notif := protocol.NewEnvelope(protocol.MsgChangeNotif, "", protocol.ChangeNotificationPayload{
		GlobalRevision: rev,
	})
	for _, nodeID := range sm.sessions.ConnectedNodeIDs() {
		sm.sessions.Notify(nodeID, notif)
	}
}

// NotifyNodeStatus 将维护/禁用状态即时下发给指定 Agent；Revision 对账仍负责最终收敛。
func (sm *SyncManager) NotifyNodeStatus(ctx context.Context, nodeID, status string) {
	sm.sessions.Notify(nodeID, protocol.NewEnvelope(protocol.MsgNodeStatus, "", protocol.NodeStatusPayload{Status: status}))
}

// BuildSyncResponse 构建同步响应
func (sm *SyncManager) BuildSyncResponse(ctx context.Context, nodeID string, since int64) (*protocol.SyncResponsePayload, error) {
	// 检查 Change Log 窗口（运行期从 settings 读取，支持动态调整）
	window := sm.changelogWindow
	if v, err := sm.store.GetSetting(ctx, "changelog_window"); err == nil && v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil && n > 0 {
			window = n
		}
	}
	current, err := sm.revisions.Current(ctx)
	if err != nil {
		return nil, err
	}
	if since > 0 && current-since > window {
		// Full Resync
		objects, err := sm.ComputeDesiredState(ctx, nodeID)
		if err != nil {
			return nil, err
		}
		return sm.buildFullResync(ctx, objects, current), nil
	}

	objects, err := sm.ComputeDesiredState(ctx, nodeID)
	if err != nil {
		return nil, err
	}

	resp := &protocol.SyncResponsePayload{
		FullResync: false,
		Snapshot:   true,
		GlobalRev:  current,
	}

	// 收集 agent 当前已知对象（from local revision 由 Agent 提供）
	// 简单策略：返回该节点全部 Desired 对象，Agent 本地去重比对 Revision。
	// 更精确：用 changelog 过滤。这里对每个对象直接下发（幂等，量小）。
	resp.Scripts = marshalScripts(objects.Scripts)
	resp.Tasks = marshalTasks(objects.Tasks)
	resp.Schedules = marshalSchedules(objects.Schedules)
	resp.Apps = marshalApps(objects.Apps)
	resp.Tombstones = sm.tombstonesSince(ctx, since)
	return resp, nil
}

// tombstonesSince 返回自 since 之后的 Tombstone 列表
func (sm *SyncManager) tombstonesSince(ctx context.Context, since int64) []protocol.Tombstone {
	if since <= 0 {
		return nil
	}
	ts, err := sm.store.ListTombstones(ctx)
	if err != nil {
		return nil
	}
	var out []protocol.Tombstone
	for _, t := range ts {
		if t.GlobalRevision > since {
			out = append(out, protocol.Tombstone{ObjectType: t.ObjectType, ObjectID: t.ObjectID, Revision: t.GlobalRevision})
		}
	}
	return out
}

func (sm *SyncManager) buildFullResync(ctx context.Context, objects *NodeDesiredObjects, current int64) *protocol.SyncResponsePayload {
	return &protocol.SyncResponsePayload{
		FullResync: true,
		Snapshot:   true,
		GlobalRev:  current,
		Scripts:    marshalScripts(objects.Scripts),
		Tasks:      marshalTasks(objects.Tasks),
		Schedules:  marshalSchedules(objects.Schedules),
		Apps:       marshalApps(objects.Apps),
	}
}

func marshalScripts(ss []*models.Script) []protocol.ObjectEntry {
	var out []protocol.ObjectEntry
	for _, s := range ss {
		b, _ := json.Marshal(s)
		out = append(out, protocol.ObjectEntry{ID: s.ID, Revision: s.Revision, Data: b})
	}
	return out
}

func marshalTasks(ts []*models.Task) []protocol.ObjectEntry {
	var out []protocol.ObjectEntry
	for _, t := range ts {
		b, _ := json.Marshal(t)
		out = append(out, protocol.ObjectEntry{ID: t.ID, Revision: t.Revision, Data: b})
	}
	return out
}

func marshalSchedules(ss []*models.Schedule) []protocol.ObjectEntry {
	var out []protocol.ObjectEntry
	for _, s := range ss {
		b, _ := json.Marshal(s)
		out = append(out, protocol.ObjectEntry{ID: s.ID, Revision: s.Revision, Data: b})
	}
	return out
}

func marshalApps(as []*models.Application) []protocol.ObjectEntry {
	var out []protocol.ObjectEntry
	for _, a := range as {
		b, _ := json.Marshal(a)
		out = append(out, protocol.ObjectEntry{ID: a.ID, Revision: a.Revision, Data: b})
	}
	return out
}
