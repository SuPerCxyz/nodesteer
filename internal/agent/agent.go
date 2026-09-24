package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent/condition"
	"github.com/SuPerCxyz/nodesteer/internal/agent/connection"
	"github.com/SuPerCxyz/nodesteer/internal/agent/execution"
	"github.com/SuPerCxyz/nodesteer/internal/agent/host"
	"github.com/SuPerCxyz/nodesteer/internal/agent/scheduler"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/google/uuid"
)

// reasonNodePausedAgent 暂停态拒收/回报的统一原因文案
const reasonNodePausedAgent = "node paused"

// Config Agent 配置
type Config struct {
	HubURL            string
	RegistrationToken string
	AgentID           string
	NodeName          string
	NodeIP            string
	DeploymentMode    string
	HostIntegration   bool
	DataDir           string
	HostRoot          string
	HostPathAllowlist []string
	TLSCAFile         string
	AgentVersion      string
	HeartbeatSec      int
	RevisionCheckSec  int
	MaxLogBytes       int
}

// Agent Agent 主结构
type Agent struct {
	cfg            Config
	store          *LocalStore
	conn           *connection.Manager
	runner         *execution.Runner
	cond           *condition.Engine
	sch            *scheduler.Scheduler
	host           host.HostAdapter
	artifact       *ArtifactCache
	appMgr         *ApplicationManager
	upgrade        *upgradeRunner
	logger         *slog.Logger
	logState       *connection.ConnectionLogState
	nodeID         string
	agentID        string
	credential     string
	ctx            context.Context
	cancel         context.CancelFunc
	ready          bool
	remoteMu       sync.Mutex
	remoteWait     map[string]chan protocol.RemoteStatePayload
	settingsMu     sync.RWMutex
	transferMu     sync.Mutex
	transferCancel map[string]context.CancelFunc
	// on_start 触发状态：startedAt 为本进程启动时刻（作为触发 slot 保证跨启动不同、
	// 同启动幂等）；onStartMu/onStartDone 保证同一次进程启动内至多触发一批。
	startedAt   time.Time
	onStartMu   sync.Mutex
	onStartDone bool
}

// New 创建 Agent
func New(cfg Config, logger *slog.Logger) (*Agent, error) {
	store, err := OpenLocalStore(cfg.DataDir)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())

	// 参数自动生成/推导：deployment_mode 缺省回退 native（cmd/agent 已做显式配置优先的探测）；
	// host_integration 由 deployment_mode 推导，不再独立读配置（yaml/env 旧键忽略）。
	if cfg.DeploymentMode == "" {
		cfg.DeploymentMode = models.DeploymentModeNative
	}
	cfg.HostIntegration = HostIntegrationFromMode(cfg.DeploymentMode)
	// agent_version 空值兜底 dev：上报值以编译注入版本为准（cmd/agent 注入）
	if cfg.AgentVersion == "" {
		cfg.AgentVersion = "dev"
	}

	a := &Agent{
		cfg:            cfg,
		store:          store,
		runner:         execution.New(),
		cond:           condition.New(condition.OSProvider{}),
		host:           host.DefaultHostAdapter(cfg.DeploymentMode),
		logger:         logger,
		logState:       connection.NewConnectionLogState(),
		ctx:            ctx,
		cancel:         cancel,
		remoteWait:     map[string]chan protocol.RemoteStatePayload{},
		transferCancel: map[string]context.CancelFunc{},
		startedAt:      time.Now(),
	}
	// 加载身份
	a.agentID, _ = store.GetIdentity("agent_id")
	if a.agentID == "" {
		a.agentID = cfg.AgentID
	}
	a.nodeID, _ = store.GetIdentity("node_id")
	a.credential, _ = store.GetIdentity("credential")

	// 预留缓存与应用管理器
	a.artifact = NewArtifactCache(filepath.Join(cfg.DataDir, "artifacts"), store, logger, cfg.RegistrationToken)
	a.artifact.SetIdentity(a.agentID, a.credential)
	if cfg.DeploymentMode == models.DeploymentModeDockerHostInt || cfg.HostIntegration {
		a.host = host.NewContainerHostAdapterWithAllowlist(cfg.HostRoot, cfg.HostPathAllowlist)
	}
	a.appMgr = NewApplicationManager(store, a.host, a.artifact, logger)

	// 连接管理器（logState 与 agent 自用共享：失败/成功打点同一窗口同一计数）
	a.conn = connection.New(cfg.HubURL, cfg.RegistrationToken, a.agentID, a, logger, a.logState)
	if err := a.conn.SetTLSCA(cfg.TLSCAFile); err != nil {
		store.Close()
		return nil, fmt.Errorf("configure agent TLS: %w", err)
	}
	a.conn.SetCredential(a.credential)

	// 自升级执行链（默认实现绑定；测试按需覆盖字段注入）
	a.upgrade = a.defaultUpgradeRunner()

	// 调度器
	a.sch = scheduler.New(a, logger)
	a.sch.SetOnlineFunc(a.conn.IsConnected)
	a.cond.Remote = a.queryRemoteCondition

	return a, nil
}

// Close 关闭
func (a *Agent) Close() error {
	a.cancel()
	if a.conn != nil {
		a.conn.Stop()
	}
	return a.store.Close()
}

// Run 启动（阻塞）
func (a *Agent) Run(ctx context.Context) error {
	// 恢复中断执行
	a.recoverInterruptedExecutions()

	// 恢复半完成部署
	a.appMgr.RecoverDeployments(a.ctx)

	// 启动调度器前加载本地已同步的调度（独立于连接状态）
	a.reloadSchedulerSchedules()

	// 启动调度器（本地调度独立于连接）
	a.sch.Start(a.ctx, 1*time.Second)

	// 心跳与 Revision 周期校验
	go a.periodicLoop(ctx)

	// 每小时状态日志（常驻 goroutine，不随连接状态启停；断开由状态机跳过）
	go a.statusLogLoop(ctx)

	// 连接循环
	a.conn.Run(ctx, a.helloFn)

	// 连接恢复后对账（由 handleHelloAck 触发）
	return nil
}

// helloFn 构造 HELLO 载荷
func (a *Agent) helloFn() *protocol.HelloPayload {
	hostname := a.cfg.NodeName
	if hostname == "" {
		hostname, _ = os.Hostname()
	}
	ip := a.cfg.NodeIP
	if ip == "" {
		ip = localIP()
	}
	localRev, _ := a.store.GetGlobalRevision()
	hostRoot := ""
	if a.cfg.HostIntegration || a.cfg.DeploymentMode == models.DeploymentModeDockerHostInt {
		hostRoot = a.host.HostRoot()
	}
	return &protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		AgentID:         a.agentID,
		AgentVersion:    a.cfg.AgentVersion,
		DeploymentMode:  a.cfg.DeploymentMode,
		HostIntegration: a.cfg.HostIntegration,
		Capabilities:    a.capabilities(),
		Hostname:        hostname,
		IP:              ip,
		OS:              readOS(hostRoot),
		Arch:            osArch(),
		LocalGlobalRev:  localRev,
	}
}

// capabilities 能力集
func (a *Agent) capabilities() map[string]bool {
	caps := map[string]bool{
		models.CapScript:            true,
		models.CapLocalScheduler:    true,
		models.CapOfflineExecution:  true,
		models.CapHostFilesystem:    false,
		models.CapManagedSystemd:    false,
		models.CapApplicationDeploy: false,
		// 自升级仅 native 形态支持（docker / docker_host_integration 均不上报）
		models.CapAgentUpgrade: false,
	}
	if a.cfg.DeploymentMode == models.DeploymentModeNative {
		caps[models.CapHostFilesystem] = true
		caps[models.CapManagedSystemd] = true
		caps[models.CapApplicationDeploy] = true
		caps[models.CapAgentUpgrade] = true
	}
	if a.cfg.HostIntegration {
		caps[models.CapHostFilesystem] = true
		caps[models.CapManagedSystemd] = true
		caps[models.CapApplicationDeploy] = true
	}
	return caps
}

