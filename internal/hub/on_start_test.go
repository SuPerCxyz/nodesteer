package hub

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// TestValidateScheduleOnStart on_start 无任何触发时间字段即可通过校验；
// cron/interval/one_time 仍各自要求对应字段（回归），未知类型仍被拒绝。
func TestValidateScheduleOnStart(t *testing.T) {
	base := func(typ string) *models.Schedule {
		return &models.Schedule{
			TaskID:         "task-1",
			Type:           typ,
			ExecutionOwner: models.ExecutionOwnerAgent,
			OfflinePolicy:  models.OfflinePolicyAllowOffline,
			MisfirePolicy:  models.MisfirePolicyRunOnce,
			Enabled:        true,
		}
	}

	// on_start：不提供 expression / interval_sec / run_at / timezone 也必须通过
	if err := validateSchedule(base(models.ScheduleTypeOnStart)); err != nil {
		t.Fatalf("on_start without time fields must validate: %v", err)
	}

	// 回归：cron 仍要求合法 expression
	cronSch := base(models.ScheduleTypeCron)
	if err := validateSchedule(cronSch); err == nil || !strings.Contains(err.Error(), "cron expression") {
		t.Fatalf("cron without expression must fail, got %v", err)
	}
	cronSch.Expression = "*/5 * * * *"
	if err := validateSchedule(cronSch); err != nil {
		t.Fatalf("valid cron must pass: %v", err)
	}

	// 回归：interval 仍要求 interval_sec
	intervalSch := base(models.ScheduleTypeInterval)
	if err := validateSchedule(intervalSch); err == nil || !strings.Contains(err.Error(), "interval_sec") {
		t.Fatalf("interval without interval_sec must fail, got %v", err)
	}
	intervalSch.IntervalSec = 60
	if err := validateSchedule(intervalSch); err != nil {
		t.Fatalf("valid interval must pass: %v", err)
	}

	// 回归：one_time 仍要求 run_at
	oneTimeSch := base(models.ScheduleTypeOneTime)
	if err := validateSchedule(oneTimeSch); err == nil || !strings.Contains(err.Error(), "run_at") {
		t.Fatalf("one_time without run_at must fail, got %v", err)
	}
	oneTimeSch.RunAt = time.Now()
	if err := validateSchedule(oneTimeSch); err != nil {
		t.Fatalf("valid one_time must pass: %v", err)
	}

	// 未知类型仍拒绝，错误信息覆盖新枚举
	err := validateSchedule(base("every_full_moon"))
	if err == nil || !strings.Contains(err.Error(), models.ScheduleTypeOnStart) {
		t.Fatalf("unknown type must be rejected listing on_start, got %v", err)
	}
}

// TestRunDueHUBSkipsOnStart on_start 不参与 hub tick：不建执行、不报错、不记 lastFire。
// newTestScheduleManager 的 execMgr 为 nil，若 on_start 被误判到期会在
// RunScheduledHub 处空指针 panic —— 天然证明其未进入执行分发。
func TestRunDueHUBSkipsOnStart(t *testing.T) {
	ctx := context.Background()
	sm := newTestScheduleManager(t)

	onStart := &models.Schedule{
		TaskID:         "task-1",
		Type:           models.ScheduleTypeOnStart,
		Timezone:       "UTC",
		ExecutionOwner: models.ExecutionOwnerHub,
		OfflinePolicy:  models.OfflinePolicyAllowOffline,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		Enabled:        true,
		CreatedAt:      time.Now().Add(-time.Hour),
	}
	if err := sm.Create(ctx, onStart); err != nil {
		t.Fatalf("create on_start schedule: %v", err)
	}

	next, due, err := sm.computeNextRun(onStart, time.Now())
	if err != nil || due || !next.IsZero() {
		t.Fatalf("on_start must be explicitly skipped: next=%v due=%v err=%v", next, due, err)
	}
	if err := sm.RunDueHUB(ctx, time.Now()); err != nil {
		t.Fatalf("RunDueHUB must not error on on_start: %v", err)
	}
	sm.mu.Lock()
	_, fired := sm.lastFire[onStart.ID]
	sm.mu.Unlock()
	if fired {
		t.Fatal("on_start must not be marked fired by hub tick")
	}
}

// TestOnStartScheduleSyncedToTargetNode 同步下发：on_start 照常进入命中任务的
// 目标节点 Desired State（agent 端才有触发语义），非目标节点不下发。
func TestOnStartScheduleSyncedToTargetNode(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	revisions := NewRevisionManager(st)
	nm := NewNodeManager(st, revisions)
	syncMgr := NewSyncManager(st, revisions, NewSessionManager(), nm)
	scm := NewScheduleManager(st, revisions, syncMgr, nm, nil)

	for _, n := range []*models.Node{
		{ID: "n1", AgentID: "a1", Hostname: "n1", Status: models.NodeStatusOnline},
		{ID: "n2", AgentID: "a2", Hostname: "n2", Status: models.NodeStatusOnline},
	} {
		if err := st.UpsertNode(ctx, n); err != nil {
			t.Fatalf("upsert node %s: %v", n.ID, err)
		}
	}
	task := &models.Task{
		ID: "task-1", Name: "task", Type: models.TaskTypeCommand, Command: "echo",
		Target: models.Target{Type: "node", NodeIDs: []string{"n1"}}, Enabled: true,
	}
	if err := st.CreateTask(ctx, task); err != nil {
		t.Fatalf("create task: %v", err)
	}
	onStart := &models.Schedule{
		TaskID:         task.ID,
		Type:           models.ScheduleTypeOnStart,
		Timezone:       "UTC",
		ExecutionOwner: models.ExecutionOwnerAgent,
		OfflinePolicy:  models.OfflinePolicyAllowOffline,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		Enabled:        true,
	}
	if err := scm.Create(ctx, onStart); err != nil {
		t.Fatalf("create on_start schedule: %v", err)
	}

	hit, err := syncMgr.ComputeDesiredState(ctx, "n1")
	if err != nil {
		t.Fatalf("compute desired state: %v", err)
	}
	if len(hit.Schedules) != 1 || hit.Schedules[0].Type != models.ScheduleTypeOnStart {
		t.Fatalf("on_start must be synced to target node, got %+v", hit.Schedules)
	}
	miss, err := syncMgr.ComputeDesiredState(ctx, "n2")
	if err != nil {
		t.Fatalf("compute desired state for non-target: %v", err)
	}
	if len(miss.Schedules) != 0 {
		t.Fatalf("non-target node must not receive schedule, got %+v", miss.Schedules)
	}
}
