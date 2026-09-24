package scheduler

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

type mockExecutor struct {
	mu       sync.Mutex
	triggers []time.Time
}

func (m *mockExecutor) TriggerSchedule(_ context.Context, _ *models.Schedule, at time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.triggers = append(m.triggers, at)
	return nil
}

// TestOnStartNotTickedByLocalScheduler on_start 不参与本地周期 tick：
// nextRun 显式返回未到期（不报错），checkDue/Start 循环不触发；
// 触发由 Agent 进程启动一次性路径负责。
func TestOnStartNotTickedByLocalScheduler(t *testing.T) {
	exec := &mockExecutor{}
	s := New(exec, nil)
	now := time.Date(2026, 1, 1, 10, 0, 0, 0, time.UTC)
	sch := &models.Schedule{
		ID:             "s-on-start",
		TaskID:         "t1",
		Type:           models.ScheduleTypeOnStart,
		Timezone:       "UTC",
		ExecutionOwner: models.ExecutionOwnerAgent,
		Enabled:        true,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		CreatedAt:      now.Add(-time.Hour),
	}

	if _, due, err := s.nextRun(sch, now); err != nil || due {
		t.Fatalf("on_start must be explicitly not-due without error: due=%v err=%v", due, err)
	}

	s.UpdateSchedules([]*models.Schedule{sch})
	s.checkDue(context.Background(), now)
	exec.mu.Lock()
	n := len(exec.triggers)
	exec.mu.Unlock()
	if n != 0 {
		t.Fatalf("on_start must not be ticked by local scheduler, got %d triggers", n)
	}
}

// TestIntervalSkipLateSync 验证：interval+SKIP 且调度创建很久后才被加载（晚同步），
// 不会因 anchor 停留在过去而永不触发；加载后应能按周期正常触发。
func TestIntervalSkipLateSync(t *testing.T) {
	exec := &mockExecutor{}
	s := New(exec, nil)
	// 创建时间在 5 分钟前（模拟晚同步），interval 30s
	created := time.Now().Add(-5 * time.Minute)
	sch := &models.Schedule{
		ID:             "sch1",
		TaskID:         "task1",
		Type:           models.ScheduleTypeInterval,
		IntervalSec:    30,
		MisfirePolicy:  models.MisfirePolicySkip,
		ExecutionOwner: models.ExecutionOwnerAgent,
		Timezone:       "UTC",
		Enabled:        true,
		CreatedAt:      created,
	}
	s.UpdateSchedules([]*models.Schedule{sch})

	// 第一轮：应 SKIP（错过多个周期），不触发，但推进 lastFire
	now := time.Now()
	s.checkDue(context.Background(), now)
	exec.mu.Lock()
	n := len(exec.triggers)
	exec.mu.Unlock()
	if n != 0 {
		t.Fatalf("first round should skip, got %d triggers", n)
	}

	// 模拟经过一个周期后：应正常触发
	next := now.Add(31 * time.Second)
	s.checkDue(context.Background(), next)
	exec.mu.Lock()
	n = len(exec.triggers)
	exec.mu.Unlock()
	if n != 1 {
		t.Fatalf("expected 1 trigger after one period, got %d", n)
	}

	// 再经过一个周期：应再次触发
	next2 := next.Add(30 * time.Second)
	s.checkDue(context.Background(), next2)
	exec.mu.Lock()
	n = len(exec.triggers)
	exec.mu.Unlock()
	if n != 2 {
		t.Fatalf("expected 2 triggers, got %d", n)
	}
}