// osArch 获取架构
func osArch() string {
	if os.Getenv("NODESTEER_ARCH") != "" {
		return os.Getenv("NODESTEER_ARCH")
	}
	if isArm() {
		return "arm64"
	}
	return "amd64"
}

// periodicLoop 心跳 + Revision 周期校验（运行期设置生效并带 Jitter）
func (a *Agent) periodicLoop(ctx context.Context) {
	t := time.NewTicker(time.Second)
	defer t.Stop()
	now := time.Now()
	nextHeartbeat := now.Add(jitterDuration(a.heartbeatInterval()))
	nextRevision := now.Add(jitterDuration(a.revisionInterval()))
	nextInventory := now.Add(10 * time.Minute)
	for {
		select {
		case <-ctx.Done():
			return
		case now = <-t.C:
			if !now.Before(nextHeartbeat) {
				a.sendHeartbeat()
				nextHeartbeat = now.Add(jitterDuration(a.heartbeatInterval()))
			}
			if !now.Before(nextRevision) {
				a.checkRevision()
				nextRevision = now.Add(jitterDuration(a.revisionInterval()))
			}
			if !now.Before(nextInventory) {
				a.reportInventory()
				nextInventory = now.Add(10 * time.Minute)
			}
		}
	}
}

func (a *Agent) heartbeatInterval() time.Duration {
	a.settingsMu.RLock()
	sec := a.cfg.HeartbeatSec
	a.settingsMu.RUnlock()
	if sec <= 0 {
		sec = 30
	}
	return time.Duration(sec) * time.Second
}

func (a *Agent) revisionInterval() time.Duration {
	a.settingsMu.RLock()
	sec := a.cfg.RevisionCheckSec
	a.settingsMu.RUnlock()
	if sec <= 0 {
		sec = 45
	}
	return time.Duration(sec) * time.Second
}

func (a *Agent) maxLogBytes() int {
	a.settingsMu.RLock()
	n := a.cfg.MaxLogBytes
	a.settingsMu.RUnlock()
	if n <= 0 {
		return 1 << 20
	}
	return n
}

func (a *Agent) sendHeartbeat() {
	if !a.conn.IsConnected() || a.nodeID == "" {
		return
	}
	localRev, _ := a.store.GetGlobalRevision()
	if a.conn.Send(protocol.NewEnvelope(protocol.MsgHeartbeat, "", protocol.HeartbeatPayload{
		NodeID:    a.nodeID,
		GlobalRev: localRev,
	})) {
		a.logState.IncHeartbeatSent()
	}
}

func (a *Agent) checkRevision() {
	if !a.conn.IsConnected() || a.nodeID == "" {
		return
	}
	localRev, _ := a.store.GetGlobalRevision()
	if a.conn.Send(protocol.NewEnvelope(protocol.MsgRevisionCheck, "", protocol.HeartbeatPayload{
		NodeID:    a.nodeID,
		GlobalRev: localRev,
	})) {
		a.logState.IncRevisionChecks()
	}
}

// ---------- 消息处理 ----------

// recordConnectionFailure 连接类失败打点：汇入共享 ConnectionLogState（与 manager 的
// connection lost 同一失败窗口同一计数，双行叠加按每分钟合计 ≤1 行节流），
// 放行时输出 ERROR（原每周期刷屏行由此替代）。
func (a *Agent) recordConnectionFailure(msg, errText string) {
	snap, ok := a.logState.RecordFailure(errText, time.Now())
	if !ok {
		return
	}
	a.logger.Error(msg,
		"error", snap.Error,
		"fail_duration", snap.FailDuration,
		"failures_this_minute", snap.FailuresThisMinute,
		"failures_total", snap.FailuresTotal)
}

func (a *Agent) OnHelloAck(env protocol.Envelope) {
	var p protocol.HelloAckPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		a.recordConnectionFailure("bad hello ack", err.Error())
		return
	}
	if !p.Accepted {
		a.recordConnectionFailure("hello rejected", p.Message)
		// Credential 失效时下一次连接使用 Registration Token 重新换取凭证。
		a.credential = ""
		a.store.SetIdentity("credential", "")
		a.conn.SetCredential("")
		a.artifact.SetIdentity(a.agentID, a.cfg.RegistrationToken)
		return
	}
	// 首次/恢复成功：1 行 INFO（含失败摘要），并重置失败窗口与计数。
	// accepted 是唯一成功准绳（传输层 connected 早于本点，不得作为依据）。
	summary := a.logState.RecordSuccess(time.Now())
	a.logger.Info("agent connected",
		"recovered", summary.Recovered,
		"fail_duration", summary.Duration,
		"failures", summary.Failures)
	// READY 门禁：对账完成前不视为就绪，不接收新任务
	a.ready = false
	// 保存身份
	if p.NodeID != "" {
		a.nodeID = p.NodeID
		a.store.SetIdentity("node_id", p.NodeID)
	}
	a.sch.SetPaused(p.NodeStatus == models.NodeStatusMaintenance || p.NodeStatus == models.NodeStatusDisabled)
	if p.AgentID != "" {
		a.agentID = p.AgentID
		a.store.SetIdentity("agent_id", p.AgentID)
		a.conn.SetAgentID(p.AgentID)
	}
	if p.AgentCredential != "" {
		a.credential = p.AgentCredential
		a.store.SetIdentity("credential", p.AgentCredential)
		a.conn.SetCredential(p.AgentCredential)
		a.artifact.SetIdentity(a.agentID, p.AgentCredential)
	}
	a.applySettingsMap(p.Settings)

	// Reconnect Reconciliation：配置对账
	localRev, _ := a.store.GetGlobalRevision()
	if localRev < p.DesiredGlobalRev {
		a.requestSync(localRev)
	} else {
		a.syncConfigReconcile(localRev)
	}

	// Execution Reconciliation：上报未同步执行
	a.reconcileExecutions()

	// 上报 Inventory（低频，连接建立时）
	a.reportInventory()
}

func (a *Agent) applySettingsMap(values map[string]string) {
	a.settingsMu.Lock()
	defer a.settingsMu.Unlock()
	if v, err := strconv.Atoi(values["heartbeat_interval_sec"]); err == nil && v > 0 {
		a.cfg.HeartbeatSec = v
	}
	if v, err := strconv.Atoi(values["revision_check_interval_sec"]); err == nil && v > 0 {
		a.cfg.RevisionCheckSec = v
	}
	if v, err := strconv.Atoi(values["max_log_bytes"]); err == nil && v > 0 {
		a.cfg.MaxLogBytes = v
	}
}

func (a *Agent) applySettings(p protocol.SettingsPayload) {
	a.settingsMu.Lock()
	defer a.settingsMu.Unlock()
	if p.HeartbeatSec > 0 {
		a.cfg.HeartbeatSec = p.HeartbeatSec
	}
	if p.RevisionCheckSec > 0 {
		a.cfg.RevisionCheckSec = p.RevisionCheckSec
	}
	if p.MaxLogBytes > 0 {
		a.cfg.MaxLogBytes = p.MaxLogBytes
	}
}

// reportInventory 上报宿主机 Inventory
func (a *Agent) reportInventory() {
	if !a.conn.IsConnected() {
		return
	}
	hostRoot := ""
	if a.cfg.HostIntegration || a.cfg.DeploymentMode == models.DeploymentModeDockerHostInt {
		hostRoot = a.host.HostRoot()
	}
	inv := collectInventory(a.cfg.DeploymentMode, a.cfg.HostIntegration, hostRoot)
	payload := protocol.InventoryPayload{NodeID: a.nodeID, Available: inv != nil}
	if inv != nil {
		payload.Inventory = convertToProtocolInventory(inv)
	}
	if a.conn.Send(protocol.NewEnvelope(protocol.MsgInventory, "", payload)) {
		a.logState.IncInventoryReports()
	}
}

