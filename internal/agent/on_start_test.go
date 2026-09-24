package agent

import (
	"encoding/json"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// onStartTestNode 本机节点 ID（helper 直接注入身份，模拟已完成注册的进程）
const onStartTestNode = "node-onstart-1"

// newOnStartTestAgent 构造 on_start 测试 Agent（不注册 t.Cleanup，
// 由各测试自行管理生命周期，重启用例需显式 Close 首个进程且不得二次 Close）。
func newOnStartTestAgent(t *testing.T, dataDir string) *Agent {
	t.Helper()
	a, err := New(Config{
		HubURL:            "ws://127.0.0.1:8443",
		RegistrationToken: "test-token",
		NodeName:          "on-start-node",
		DataDir:           dataDir,
		DeploymentMode:    models.DeploymentModeNative,
		AgentVersion:      "v0.0.0-test",
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatalf("new agent: %v", err)
	}
	a.nodeID = onStartTestNode
	_ = a.store.SetIdentity("node_id", onStartTestNode)
	return a
}

// onStartTask 构造目标命中/未命中本节点的 command 任务
func onStartTask(id, targetNode string, enabled bool) *models.Task {
	return &models.Task{
		ID: id, Name: id, Type: models.TaskTypeCommand, Command: "true",
		Target:  models.Target{Type: "node", NodeIDs: []string{targetNode}},
		Timeout: 5, Enabled: enabled, Revision: 1,
	}
}

// onStartSchedule 构造 enabled 可配的 on_start 调度（agent owner）
func onStartSchedule(id, taskID string, enabled bool) *models.Schedule {
	return &models.Schedule{
		ID: id, TaskID: taskID, Type: models.ScheduleTypeOnStart,
		Timezone:       "UTC",
		ExecutionOwner: models.ExecutionOwnerAgent,
		OfflinePolicy:  models.OfflinePolicyAllowOffline,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		Enabled:        enabled, Revision: 1,
		CreatedAt: time.Now(),
	}
}

// onStartSyncEnv 构造带任务/调度条目的 Snapshot 同步响应（对齐 Hub 下发形态）
func onStartSyncEnv(t *testing.T, tasks []*models.Task, schedules []*models.Schedule) protocol.Envelope {
	t.Helper()
	p := protocol.SyncResponsePayload{GlobalRev: 5, Snapshot: true}
	for _, tk := range tasks {
		raw, err := json.Marshal(tk)
		if err != nil {
			t.Fatalf("marshal task: %v", err)
		}
		p.Tasks = append(p.Tasks, protocol.ObjectEntry{ID: tk.ID, Revision: tk.Revision, Data: raw})
	}
	for _, sc := range schedules {
		raw, err := json.Marshal(sc)
		if err != nil {
			t.Fatalf("marshal schedule: %v", err)
		}
		p.Schedules = append(p.Schedules, protocol.ObjectEntry{ID: sc.ID, Revision: sc.Revision, Data: raw})
	}
	raw, err := json.Marshal(p)
	if err != nil {
		t.Fatalf("marshal sync payload: %v", err)
	}
	return protocol.Envelope{Type: protocol.MsgSyncResponse, Payload: raw}
}

// onStartSlot 复刻 TriggerSchedule 的 slot 计算（task+node+slot 幂等 key）
func onStartSlot(a *Agent) string {
	return a.startedAt.UTC().Truncate(time.Second).Format(time.RFC3339Nano)
}

// countTaskExecutions 统计某任务在本机的执行 journal 行数
func countTaskExecutions(t *testing.T, a *Agent, taskID string) int {
	t.Helper()
	var n int
	if err := a.store.DB().QueryRowContext(a.ctx,
		`SELECT COUNT(*) FROM executions WHERE task_id = ?`, taskID).Scan(&n); err != nil {
		t.Fatalf("count executions: %v", err)
	}
	return n
}

// TestOnStartTriggersOncePerProcessStart 进程启动一次 → 首次 sync 触发一次；
// 同启动内重复 sync、HELLO accepted 重连及其触发的 sync 均不再触发；
// 触发后的执行走正常执行链完成（进入执行历史）。
func TestOnStartTriggersOncePerProcessStart(t *testing.T) {
	a := newOnStartTestAgent(t, t.TempDir())
	t.Cleanup(func() { _ = a.Close() })

	task := onStartTask("task-onstart", a.nodeID, true)
	sch := onStartSchedule("sch-onstart", task.ID, true)
	env := onStartSyncEnv(t, []*models.Task{task}, []*models.Schedule{sch})

	a.OnSyncResponse(env)
	if n := countTaskExecutions(t, a, task.ID); n != 1 {
		t.Fatalf("first sync must trigger exactly once, got %d", n)
	}
	ex, err := a.store.FindExecutionBySlot(a.ctx, task.ID, a.nodeID, onStartSlot(a))
	if err != nil || ex == nil {
		t.Fatalf("on_start execution journal missing: %v", err)
	}
	if ex.TriggerType != models.TriggerSchedule {
		t.Fatalf("trigger_type = %q, want %q", ex.TriggerType, models.TriggerSchedule)
	}

	// 同一次进程启动内重复 sync 不再触发
	a.OnSyncResponse(env)
	a.OnSyncResponse(env)
	// 重连：HELLO accepted 再来一次 + 其触发的 sync 不再触发
	a.OnHelloAck(helloAckEnvelope(t, protocol.HelloAckPayload{
		Accepted: true, NodeID: a.nodeID, DesiredGlobalRev: 5,
	}))
	a.OnSyncResponse(env)
	if n := countTaskExecutions(t, a, task.ID); n != 1 {
		t.Fatalf("repeated sync/reconnect must not retrigger, got %d", n)
	}

	// 触发的执行正常走执行链完成（进入执行历史）
	deadline := time.Now().Add(5 * time.Second)
	for {
		cur, err := a.store.GetExecution(a.ctx, ex.ID)
		if err != nil || cur == nil {
			t.Fatalf("get execution: %v", err)
		}
		if cur.Status == models.ExecStatusSuccess {
			break
		}
		if cur.Status == models.ExecStatusFailed || cur.Status == models.ExecStatusBlocked ||
			cur.Status == models.ExecStatusTimedOut {
			t.Fatalf("on_start execution ended badly: status=%q reason=%q", cur.Status, cur.BlockReason)
		}
		if time.Now().After(deadline) {
			t.Fatalf("on_start execution did not finish, status=%q", cur.Status)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// TestOnStartReTriggersAfterProcessRestart 重启再触发：同一 DataDir 重新构造
// Agent（模拟进程重启）后再次触发，slot 跨启动不同；新启动内重复 sync 不再触发。
func TestOnStartReTriggersAfterProcessRestart(t *testing.T) {
	dir := t.TempDir()
	a1 := newOnStartTestAgent(t, dir)
	task := onStartTask("task-onstart-restart", a1.nodeID, true)
	sch := onStartSchedule("sch-onstart-restart", task.ID, true)
	env := onStartSyncEnv(t, []*models.Task{task}, []*models.Schedule{sch})

	a1.OnSyncResponse(env)
	if n := countTaskExecutions(t, a1, task.ID); n != 1 {
		t.Fatalf("first process must trigger once, got %d", n)
	}
	slot1 := onStartSlot(a1)
	// 显式关闭首个进程（不注册 cleanup，避免二次 Close）
	if err := a1.Close(); err != nil {
		t.Fatalf("close first process: %v", err)
	}

	a2 := newOnStartTestAgent(t, dir)
	t.Cleanup(func() { _ = a2.Close() })
	if a2.nodeID != onStartTestNode {
		t.Fatalf("node identity must persist across restart, got %q", a2.nodeID)
	}
	// 真实重启跨秒（slot 为秒级幂等粒度），显式推进启动时刻模拟
	a2.startedAt = a1.startedAt.Add(3 * time.Second)
	slot2 := onStartSlot(a2)
	if slot1 == slot2 {
		t.Fatalf("restart must produce distinct slot, both %q", slot1)
	}

	a2.OnSyncResponse(env)
	if n := countTaskExecutions(t, a2, task.ID); n != 2 {
		t.Fatalf("restart must trigger again, got %d executions", n)
	}
	a2.OnSyncResponse(env)
	if n := countTaskExecutions(t, a2, task.ID); n != 2 {
		t.Fatalf("same process start must stay idempotent, got %d", n)
	}
}

// TestOnStartSkipsWhenTargetMissesNode target 不含本节点：不触发。
func TestOnStartSkipsWhenTargetMissesNode(t *testing.T) {
	a := newOnStartTestAgent(t, t.TempDir())
	t.Cleanup(func() { _ = a.Close() })

	task := onStartTask("task-onstart-miss", "other-node", true)
	sch := onStartSchedule("sch-onstart-miss", task.ID, true)
	env := onStartSyncEnv(t, []*models.Task{task}, []*models.Schedule{sch})

	a.OnSyncResponse(env)
	if n := countTaskExecutions(t, a, task.ID); n != 0 {
		t.Fatalf("non-matching target must not trigger, got %d", n)
	}
	a.OnSyncResponse(env)
	if n := countTaskExecutions(t, a, task.ID); n != 0 {
		t.Fatalf("non-matching target must stay untriggered on resync, got %d", n)
	}
}

// TestOnStartSkipsDisabled schedule/enabled=false 或任务禁用均不触发。
func TestOnStartSkipsDisabled(t *testing.T) {
	t.Run("schedule disabled", func(t *testing.T) {
		a := newOnStartTestAgent(t, t.TempDir())
		t.Cleanup(func() { _ = a.Close() })
		task := onStartTask("task-onstart-sch-disabled", a.nodeID, true)
		sch := onStartSchedule("sch-onstart-disabled", task.ID, false)
		a.OnSyncResponse(onStartSyncEnv(t, []*models.Task{task}, []*models.Schedule{sch}))
		if n := countTaskExecutions(t, a, task.ID); n != 0 {
			t.Fatalf("disabled schedule must not trigger, got %d", n)
		}
	})
	t.Run("task disabled", func(t *testing.T) {
		a := newOnStartTestAgent(t, t.TempDir())
		t.Cleanup(func() { _ = a.Close() })
		task := onStartTask("task-onstart-task-disabled", a.nodeID, false)
		sch := onStartSchedule("sch-onstart-task-disabled", task.ID, true)
		a.OnSyncResponse(onStartSyncEnv(t, []*models.Task{task}, []*models.Schedule{sch}))
		if n := countTaskExecutions(t, a, task.ID); n != 0 {
			t.Fatalf("disabled task must not trigger, got %d", n)
		}
	})
}

// TestOnStartOfflineStartDefersUntilFirstSync 离线启动路径：启动阶段仅加载本地
// 缓存（Run 的 reloadSchedulerSchedules）不触发；HELLO accepted 也不触发；
// 连上并完成首次 sync 后补触发一次，之后同启动内幂等。
func TestOnStartOfflineStartDefersUntilFirstSync(t *testing.T) {
	a := newOnStartTestAgent(t, t.TempDir())
	t.Cleanup(func() { _ = a.Close() })

	task := onStartTask("task-onstart-offline", a.nodeID, true)
	sch := onStartSchedule("sch-onstart-offline", task.ID, true)
	// 上一进程同步遗留的本地缓存（离线启动时已存在）
	taskRaw, err := json.Marshal(task)
	if err != nil {
		t.Fatalf("marshal task: %v", err)
	}
	schRaw, err := json.Marshal(sch)
	if err != nil {
		t.Fatalf("marshal schedule: %v", err)
	}
	// UpsertTask/UpsertSchedule 仅在同步事务内可用（与 applySync 同路径）
	if err := a.store.BeginSync(a.ctx); err != nil {
		t.Fatalf("begin seed tx: %v", err)
	}
	seedErr := a.store.UpsertTask(a.ctx, task.ID, taskRaw, 1, true)
	if seedErr == nil {
		seedErr = a.store.UpsertSchedule(a.ctx, sch.ID, schRaw, 1, true)
	}
	if seedErr != nil {
		_ = a.store.RollbackSync()
		t.Fatalf("seed local cache: %v", seedErr)
	}
	if err := a.store.CommitSync(); err != nil {
		t.Fatalf("commit seed: %v", err)
	}

	// 启动阶段：仅把本地缓存加载进调度器，离线不触发
	a.reloadSchedulerSchedules()
	if n := countTaskExecutions(t, a, task.ID); n != 0 {
		t.Fatalf("offline start must not trigger from local cache alone, got %d", n)
	}

	// 连上（HELLO accepted）仍不触发：触发点在 sync 完成后
	a.OnHelloAck(helloAckEnvelope(t, protocol.HelloAckPayload{
		Accepted: true, NodeID: a.nodeID,
	}))
	if n := countTaskExecutions(t, a, task.ID); n != 0 {
		t.Fatalf("hello accepted alone must not trigger, got %d", n)
	}

	// 连上并同步到 schedule 后补触发一次
	env := onStartSyncEnv(t, []*models.Task{task}, []*models.Schedule{sch})
	a.OnSyncResponse(env)
	if n := countTaskExecutions(t, a, task.ID); n != 1 {
		t.Fatalf("first sync after offline start must trigger once, got %d", n)
	}
	a.OnSyncResponse(env)
	if n := countTaskExecutions(t, a, task.ID); n != 1 {
		t.Fatalf("same process start must stay idempotent, got %d", n)
	}
}

// TestOnStartDeferredWhileNodePaused 启动时处于暂停态（maintenance/disabled）：
// 不消费本启动的触发额度，恢复运行态后补触发一次且幂等。
func TestOnStartDeferredWhileNodePaused(t *testing.T) {
	a := newOnStartTestAgent(t, t.TempDir())
	t.Cleanup(func() { _ = a.Close() })

	task := onStartTask("task-onstart-paused", a.nodeID, true)
	sch := onStartSchedule("sch-onstart-paused", task.ID, true)
	env := onStartSyncEnv(t, []*models.Task{task}, []*models.Schedule{sch})

	a.sch.SetPaused(true)
	a.OnSyncResponse(env)
	if n := countTaskExecutions(t, a, task.ID); n != 0 {
		t.Fatalf("paused node must not trigger, got %d", n)
	}

	online := protocol.NewEnvelope(protocol.MsgNodeStatus, "",
		protocol.NodeStatusPayload{Status: models.NodeStatusOnline})
	a.OnNodeStatus(online)
	if n := countTaskExecutions(t, a, task.ID); n != 1 {
		t.Fatalf("resume must trigger exactly once, got %d", n)
	}
	a.OnNodeStatus(online)
	if n := countTaskExecutions(t, a, task.ID); n != 1 {
		t.Fatalf("repeated resume must stay idempotent, got %d", n)
	}
}
