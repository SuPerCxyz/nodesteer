package hub

import (
	"context"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// newTestScheduleManager 构造 ScheduleManager（内存 store）
func newTestScheduleManager(t *testing.T) *ScheduleManager {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	revisions := NewRevisionManager(st)
	if err := st.CreateTask(context.Background(), &models.Task{ID: "task-1", Name: "task", Type: models.TaskTypeCommand, Command: "echo", Target: models.Target{Type: "label", LabelKey: "env"}, Enabled: true}); err != nil {
		t.Fatalf("create task: %v", err)
	}
	return NewScheduleManager(st, revisions, NewSyncManager(st, revisions, NewSessionManager(), NewNodeManager(st, revisions)), NewNodeManager(st, revisions), nil)
}

// TestSchedulePartialUpdatePreservesFields 验证 Bug D：部分更新 enabled 时
// 不得清空 task_id/type 等完整定义字段。
func TestSchedulePartialUpdatePreservesFields(t *testing.T) {
	ctx := context.Background()
	sm := newTestScheduleManager(t)

	full := &models.Schedule{
		TaskID:         "task-1",
		Type:           "interval",
		IntervalSec:    60,
		Timezone:       "UTC",
		ExecutionOwner: "agent",
		OfflinePolicy:  models.OfflinePolicyAllowOffline,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		Enabled:        true,
		CreatedAt:      time.Now(),
	}
	if err := sm.Create(ctx, full); err != nil {
		t.Fatalf("create: %v", err)
	}

	// 部分更新：仅禁用，不传其他字段
	partial := &models.Schedule{ID: full.ID, Enabled: false}
	if err := sm.Update(ctx, partial); err != nil {
		t.Fatalf("partial update: %v", err)
	}

	got, err := sm.store.GetSchedule(ctx, full.ID)
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if got.TaskID != "task-1" {
		t.Fatalf("task_id lost after partial update: %q", got.TaskID)
	}
	if got.Type != "interval" {
		t.Fatalf("type lost after partial update: %q", got.Type)
	}
	if got.IntervalSec != 60 {
		t.Fatalf("interval_sec lost after partial update: %d", got.IntervalSec)
	}
	if got.Enabled {
		t.Fatalf("enabled should be false after disable")
	}
	if got.Revision != 2 {
		t.Fatalf("revision should advance to 2, got %d", got.Revision)
	}
}

// TestValidateNormalizesConditionType 验证 Bug E：远程/本地条件 type 为空时
// 应在 Validate 中归一化为 remote/local，否则 Agent 评估直接放行。
func TestValidateNormalizesConditionType(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	revisions := NewRevisionManager(st)
	tm := &TaskManager{store: st, revisions: revisions}
	if err := st.UpsertNode(ctx, &models.Node{ID: "n1", AgentID: "a1", Hostname: "n1", Status: models.NodeStatusOnline}); err != nil {
		t.Fatalf("create node: %v", err)
	}

	cases := []struct {
		name      string
		condition *models.Condition
		wantType  string
	}{
		{"remote empty type", &models.Condition{Remote: &models.RemoteCondition{NodeID: "n1", Property: "online", Operator: "==", Value: "ONLINE"}}, "remote"},
		{"local empty type", &models.Condition{Local: &models.LocalCondition{Metric: "cpu_usage", Operator: ">", Value: "50"}}, "local"},
		{"already typed", &models.Condition{Type: "and", And: []models.Condition{{Type: "local", Local: &models.LocalCondition{Metric: "cpu_usage", Operator: ">", Value: "50"}}}}, "and"},
		{"nil condition", nil, ""},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			task := &models.Task{ID: "t1", Name: "x", Type: models.TaskTypeCommand, Command: "echo", Target: models.Target{Type: "node", NodeIDs: []string{"n1"}}, Condition: tc.condition}
			if err := tm.Validate(ctx, task); err != nil {
				t.Fatalf("validate: %v", err)
			}
			if tc.condition == nil {
				return
			}
			if task.Condition.Type != tc.wantType {
				t.Fatalf("condition type = %q, want %q", task.Condition.Type, tc.wantType)
			}
		})
	}
}