// logStatusIfDue 成功期每小时状态行：以 accepted 为准绳（connectedAt 非零），
// 未 accepted 或已进入失败态时跳过。返回是否输出。
func (a *Agent) logStatusIfDue(now time.Time) bool {
	snap, ok := a.logState.TakeStatus(now)
	if !ok {
		return false
	}
	rev, _ := a.store.GetGlobalRevision()
	a.logger.Info("agent status",
		"uptime", snap.Uptime,
		"heartbeats", snap.Heartbeats,
		"inventory_reports", snap.InventoryReports,
		"revision_checks", snap.RevisionChecks,
		"revision", rev)
	return true
}

// statusLogLoop 常驻状态日志循环：固定 ticker 驱动，不随连接状态启停（防 goroutine 泄漏），
// 断开/未 accepted 由 logStatusIfDue（状态机 connectedAt）跳过打印。
func (a *Agent) statusLogLoop(ctx context.Context) {
	t := time.NewTicker(connection.StatusLogInterval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-t.C:
			a.logStatusIfDue(now)
		}
	}
}

// convertToProtocolInventory models → protocol Inventory
func convertToProtocolInventory(inv *models.Inventory) *protocol.Inventory {
	m := &protocol.Inventory{OS: inv.OS, OSVersion: inv.OSVersion, Kernel: inv.Kernel, Arch: inv.Arch}
	for _, c := range inv.CPU {
		m.CPU = append(m.CPU, protocol.InventoryCPU{Model: c.Model, Cores: c.Cores, MHz: c.MHz})
	}
	if inv.Memory != nil {
		m.Memory = &protocol.InventoryMemory{TotalKB: inv.Memory.TotalKB, AvailableKB: inv.Memory.AvailableKB}
	}
	for _, f := range inv.Filesystem {
		m.Filesystem = append(m.Filesystem, protocol.InventoryFS{Mount: f.Mount, Device: f.Device, FSType: f.FSType, TotalKB: f.TotalKB, FreeKB: f.FreeKB})
	}
	for _, n := range inv.Network {
		m.Network = append(m.Network, protocol.InventoryNet{Interface: n.Interface, Addresses: n.Addresses, MAC: n.MAC})
	}
	return m
}

func (a *Agent) OnHeartbeatAck(env protocol.Envelope) {
	var p protocol.SettingsPayload
	if json.Unmarshal(env.Payload, &p) == nil {
		a.applySettings(p)
	}
}

func (a *Agent) OnSettings(env protocol.Envelope) {
	var p protocol.SettingsPayload
	if json.Unmarshal(env.Payload, &p) == nil {
		a.applySettings(p)
	}
}

// OnNodeStatus 应用 Hub 下发的节点维护/禁用状态。
func (a *Agent) OnNodeStatus(env protocol.Envelope) {
	var p protocol.NodeStatusPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return
	}
	a.sch.SetPaused(p.Status == models.NodeStatusMaintenance || p.Status == models.NodeStatusDisabled)
	// 恢复运行态时补做本进程尚未消费的 on_start 触发（启动时处于暂停态则延后到此）。
	if !a.sch.IsPaused() {
		a.runOnStartTriggers()
	}
}

// reloadSchedulerSchedules 从本地存储加载调度到调度器
func (a *Agent) reloadSchedulerSchedules() {
	schedules, err := a.store.ListSchedules(a.ctx)
	if err != nil {
		return
	}
	var schs []*models.Schedule
	for _, def := range schedules {
		var s models.Schedule
		if err := json.Unmarshal(def, &s); err == nil {
			schs = append(schs, &s)
		}
	}
	a.sch.UpdateSchedules(schs)
}

// runOnStartTriggers on_start 调度的进程级一次性触发（任务触发 v2）。
//
// 时序：进程启动 → 首次成功应用 sync（OnSyncResponse，本地 store 已含 Hub 下发
// 与历史缓存合并后的调度）→ 筛选 target 命中本节点的 on_start → 各触发一次。
//   - 挂接点是 sync 完成而非 HELLO accepted：仅网络重连不会重复触发；
//   - onStartDone 保证同一次进程启动内幂等（重复 sync/重连/状态推送不再触发）；
//   - scheduledAt 固定为本进程启动时刻，TriggerSchedule 按 task+node+slot 幂等兜底，
//     跨启动 slot 不同（重启再触发）；
//   - 离线启动：启动阶段仅加载本地缓存（reloadSchedulerSchedules）不触发，
//     连上并完成首次 sync 后在此补触发一次；Hub 始终不可达则不触发；
//   - 节点暂停（maintenance/disabled）时不消费本启动的触发额度，恢复后由
//     OnNodeStatus 或后续 sync 补触发。
func (a *Agent) runOnStartTriggers() {
	a.onStartMu.Lock()
	if a.onStartDone || a.sch.IsPaused() {
		a.onStartMu.Unlock()
		return
	}
	schedules, err := a.store.ListSchedules(a.ctx)
	if err != nil {
		// 读取失败不消费触发额度，等待下次 sync 重试
		a.onStartMu.Unlock()
		return
	}
	a.onStartDone = true
	a.onStartMu.Unlock()

	for _, def := range schedules {
		var sch models.Schedule
		if err := json.Unmarshal(def, &sch); err != nil {
			continue
		}
		if sch.Type != models.ScheduleTypeOnStart || !sch.Enabled {
			continue
		}
		taskDef, err := a.store.GetTask(a.ctx, sch.TaskID)
		if err != nil {
			a.logger.Warn("on_start trigger skipped: task unavailable",
				"schedule", sch.ID, "task", sch.TaskID, "error", err)
			continue
		}
		var t models.Task
		if err := json.Unmarshal(taskDef, &t); err != nil || !t.Enabled {
			continue
		}
		if !a.onStartTargetsThisNode(&t) {
			continue
		}
		if a.logger != nil {
			a.logger.Info("on_start schedule trigger",
				"schedule", sch.ID, "task", sch.TaskID, "at", a.startedAt)
		}
		if err := a.TriggerSchedule(a.ctx, &sch, a.startedAt); err != nil && a.logger != nil {
			a.logger.Warn("on_start schedule trigger failed", "schedule", sch.ID, "error", err)
		}
	}
}

// onStartTargetsThisNode 判断 on_start 所属任务的目标是否命中本节点。
// node 目标在本地精确比对；group/label 目标由 Hub 同步侧按目标过滤
// （SyncManager.ComputeDesiredState 的 taskMatches），能下发到本地即视为命中。
func (a *Agent) onStartTargetsThisNode(t *models.Task) bool {
	switch t.Target.Type {
	case "group", "label":
		return true
	case "node":
		if a.nodeID == "" {
			return false
		}
		for _, id := range t.Target.NodeIDs {
			if id == a.nodeID {
				return true
			}
		}
		return false
	default:
		// target 类型由 Hub 校验限定为 node|group|label，未知类型不触发
		return false
	}
}

func (a *Agent) OnChangeNotification(env protocol.Envelope) {
	var p protocol.ChangeNotificationPayload
	json.Unmarshal(env.Payload, &p)
	localRev, _ := a.store.GetGlobalRevision()
	if p.GlobalRevision > localRev {
		a.requestSync(localRev)
	}
}

func (a *Agent) OnRevisionCheckAck(env protocol.Envelope) {
	var p protocol.HeartbeatPayload
	json.Unmarshal(env.Payload, &p)
	localRev, _ := a.store.GetGlobalRevision()
	if p.GlobalRev > localRev {
		a.requestSync(localRev)
	}
}

func (a *Agent) OnSyncAck(env protocol.Envelope) {
	var p protocol.SyncAckPayload
	json.Unmarshal(env.Payload, &p)
}

