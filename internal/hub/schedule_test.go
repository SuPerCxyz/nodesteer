package hub

import (
	"context"
	"strings"
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

// TestOneTimeScheduleRejectsPastRunAt 一次性调度运行时间必须晚于当前时间：
// 过去时间创建必须 400 语义拒绝；修复前创建的过去时间记录（run_at 未修改）
// 仍可编辑/禁用，不会因修复被锁死（缺陷 OBS-20261014-PAST-ONETIME-SAVE-ENABLED）。
func TestOneTimeScheduleRejectsPastRunAt(t *testing.T) {
	ctx := context.Background()
	sm := newTestScheduleManager(t)

	past := &models.Schedule{
		TaskID: "task-1", Type: models.ScheduleTypeOneTime,
		RunAt:          time.Now().Add(-24 * time.Hour),
		Timezone:       "UTC",
		ExecutionOwner: models.ExecutionOwnerHub,
		OfflinePolicy:  models.OfflinePolicyAllowOffline,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		Enabled:        true,
	}
	err := sm.Create(ctx, past)
	if err == nil || !strings.Contains(err.Error(), "run_at must be in the future") {
		t.Fatalf("past one_time create must be rejected with clear message, got %v", err)
	}

	// 未来时间创建成功（新对象，不复用失败请求已分配的 ID）
	future := *past
	future.ID = ""
	future.RunAt = time.Now().Add(time.Hour)
	if err := sm.Create(ctx, &future); err != nil {
		t.Fatalf("future one_time create: %v", err)
	}

	// 模拟修复前的历史遗留记录：直接落库过去时间，随后编辑（禁用）必须放行
	if err := sm.store.CreateSchedule(ctx, past); err != nil {
		t.Fatalf("seed legacy past schedule: %v", err)
	}
	if err := sm.Update(ctx, &models.Schedule{ID: past.ID, Enabled: false}); err != nil {
		t.Fatalf("legacy past run_at must stay editable: %v", err)
	}
	// 改成新的过去时间仍拒绝
	changed := &models.Schedule{ID: past.ID, RunAt: time.Now().Add(-time.Hour)}
	if err := sm.Update(ctx, changed); err == nil || !strings.Contains(err.Error(), "run_at must be in the future") {
		t.Fatalf("changed past run_at must be rejected, got %v", err)
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
