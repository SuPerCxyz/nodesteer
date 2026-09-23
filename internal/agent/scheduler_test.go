package agent

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent/scheduler"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/google/uuid"
)

type mockExecutor struct {
	mu        sync.Mutex
	triggered []struct {
		sch *models.Schedule
		at  time.Time
	}
}

func (m *mockExecutor) TriggerSchedule(ctx context.Context, sch *models.Schedule, at time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.triggered = append(m.triggered, struct {
		sch *models.Schedule
		at  time.Time
	}{sch, at})
	return nil
}

func (m *mockExecutor) snapshot() []struct {
	sch *models.Schedule
	at  time.Time
} {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]struct {
		sch *models.Schedule
		at  time.Time
	}, len(m.triggered))
	copy(out, m.triggered)
	return out
}

func TestSchedulerAgentOwnerOnly(t *testing.T) {
	exec := &mockExecutor{}
	s := scheduler.New(exec, nil)

	now := time.Date(2026, 1, 1, 10, 0, 0, 0, time.UTC)
	// Agent 拥有
	agentSch := &models.Schedule{
		ID: "s-agent", TaskID: "t1", Type: "interval", IntervalSec: 60,
		Timezone: "UTC", ExecutionOwner: "agent", Enabled: true,
		MisfirePolicy: "run_once", CreatedAt: now.Add(-65 * time.Second),
	}
	// Hub 拥有（不应由本地触发）
	hubSch := &models.Schedule{
		ID: "s-hub", TaskID: "t1", Type: "interval", IntervalSec: 60,
		Timezone: "UTC", ExecutionOwner: "hub", Enabled: true,
		MisfirePolicy: "run_once", CreatedAt: now.Add(-65 * time.Second),
	}
	s.UpdateSchedules([]*models.Schedule{agentSch, hubSch})

	// 内部 checkDue 通过 Start 循环；这里通过重新触发一次（直接调用内部不可达，改为短间隔 Start）
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx, 500*time.Millisecond)
	time.Sleep(1 * time.Second)

	if len(exec.snapshot()) == 0 {
		t.Fatalf("expected agent-owned schedule to trigger")
	}
	for _, tr := range exec.snapshot() {
		if tr.sch.ID == "s-hub" {
			t.Fatalf("hub-owned schedule must not be triggered by agent local scheduler")
		}
	}
}

func TestSchedulerIntervalMissedRun(t *testing.T) {
	exec := &mockExecutor{}
	s := scheduler.New(exec, nil)
	now := time.Date(2026, 1, 1, 10, 0, 0, 0, time.UTC)
	sch := &models.Schedule{
		ID: "s1", TaskID: "t1", Type: "interval", IntervalSec: 60,
		Timezone: "UTC", ExecutionOwner: "agent", Enabled: true,
		MisfirePolicy: "run_once", CreatedAt: now.Add(-125 * time.Second),
	}
	s.UpdateSchedules([]*models.Schedule{sch})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	s.Start(ctx, 300*time.Millisecond)
	time.Sleep(1 * time.Second)
	if len(exec.snapshot()) == 0 {
		t.Fatalf("expected missed-run trigger for run_once policy")
	}
}

var _ = uuid.NewString