// requestSync 请求同步
func (a *Agent) requestSync(since int64) {
	if !a.conn.IsConnected() {
		return
	}
	a.conn.Send(protocol.NewEnvelope(protocol.MsgSyncRequest, "", protocol.SyncRequestPayload{
		Since: since,
	}))
}

// syncConfigReconcile 重连配置对账（无变化时全量核对）
func (a *Agent) syncConfigReconcile(localRev int64) {
	a.requestSync(localRev)
}

// reconcileExecutions 重连执行对账
func (a *Agent) reconcileExecutions() {
	execs, err := a.store.ListUnsyncedExecutions(a.ctx)
	if err != nil {
		return
	}
	for _, e := range execs {
		a.uploadExecution(e)
	}
}

// uploadExecution 上传执行结果
func (a *Agent) uploadExecution(e *LocalExecution) {
	if !a.conn.IsConnected() {
		return
	}
	payload := protocol.ExecutionFinishedPayload{
		ExecutionID:          e.ID,
		TaskID:               e.TaskID,
		TaskRevision:         e.TaskRevision,
		ScriptID:             e.ScriptID,
		ScriptRevision:       e.ScriptRevision,
		ApplicationID:        e.ApplicationID,
		ApplicationVersion:   e.ApplicationVersion,
		ApplicationOperation: e.ApplicationOperation,
		ApplicationHealth:    e.ApplicationHealth,
		StartTime:            e.StartTime,
		NodeID:               e.NodeID,
		TriggerType:          e.TriggerType,
		ScheduledTime:        e.ScheduledTime,
		Status:               e.Status,
		ExitCode:             e.ExitCode,
		Stdout:               e.Stdout,
		Stderr:               e.Stderr,
		StdoutTruncated:      e.StdoutTruncated,
		StderrTruncated:      e.StderrTruncated,
		EndTime:              e.EndTime,
		Offline:              e.Offline,
		BlockReason:          e.BlockReason,
	}
	a.conn.Send(protocol.NewEnvelope(protocol.MsgExecFinished, e.ID, payload))
}

// OnExecutionAck 只有 Hub 确认收到结果后才结束本地 Journal 的同步状态。
func (a *Agent) OnExecutionAck(env protocol.Envelope) {
	var p protocol.ExecutionAckPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil || !p.OK {
		return
	}
	ex, err := a.store.GetExecution(a.ctx, p.ExecutionID)
	if err != nil || ex == nil {
		return
	}
	ex.Synced = true
	if err := a.store.UpdateExecution(a.ctx, ex); err != nil {
		a.logger.Warn("mark execution synced failed", "id", p.ExecutionID, "error", err)
	}
}

func (a *Agent) OnSyncResponse(env protocol.Envelope) {
	var p protocol.SyncResponsePayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		a.logger.Error("bad sync response", "error", err)
		return
	}
	if p.Snapshot || p.FullResync {
		a.cleanupPrunedApplications(p.Apps)
	}
	if err := a.applySync(&p); err != nil {
		a.logger.Error("apply sync failed", "error", err)
		a.conn.Send(protocol.NewEnvelope(protocol.MsgSyncAck, env.ID, protocol.SyncAckPayload{
			AppliedRev: 0, OK: false, Error: err.Error(),
		}))
		return
	}
	a.conn.Send(protocol.NewEnvelope(protocol.MsgSyncAck, env.ID, protocol.SyncAckPayload{
		AppliedRev: p.GlobalRev,
		OK:         true,
	}))
	// 对账完成，进入 READY 状态
	a.ready = true
	// on_start：本进程首次成功同步后触发一次（挂接点是 sync 而非 HELLO accepted，
	// 保证仅网络重连不触发；离线启动在连上并同步后于此补触发一次）。
	a.runOnStartTriggers()
}

func (a *Agent) cleanupPrunedApplications(entries []protocol.ObjectEntry) {
	desired := make(map[string]bool, len(entries))
	for _, entry := range entries {
		if !entry.Deleted {
			desired[entry.ID] = true
		}
	}
	defs, err := a.store.ListApplications(a.ctx)
	if err != nil {
		return
	}
	for _, def := range defs {
		var app models.Application
		if json.Unmarshal(def, &app) == nil && app.ID != "" && !desired[app.ID] {
			if err := a.appMgr.CleanupApplication(a.ctx, app.ID); err != nil {
				a.logger.Warn("cleanup pruned application failed", "app", app.ID, "error", err)
			}
		}
	}
}

// applySync 原子应用同步结果
func (a *Agent) applySync(p *protocol.SyncResponsePayload) (err error) {
	// 原子同步：BEGIN → 写入 → COMMIT；任何失败 ROLLBACK，不产生半同步状态
	if err := a.store.BeginSync(a.ctx); err != nil {
		return err
	}
	synced := false
	defer func() {
		if !synced {
			if rbErr := a.store.RollbackSync(); rbErr != nil {
				a.logger.Warn("sync rollback failed", "error", rbErr)
			}
		}
	}()
	// Sync Plan：先脚本，再任务，再调度，再应用
	if p.Scripts != nil {
		for _, entry := range p.Scripts {
			if entry.Deleted {
				a.store.DeleteScript(a.ctx, entry.ID)
				continue
			}
			var sc models.Script
			if err := json.Unmarshal(entry.Data, &sc); err != nil {
				return err
			}
			if err := a.store.UpsertScript(a.ctx, &ScriptRow{
				ID: sc.ID, Name: sc.Name, Interpreter: sc.Interpreter, Content: sc.Content,
				Parameters: marshalJSON(sc.Parameters), Environment: marshalJSON(sc.Environment),
				WorkingDir: sc.WorkingDir, RunUser: sc.RunUser, Timeout: sc.Timeout, Enabled: sc.Enabled,
				Revision: sc.Revision, SHA256: sc.SHA256,
			}); err != nil {
				return err
			}
		}
	}
	if p.Tasks != nil {
		for _, entry := range p.Tasks {
			if entry.Deleted {
				a.store.DeleteTask(a.ctx, entry.ID)
				continue
			}
			var t models.Task
			if err := json.Unmarshal(entry.Data, &t); err != nil {
				return err
			}
			if err := a.store.UpsertTask(a.ctx, t.ID, entry.Data, t.Revision, t.Enabled); err != nil {
				return err
			}
		}
	}
	if p.Schedules != nil {
		for _, entry := range p.Schedules {
			if entry.Deleted {
				a.store.DeleteSchedule(a.ctx, entry.ID)
				a.sch.RemoveSchedule(entry.ID)
				continue
			}
			var s models.Schedule
			if err := json.Unmarshal(entry.Data, &s); err != nil {
				return err
			}
			if err := a.store.UpsertSchedule(a.ctx, s.ID, entry.Data, s.Revision, s.Enabled); err != nil {
				return err
			}
		}
	}
	if p.Apps != nil {
		for _, entry := range p.Apps {
			if entry.Deleted {
				if err := a.appMgr.CleanupApplication(a.ctx, entry.ID); err != nil {
					a.logger.Warn("cleanup removed application failed", "app", entry.ID, "error", err)
				}
				a.store.DeleteApplication(a.ctx, entry.ID)
				continue
			}
			if err := a.store.UpsertApplication(a.ctx, entry.ID, entry.Data, entry.Revision); err != nil {
				return err
			}
		}
	}
	if p.Snapshot || p.FullResync {
		var scriptIDs, taskIDs, scheduleIDs, appIDs []string
		for _, entry := range p.Scripts {
			if !entry.Deleted {
				scriptIDs = append(scriptIDs, entry.ID)
			}
		}
		for _, entry := range p.Tasks {
			if !entry.Deleted {
				taskIDs = append(taskIDs, entry.ID)
			}
		}
		for _, entry := range p.Schedules {
			if !entry.Deleted {
				scheduleIDs = append(scheduleIDs, entry.ID)
			}
		}
		for _, entry := range p.Apps {
			if !entry.Deleted {
				appIDs = append(appIDs, entry.ID)
			}
		}
		if err := a.store.PruneObjects(a.ctx, scriptIDs, taskIDs, scheduleIDs, appIDs); err != nil {
			return err
		}
	}
	// Tombstone
	for _, ts := range p.Tombstones {
		switch ts.ObjectType {
		case models.ObjectScript:
			a.store.DeleteScript(a.ctx, ts.ObjectID)
		case models.ObjectTask:
			a.store.DeleteTask(a.ctx, ts.ObjectID)
		case models.ObjectSchedule:
			a.store.DeleteSchedule(a.ctx, ts.ObjectID)
			a.sch.RemoveSchedule(ts.ObjectID)
		case models.ObjectApplication:
			a.appMgr.CleanupApplication(a.ctx, ts.ObjectID)
			a.store.DeleteApplication(a.ctx, ts.ObjectID)
		}
	}
	// Revision 前进（成功持久化后）
	if err := a.store.SetGlobalRevision(p.GlobalRev); err != nil {
		return err
	}
	// 提交同步事务
	if err := a.store.CommitSync(); err != nil {
		return err
	}
	synced = true
	// 事务提交后再更新调度器：事务外读取可见全部已提交调度，
	// 避免 WAL 下读到未提交数据导致新调度未进入本地调度器
	a.reloadSchedulerSchedules()
	a.prefetchApplications(p.Apps)
	return nil
}

