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