// TestPausedBlocksLocalSchedule T8 锁定：paused 优先于到期判断，暂停期间本地调度
// 不触发；解除暂停后同一到期点恢复触发（未因暂停丢失）。
func TestPausedBlocksLocalSchedule(t *testing.T) {
	exec := &mockExecutor{}
	s := New(exec, nil)
	created := time.Now()
	sch := &models.Schedule{
		ID: "sch-paused", TaskID: "task-paused",
		Type: models.ScheduleTypeInterval, IntervalSec: 30,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		ExecutionOwner: models.ExecutionOwnerAgent,
		Timezone:       "UTC", Enabled: true, CreatedAt: created,
	}
	s.UpdateSchedules([]*models.Schedule{sch})

	due := created.Add(31 * time.Second)
	s.SetPaused(true)
	s.checkDue(context.Background(), due)
	exec.mu.Lock()
	n := len(exec.triggers)
	exec.mu.Unlock()
	if n != 0 {
		t.Fatalf("paused scheduler must not trigger local schedules, got %d", n)
	}
	if !s.IsPaused() {
		t.Fatal("IsPaused must reflect SetPaused(true)")
	}

	s.SetPaused(false)
	s.checkDue(context.Background(), due)
	exec.mu.Lock()
	n = len(exec.triggers)
	exec.mu.Unlock()
	if n != 1 {
		t.Fatalf("expected 1 trigger after resume, got %d", n)
	}
	if s.IsPaused() {
		t.Fatal("IsPaused must reflect SetPaused(false)")
	}
}

// TestUnpausedOfflineSchedulePolicy T8 回归：未暂停时离线调度策略照常生效——
// allow_offline 离线可触发，hub_online_required 离线不触发。
func TestUnpausedOfflineSchedulePolicy(t *testing.T) {
	newSchedule := func(id, policy string, created time.Time) *models.Schedule {
		return &models.Schedule{
			ID: id, TaskID: "task-" + id,
			Type: models.ScheduleTypeInterval, IntervalSec: 30,
			MisfirePolicy:  models.MisfirePolicyRunOnce,
			ExecutionOwner: models.ExecutionOwnerAgent,
			OfflinePolicy:  policy,
			Timezone:       "UTC", Enabled: true, CreatedAt: created,
		}
	}
	created := time.Now()
	due := created.Add(31 * time.Second)

	// 离线 + allow_offline → 触发
	allowExec := &mockExecutor{}
	allow := New(allowExec, nil)
	allow.SetOnlineFunc(func() bool { return false })
	allow.UpdateSchedules([]*models.Schedule{newSchedule("allow", models.OfflinePolicyAllowOffline, created)})
	allow.checkDue(context.Background(), due)
	allowExec.mu.Lock()
	n := len(allowExec.triggers)
	allowExec.mu.Unlock()
	if n != 1 {
		t.Fatalf("unpaused allow_offline schedule must fire offline, got %d", n)
	}

	// 离线 + hub_online_required → 不触发
	reqExec := &mockExecutor{}
	req := New(reqExec, nil)
	req.SetOnlineFunc(func() bool { return false })
	req.UpdateSchedules([]*models.Schedule{newSchedule("required", models.OfflinePolicyHubOnlineRequired, created)})
	req.checkDue(context.Background(), due)
	reqExec.mu.Lock()
	n = len(reqExec.triggers)
	reqExec.mu.Unlock()
	if n != 0 {
		t.Fatalf("hub_online_required must not fire offline, got %d", n)
	}
}

// TestIntervalRunOnce 验证：interval+RUN_ONCE 错过多个周期时补跑最近一次
func TestIntervalRunOnce(t *testing.T) {
	exec := &mockExecutor{}
	s := New(exec, nil)
	created := time.Now().Add(-3 * time.Minute)
	sch := &models.Schedule{
		ID:             "sch2",
		TaskID:         "task2",
		Type:           models.ScheduleTypeInterval,
		IntervalSec:    30,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		ExecutionOwner: models.ExecutionOwnerAgent,
		Timezone:       "UTC",
		Enabled:        true,
		CreatedAt:      created,
	}
	s.UpdateSchedules([]*models.Schedule{sch})
	s.checkDue(context.Background(), time.Now())
	exec.mu.Lock()
	n := len(exec.triggers)
	exec.mu.Unlock()
	if n != 1 {
		t.Fatalf("RUN_ONCE should trigger once, got %d", n)
	}
}