func (a *Agent) prefetchApplications(entries []protocol.ObjectEntry) {
	for _, entry := range entries {
		if entry.Deleted {
			continue
		}
		var app models.Application
		if err := json.Unmarshal(entry.Data, &app); err != nil || app.ArtifactURL == "" || app.ArtifactSHA256 == "" {
			continue
		}
		go func(app models.Application) {
			if err := a.artifact.Prefetch(a.ctx, app.ArtifactURL, app.ArtifactSHA256); err != nil {
				a.logger.Warn("application artifact prefetch failed", "app", app.ID, "error", err)
			}
		}(app)
	}
}

func (a *Agent) OnRunExecution(env protocol.Envelope) {
	var p protocol.RunExecutionPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		a.logger.Error("bad run execution", "error", err)
		return
	}
	// 执行前先写 Journal
	ex := &LocalExecution{
		ID:                 p.ExecutionID,
		TaskID:             p.TaskID,
		TaskRevision:       p.TaskRevision,
		ScriptID:           p.ScriptID,
		ScriptRevision:     p.ScriptRevision,
		ApplicationID:      p.AppID,
		ApplicationVersion: p.ApplicationVersion,
		NodeID:             a.nodeID,
		TriggerType:        p.TriggerType,
		ScheduledTime:      p.ScheduledTime,
		Status:             models.ExecStatusRunning,
		StartTime:          time.Now().UTC().Format(time.RFC3339Nano),
		Offline:            !a.conn.IsConnected(),
		Synced:             false,
	}
	if err := a.store.CreateExecution(a.ctx, ex); err != nil {
		// 幂等：已存在则忽略
		if existing, _ := a.store.GetExecution(a.ctx, p.ExecutionID); existing != nil && existing.Status != models.ExecStatusRunning {
			return
		}
		if existing, _ := a.store.GetExecution(a.ctx, p.ExecutionID); existing != nil && existing.Status == models.ExecStatusRunning {
			return
		}
		a.logger.Error("create execution journal failed", "id", p.ExecutionID, "error", err)
		return
	}

	// READY 门禁：对账未完成前拒绝 Hub 下发执行（journal 已写，确保状态可上报）
	if !a.ready {
		a.logger.Warn("execution rejected: not ready", "id", p.ExecutionID)
		a.finishExecution(p.ExecutionID, models.ExecStatusBlocked, -1, "", "", false, false, "agent not ready (reconciling)", true)
		return
	}

	// PAUSED 门禁：节点暂停（maintenance/disabled）时拒收执行指令并回报，不启动本地执行
	if a.sch.IsPaused() {
		a.logger.Warn("execution rejected: node paused", "id", p.ExecutionID)
		if a.conn.IsConnected() {
			a.conn.Send(protocol.NewEnvelope(protocol.MsgError, p.ExecutionID, protocol.ErrorPayload{
				Code: "node_paused", Message: "execution rejected: node paused",
			}))
		}
		a.finishExecution(p.ExecutionID, models.ExecStatusBlocked, -1, "", "", false, false, reasonNodePausedAgent, true)
		return
	}

	// 通知已开始
	if a.conn.IsConnected() {
		a.conn.Send(protocol.NewEnvelope(protocol.MsgExecStarted, p.ExecutionID, protocol.ExecutionStartedPayload{
			ExecutionID: p.ExecutionID,
			StartTime:   ex.StartTime,
		}))
	}

	// Agent 自升级走独立执行链（下载→SHA256 校验→备份→替换→重启→回滚），
	// 其余类型走通用执行器；均在独立 goroutine 中，避免阻塞 dispatch 循环。
	if p.Type == models.TaskTypeAgentUpgrade {
		go a.runAgentUpgrade(p)
		return
	}
	go a.executeTask(p, ex)
}

// executeTask 执行任务
func (a *Agent) executeTask(p protocol.RunExecutionPayload, ex *LocalExecution) {
	// Condition 评估（独立 goroutine，可发起远程状态查询而不会阻塞 dispatch）
	if len(p.Condition) > 0 {
		var c models.Condition
		if err := json.Unmarshal(p.Condition, &c); err == nil {
			ok, evaluated, err := a.cond.Evaluate(a.ctx, &c)
			if err != nil || !evaluated {
				// Fail Closed → BLOCKED
				a.finishExecution(p.ExecutionID, models.ExecStatusBlocked, -1, "", "", false, false, "condition unknown: "+fmt.Sprint(err), true)
				return
			}
			if !ok {
				a.finishExecution(p.ExecutionID, models.ExecStatusSkipped, 0, "", "", false, false, "condition not satisfied", true)
				return
			}
		}
	}

	var command, script, interpreter string
	var env map[string]string

	// 应用 Task 走应用管理器
	if p.Type == models.TaskTypeAppDeploy || p.Type == models.TaskTypeAppOperation {
		a.runAppOperation(p, ex)
		return
	}

	switch p.Type {
	case models.TaskTypeCommand:
		command = p.Command
	case models.TaskTypeScript:
		// 从本地同步的脚本加载
		if p.ScriptContent != "" {
			script = p.ScriptContent
			interpreter = p.Interpreter
		} else if p.ScriptID != "" {
			if sc, err := a.store.GetScript(a.ctx, p.ScriptID); err == nil {
				script = sc.Content
				interpreter = sc.Interpreter
				if p.RunUser == "" {
					p.RunUser = sc.RunUser
				}
			}
		}
	default:
		command = p.Command
	}
	env = p.Environment

	maxBytes := a.cfg.MaxLogBytes
	if maxBytes <= 0 {
		maxBytes = 1 << 20
	}
	timeout := time.Duration(p.Timeout) * time.Second
	if timeout <= 0 {
		timeout = 300 * time.Second
	}

	execID := p.ExecutionID
	// Retry 循环：失败（FAILED/TIMED_OUT）时按 Retry 次数重试
	attempts := p.Retry
	if attempts < 0 {
		attempts = 0
	}
	var lastResult *execution.Result
	var lastErr error
	for attempt := 0; attempt <= attempts; attempt++ {
		lastResult, lastErr = a.runner.Run(a.ctx, execID, execution.RunConfig{
			Command:        command,
			Script:         script,
			Interpreter:    interpreter,
			WorkingDir:     p.WorkingDir,
			RunUser:        p.RunUser,
			Environment:    env,
			Timeout:        timeout,
			MaxStdoutBytes: maxBytes,
			MaxStderrBytes: maxBytes,
			MaxTotalBytes:  maxBytes * 2,
			OnLogChunk: func(stream string, chunk []byte) {
				if len(chunk) == 0 || !a.conn.IsConnected() {
					return
				}
				// 复制数据：chunk 复用 runner 内部 buffer，异步发送前必须拷贝
				data := redact(string(chunk), p.SecretValues)
				a.conn.Send(protocol.NewEnvelope(protocol.MsgLogChunk, execID, protocol.LogChunkPayload{
					ExecutionID: execID,
					Stream:      stream,
					Chunk:       data,
				}))
			},
		})
		if lastErr != nil {
			break
		}
		// 仅失败状态重试；成功/取消/跳过不再重试
		if lastResult.Status != models.ExecStatusFailed && lastResult.Status != models.ExecStatusTimedOut {
			break
		}
		if attempt < attempts {
			a.logger.Info("execution retry", "id", execID, "attempt", attempt+1, "of", attempts, "status", lastResult.Status)
		}
	}
	if lastErr != nil {
		a.finishExecution(p.ExecutionID, models.ExecStatusFailed, -1, "", "", false, false, lastErr.Error(), false)
		return
	}
	result := lastResult
	status := result.Status
	if status != models.ExecStatusSuccess && status != models.ExecStatusFailed &&
		status != models.ExecStatusTimedOut && status != models.ExecStatusCanceled {
		if result.ExitCode == 0 {
			status = models.ExecStatusSuccess
		} else {
			status = models.ExecStatusFailed
		}
	}
	a.finishExecution(p.ExecutionID, status, result.ExitCode, redact(result.Stdout, p.SecretValues), redact(result.Stderr, p.SecretValues),
		result.StdoutTruncated, result.StderrTruncated, "", false)
}

// finishExecution 完成执行并持久化
func (a *Agent) finishExecution(execID, status string, exitCode int, stdout, stderr string, outT, errT bool, blockReason string, forceOffline bool) {
	ex, err := a.store.GetExecution(a.ctx, execID)
	if err != nil || ex == nil {
		return
	}
	ex.Status = status
	ex.ExitCode = exitCode
	maxLogBytes := a.maxLogBytes()
	ex.Stdout = truncate(ex.Stdout, stdout, maxLogBytes)
	ex.Stderr = truncate(ex.Stderr, stderr, maxLogBytes)
	ex.StdoutTruncated = ex.StdoutTruncated || outT
	ex.StderrTruncated = ex.StderrTruncated || errT
	ex.EndTime = time.Now().UTC().Format(time.RFC3339Nano)
	ex.BlockReason = blockReason
	if forceOffline {
		ex.Offline = true
	}
	ex.Synced = false
	if err := a.store.UpdateExecution(a.ctx, ex); err != nil {
		a.logger.Error("update execution failed", "id", execID, "error", err)
		return
	}
	a.sendExecutionFinished(ex)
}

// sendExecutionFinished 从 journal 上报执行终态（EXECUTION_FINISHED）。
// 连接断开时不发送；journal 保持 Synced=false，由重连对账（reconcileExecutions）补齐。
func (a *Agent) sendExecutionFinished(ex *LocalExecution) {
	if !a.conn.IsConnected() {
		return
	}
	a.conn.Send(protocol.NewEnvelope(protocol.MsgExecFinished, ex.ID, protocol.ExecutionFinishedPayload{
		ExecutionID:     ex.ID,
		TaskID:          ex.TaskID,
		TaskRevision:    ex.TaskRevision,
		ScriptID:        ex.ScriptID,
		ScriptRevision:  ex.ScriptRevision,
		NodeID:          ex.NodeID,
		TriggerType:     ex.TriggerType,
		ScheduledTime:   ex.ScheduledTime,
		Status:          ex.Status,
		ExitCode:        ex.ExitCode,
		Stdout:          ex.Stdout,
		Stderr:          ex.Stderr,
		StdoutTruncated: ex.StdoutTruncated,
		StderrTruncated: ex.StderrTruncated,
		StartTime:       ex.StartTime,
		EndTime:         ex.EndTime,
		Offline:         ex.Offline,
		BlockReason:     ex.BlockReason,
	}))
}

func (a *Agent) OnCancelExecution(env protocol.Envelope) {
	var p protocol.CancelExecutionPayload
	json.Unmarshal(env.Payload, &p)
	a.runner.Cancel(p.ExecutionID)
}

func (a *Agent) OnArtifactPrefetch(env protocol.Envelope) {
	var p protocol.ArtifactPrefetchPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return
	}
	if err := a.artifact.Prefetch(a.ctx, p.URL, p.SHA256); err != nil {
		a.logger.Warn("artifact prefetch failed", "id", p.ArtifactID, "error", err)
	}
}

// OnFileUploadRequest 上传源 Agent 文件到 Hub。二进制走独立 HTTP 数据通道。
func (a *Agent) OnFileUploadRequest(env protocol.Envelope) {
	var p protocol.FileUploadRequestPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		a.logger.Error("bad file upload request", "error", err)
		return
	}
	ctx, done := a.startFileTransfer(p.TransferID)
	go func() {
		defer done()
		err := a.uploadFile(ctx, p)
		a.sendFileUploadResult(p.TransferID, err)
	}()
}

func (a *Agent) uploadFile(ctx context.Context, p protocol.FileUploadRequestPayload) error {
	if err := a.validateFileTransferPath(p.SourcePath); err != nil {
		return fmt.Errorf("source path: %w", err)
	}
	offset := p.Offset
	for attempt := 0; attempt < 5; attempt++ {
		f, info, err := a.host.OpenRead(ctx, p.SourcePath)
		if err != nil {
			return err
		}
		if !info.Mode().IsRegular() {
			f.Close()
			return errors.New("source is not a regular file")
		}
		if offset < 0 || offset > info.Size() {
			f.Close()
			return fmt.Errorf("invalid upload offset %d", offset)
		}
		if seeker, ok := f.(io.Seeker); ok {
			if _, err := seeker.Seek(offset, io.SeekStart); err != nil {
				f.Close()
				return err
			}
		}
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, p.UploadURL, io.LimitReader(f, info.Size()-offset))
		if err != nil {
			f.Close()
			return err
		}
		req.ContentLength = info.Size() - offset
		req.Header.Set("X-NodeSteer-Agent-ID", a.agentID)
		req.Header.Set("X-NodeSteer-Agent-Token", a.credential)
		req.Header.Set("X-NodeSteer-File-Size", strconv.FormatInt(info.Size(), 10))
		req.Header.Set("X-NodeSteer-File-Mode", strconv.FormatUint(uint64(info.Mode().Perm()), 8))
		if info.Size() > 0 {
			req.Header.Set("Content-Range", fmt.Sprintf("bytes %d-%d/%d", offset, info.Size()-1, info.Size()))
		}
		resp, requestErr := a.conn.HTTPClient().Do(req)
		f.Close()
		if requestErr != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			time.Sleep(time.Duration(attempt+1) * 500 * time.Millisecond)
			continue
		}
		var result struct {
			NextOffset int64  `json:"next_offset"`
			Error      string `json:"error"`
		}
		json.NewDecoder(resp.Body).Decode(&result)
		resp.Body.Close()
		switch resp.StatusCode {
		case http.StatusOK:
			return nil
		case http.StatusConflict, http.StatusPermanentRedirect:
			if result.NextOffset >= 0 && result.NextOffset <= info.Size() {
				offset = result.NextOffset
				continue
			}
		}
		if result.Error != "" {
			return errors.New(result.Error)
		}
		return fmt.Errorf("upload status %d", resp.StatusCode)
	}
	return errors.New("upload retry limit exceeded")
}

// OnFileDeliveryRequest 下载 Hub 文件并原子替换目标路径。
func (a *Agent) OnFileDeliveryRequest(env protocol.Envelope) {
	var p protocol.FileDeliveryRequestPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		a.logger.Error("bad file delivery request", "error", err)
		return
	}
	ctx, done := a.startFileTransfer(p.TransferID)
	go func() {
		defer done()
		err := a.downloadFile(ctx, p)
		a.sendFileDeliveryResult(p.TransferID, err)
	}()
}

func (a *Agent) downloadFile(ctx context.Context, p protocol.FileDeliveryRequestPayload) error {
	if err := a.validateFileTransferPath(p.DestinationPath); err != nil {
		return fmt.Errorf("destination path: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.DownloadURL, nil)
	if err != nil {
		return err
	}
	req.Header.Set("X-NodeSteer-Agent-ID", a.agentID)
	req.Header.Set("X-NodeSteer-Agent-Token", a.credential)
	resp, err := a.conn.HTTPClient().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download status %d", resp.StatusCode)
	}
	mode := os.FileMode(p.Mode & 0o777)
	if mode == 0 {
		mode = 0o644
	}
	reader := &verifyingFileReader{reader: resp.Body, hash: sha256.New(), expectedSize: p.Size, expectedSHA: strings.ToLower(p.SHA256)}
	if err := a.host.AtomicReplaceReader(ctx, p.DestinationPath, reader, mode); err != nil {
		return err
	}
	return reader.verify()
}

type verifyingFileReader struct {
	reader       io.Reader
	hash         hash.Hash
	bytes        int64
	expectedSize int64
	expectedSHA  string
	verified     bool
}

func (r *verifyingFileReader) Read(p []byte) (int, error) {
	n, err := r.reader.Read(p)
	if n > 0 {
		r.bytes += int64(n)
		_, _ = r.hash.Write(p[:n])
	}
	if err == io.EOF {
		if verifyErr := r.verify(); verifyErr != nil {
			return n, verifyErr
		}
	}
	return n, err
}

func (r *verifyingFileReader) verify() error {
	if r.verified {
		return nil
	}
	r.verified = true
	if r.expectedSize >= 0 && r.bytes != r.expectedSize {
		return fmt.Errorf("file size mismatch: expected %d got %d", r.expectedSize, r.bytes)
	}
	if r.expectedSHA != "" && hex.EncodeToString(r.hash.Sum(nil)) != r.expectedSHA {
		return fmt.Errorf("sha256 mismatch")
	}
	return nil
}

func (a *Agent) OnFileTransferCancel(env protocol.Envelope) {
	var p protocol.FileTransferCancelPayload
	if json.Unmarshal(env.Payload, &p) != nil {
		return
	}
	a.transferMu.Lock()
	if cancel := a.transferCancel[p.TransferID]; cancel != nil {
		cancel()
	}
	a.transferMu.Unlock()
}

func (a *Agent) startFileTransfer(id string) (context.Context, func()) {
	ctx, cancel := context.WithCancel(a.ctx)
	a.transferMu.Lock()
	if old := a.transferCancel[id]; old != nil {
		old()
	}
	a.transferCancel[id] = cancel
	a.transferMu.Unlock()
	return ctx, func() {
		a.transferMu.Lock()
		if current := a.transferCancel[id]; current != nil {
			delete(a.transferCancel, id)
		}
		a.transferMu.Unlock()
	}
}

func (a *Agent) sendFileUploadResult(id string, err error) {
	if !a.conn.IsConnected() {
		return
	}
	p := protocol.FileUploadResultPayload{TransferID: id, OK: err == nil}
	if err != nil {
		p.Error = err.Error()
	}
	a.conn.Send(protocol.NewEnvelope(protocol.MsgFileUploadResult, id, p))
}

func (a *Agent) sendFileDeliveryResult(id string, err error) {
	if !a.conn.IsConnected() {
		return
	}
	p := protocol.FileDeliveryResultPayload{TransferID: id, OK: err == nil}
	if err != nil {
		p.Error = err.Error()
	}
	a.conn.Send(protocol.NewEnvelope(protocol.MsgFileDeliveryResult, id, p))
}

func (a *Agent) validateFileTransferPath(path string) error {
	if err := a.host.ValidatePath(path); err != nil {
		return err
	}
	clean := filepath.Clean(path)
	if len(a.cfg.HostPathAllowlist) == 0 {
		return nil
	}
	for _, prefix := range a.cfg.HostPathAllowlist {
		prefix = filepath.Clean(prefix)
		if clean == prefix || strings.HasPrefix(clean, prefix+string(filepath.Separator)) {
			return nil
		}
	}
	return fmt.Errorf("path is outside configured allowlist: %s", path)
}

func (a *Agent) OnDeployRequest(env protocol.Envelope) {
	var p protocol.DeployRequestPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		a.logger.Error("bad deploy request", "error", err)
		return
	}
	execID := env.ID
	if execID == "" {
		execID = uuid.NewString()
	}
	ex := &LocalExecution{
		ID: execID, TaskID: p.AppID, ApplicationID: p.AppID, ApplicationVersion: p.AppVersion,
		ApplicationOperation: p.Operation, NodeID: a.nodeID, TriggerType: models.TriggerManual,
		Status: models.ExecStatusRunning, StartTime: time.Now().UTC().Format(time.RFC3339Nano),
		Offline: !a.conn.IsConnected(), Synced: false,
	}
	if err := a.store.CreateExecution(a.ctx, ex); err != nil {
		if existing, _ := a.store.GetExecution(a.ctx, execID); existing != nil {
			return
		}
		a.logger.Error("create deployment journal failed", "id", execID, "error", err)
		return
	}
	callback := a.deployResultCallback(execID)
	if !a.ready {
		a.logger.Warn("deploy request rejected: not ready", "app", p.AppID)
		callback(protocol.DeployResultPayload{AppID: p.AppID, Operation: p.Operation, OK: false, Error: "agent not ready (reconciling)"})
		return
	}
	// PAUSED 门禁：节点暂停（maintenance/disabled）时拒收部署指令并回报，不启动本地部署
	if a.sch.IsPaused() {
		a.logger.Warn("deploy request rejected: node paused", "app", p.AppID, "exec", execID)
		if a.conn.IsConnected() {
			a.conn.Send(protocol.NewEnvelope(protocol.MsgError, execID, protocol.ErrorPayload{
				Code: "node_paused", Message: "deploy request rejected: node paused",
			}))
		}
		callback(protocol.DeployResultPayload{AppID: p.AppID, Operation: p.Operation, OK: false, Error: reasonNodePausedAgent})
		return
	}
	go a.appMgr.HandleDeployRequest(a.ctx, p, execID, callback)
}

// deployResultCallback 返回结果回调
func (a *Agent) deployResultCallback(execID string) func(p protocol.DeployResultPayload) {
	return func(p protocol.DeployResultPayload) {
		p.ExecutionID = execID
		if ex, err := a.store.GetExecution(a.ctx, execID); err == nil && ex != nil {
			p.StartTime = ex.StartTime
			p.TaskID = ex.TaskID
			p.TaskRevision = ex.TaskRevision
			p.TriggerType = ex.TriggerType
			p.ScheduledTime = ex.ScheduledTime
			ex.ApplicationID = p.AppID
			ex.ApplicationVersion = p.Version
			ex.ApplicationOperation = p.Operation
			ex.ApplicationHealth = p.Health
			if p.OK {
				ex.Status = models.ExecStatusSuccess
			} else {
				ex.Status = models.ExecStatusFailed
			}
			ex.EndTime = time.Now().UTC().Format(time.RFC3339Nano)
			ex.BlockReason = p.Error
			ex.Offline = ex.Offline || !a.conn.IsConnected()
			ex.Synced = false
			if err := a.store.UpdateExecution(a.ctx, ex); err != nil {
				a.logger.Warn("update deployment journal failed", "id", execID, "error", err)
			}
		}
		if a.conn.IsConnected() {
			a.conn.Send(protocol.NewEnvelope(protocol.MsgDeployResult, execID, p))
		}
	}
}

func (a *Agent) OnError(env protocol.Envelope) {
	var p protocol.ErrorPayload
	json.Unmarshal(env.Payload, &p)
	a.logger.Error("hub error", "code", p.Code, "message", p.Message)
}

// TriggerSchedule 调度触发（实现 scheduler.Executor）
func (a *Agent) TriggerSchedule(ctx context.Context, sch *models.Schedule, scheduledAt time.Time) error {
	taskDef, err := a.store.GetTask(ctx, sch.TaskID)
	if err != nil {
		return err
	}
	var t models.Task
	if err := json.Unmarshal(taskDef, &t); err != nil {
		return err
	}
	// 逻辑 Key 幂等
	slot := scheduledAt.UTC().Truncate(time.Second).Format(time.RFC3339Nano)
	if existing, err := a.store.FindExecutionBySlot(ctx, sch.TaskID, a.nodeID, slot); err == nil && existing != nil {
		return nil
	}
	ex := &LocalExecution{
		ID:            uuid.NewString(),
		TaskID:        sch.TaskID,
		TaskRevision:  t.Revision,
		ScriptID:      t.ScriptID,
		ApplicationID: t.ApplicationID,
		NodeID:        a.nodeID,
		TriggerType:   models.TriggerSchedule,
		ScheduledTime: slot,
		Status:        models.ExecStatusRunning,
		StartTime:     time.Now().UTC().Format(time.RFC3339Nano),
		Offline:       !a.conn.IsConnected(),
		Synced:        false,
	}
	if err := a.store.CreateExecution(ctx, ex); err != nil {
		return err
	}
	// 构建运行载荷
	p := protocol.RunExecutionPayload{
		ExecutionID:        ex.ID,
		TaskID:             t.ID,
		TaskRevision:       t.Revision,
		ScriptID:           t.ScriptID,
		Type:               t.Type,
		Command:            t.Command,
		Timeout:            t.Timeout,
		Retry:              t.Retry,
		RunUser:            t.RunUser,
		AppID:              t.ApplicationID,
		ApplicationVersion: applicationVersion(a.store, t.ApplicationID),
		AppOperation:       t.AppOperation,
		TriggerType:        models.TriggerSchedule,
		ScheduledTime:      slot,
	}
	values := map[string]string{}
	for k, v := range t.ParamValues {
		values[k] = v
	}
	for _, def := range t.Parameters {
		if def.Default != "" && values[def.Name] == "" {
			values[def.Name] = def.Default
		}
	}
	var scriptParams []models.Parameter
	if t.Type == models.TaskTypeScript && t.ScriptID != "" {
		if sc, err := a.store.GetScript(ctx, t.ScriptID); err == nil {
			p.ScriptContent = sc.Content
			p.Interpreter = sc.Interpreter
			p.ScriptRevision = sc.Revision
			ex.ScriptRevision = sc.Revision
			a.store.UpdateExecution(ctx, ex)
			p.WorkingDir = sc.WorkingDir
			if p.RunUser == "" {
				p.RunUser = sc.RunUser
			}
			json.Unmarshal(sc.Environment, &p.Environment)
			json.Unmarshal(sc.Parameters, &scriptParams)
			for _, def := range scriptParams {
				if def.Default != "" && values[def.Name] == "" {
					values[def.Name] = def.Default
				}
				if def.Required && values[def.Name] == "" {
					return fmt.Errorf("required parameter %s is missing", def.Name)
				}
			}
		}
	}
	for _, def := range t.Parameters {
		if def.Required && values[def.Name] == "" {
			return fmt.Errorf("required parameter %s is missing", def.Name)
		}
	}
	if p.Environment == nil {
		p.Environment = map[string]string{}
	}
	for k, v := range values {
		p.Environment["NODESTEER_PARAM_"+k] = v
	}
	p.SecretValues = localSecretValues(t.Parameters, scriptParams, values)
	if t.Condition != nil {
		b, _ := json.Marshal(t.Condition)
		p.Condition = b
	}
	go a.executeTask(p, ex)
	return nil
}

func localSecretValues(taskParams, scriptParams []models.Parameter, values map[string]string) []string {
	var out []string
	for _, def := range append(append([]models.Parameter{}, taskParams...), scriptParams...) {
		if def.Type == "secret" && values[def.Name] != "" {
			out = append(out, values[def.Name])
		}
	}
	return out
}

func redact(value string, secrets []string) string {
	for _, secret := range secrets {
		if secret != "" {
			value = strings.ReplaceAll(value, secret, "[REDACTED]")
		}
	}
	return value
}

func applicationVersion(st *LocalStore, id string) string {
	if id == "" {
		return ""
	}
	for _, def := range mustListApplications(st) {
		var app models.Application
		if json.Unmarshal(def, &app) == nil && app.ID == id {
			return app.Version
		}
	}
	return ""
}

func mustListApplications(st *LocalStore) [][]byte {
	apps, _ := st.ListApplications(context.Background())
	return apps
}

// runAppOperation 应用操作执行
func (a *Agent) runAppOperation(p protocol.RunExecutionPayload, ex *LocalExecution) {
	// 通过应用管理器处理
	a.appMgr.HandleRunOperation(a.ctx, p, ex, a.deployResultCallback(ex.ID))
}

// recoverInterruptedExecutions Agent 重启恢复
func (a *Agent) recoverInterruptedExecutions() {
	execs, err := a.store.ListRunningExecutions(a.ctx)
	if err != nil {
		return
	}
	for _, e := range execs {
		e.Status = models.ExecStatusFailed
		e.BlockReason = "agent_restarted"
		e.EndTime = time.Now().UTC().Format(time.RFC3339Nano)
		e.Synced = false
		a.store.UpdateExecution(a.ctx, e)
	}
}

// queryRemoteCondition 远程条件查询（通过 Hub 实时查询目标节点状态）
func (a *Agent) queryRemoteCondition(ctx context.Context, rc *models.RemoteCondition) (string, bool, error) {
	if rc == nil {
		return "", false, nil
	}
	if !a.conn.IsConnected() {
		return "", false, nil
	}
	reqID := uuid.NewString()
	respCh := make(chan protocol.RemoteStatePayload, 1)
	a.remoteMu.Lock()
	a.remoteWait[reqID] = respCh
	a.remoteMu.Unlock()
	defer func() {
		a.remoteMu.Lock()
		delete(a.remoteWait, reqID)
		a.remoteMu.Unlock()
	}()

	if !a.conn.Send(protocol.NewEnvelope(protocol.MsgRemoteStateReq, reqID, protocol.RemoteStateReqPayload{
		TargetNodeID: rc.NodeID,
		Property:     rc.Property,
		TaskID:       rc.TaskID,
	})) {
		return "", false, nil
	}

	// 等待 Hub 响应（短超时，避免条件求值无限阻塞）
	select {
	case p := <-respCh:
		if p.Value == "" || p.Value == protocol.RemoteStateUnknown {
			// 空值或 UNKNOWN → Fail Closed（视为不可评估）
			return "", false, nil
		}
		return p.Value, true, nil
	case <-ctx.Done():
		return "", false, nil
	case <-time.After(5 * time.Second):
		return "", false, nil
	}
}

// OnRemoteState 处理 Hub 返回的远程状态查询结果
func (a *Agent) OnRemoteState(env protocol.Envelope) {
	var p protocol.RemoteStatePayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return
	}
	a.remoteMu.Lock()
	ch, ok := a.remoteWait[env.ID]
	a.remoteMu.Unlock()
	if ok {
		select {
		case ch <- p:
		default:
		}
	}
}

// Truncate 日志截断
func truncate(current, incoming string, limit int) string {
	if limit <= 0 {
		limit = 1 << 20
	}
	if len(current) >= limit {
		return current
	}
	avail := limit - len(current)
	if len(incoming) > avail {
		return current + incoming[:avail]
	}
	return current + incoming
}
