package store

// 同类缺陷审计（F5 同族）：Update*/Delete*/Set* 写方法对「目标行不存在」必须
// 返回 sql.ErrNoRows，不得静默成功（此前 PUT/DELETE 在并发删除窗口可对已不存在的
// 资源返回 200，或为不存在的定义写孤儿历史版本）。
// 本文件锁定 store 层的零行命中语义；API 层 404 归一化见 internal/hub/api。

import (
	"database/sql"
	"errors"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/google/uuid"
)

// TestWriteMethodsMissingRowReturnErrNoRows 逐条核对全部单行 Update/Delete/Set 方法：
// 不存在的 id → sql.ErrNoRows。
func TestWriteMethodsMissingRowReturnErrNoRows(t *testing.T) {
	s := newTestStore(t)
	ctx := t.Context()
	missing := uuid.NewString()

	cases := []struct {
		name string
		run  func() error
	}{
		{"UpdateUserRole", func() error { return s.UpdateUserRole(ctx, missing, models.RoleViewer) }},
		{"UpdateUserPassword", func() error { return s.UpdateUserPassword(ctx, missing, "hash") }},
		{"UpdateScript", func() error {
			return s.UpdateScript(ctx, &models.Script{ID: missing, Name: "ghost", Interpreter: "shell", Content: "true", Revision: 2}, 1)
		}},
		{"DeleteScript", func() error { return s.DeleteScript(ctx, missing) }},
		{"UpdateTask", func() error {
			return s.UpdateTask(ctx, &models.Task{ID: missing, Name: "ghost", Revision: 2}, 1)
		}},
		{"DeleteTask", func() error { return s.DeleteTask(ctx, missing) }},
		{"UpdateSchedule", func() error {
			return s.UpdateSchedule(ctx, &models.Schedule{ID: missing, TaskID: "t", Revision: 2}, 1)
		}},
		{"DeleteSchedule", func() error { return s.DeleteSchedule(ctx, missing) }},
		{"UpdateApplication", func() error {
			return s.UpdateApplication(ctx, &models.Application{ID: missing, Name: "ghost", Revision: 2}, 1)
		}},
		{"DeleteApplication", func() error { return s.DeleteApplication(ctx, missing) }},
		{"DeleteArtifact", func() error { return s.DeleteArtifact(ctx, missing) }},
		{"UpdateNodeStatus", func() error { return s.UpdateNodeStatus(ctx, missing, models.NodeStatusOffline) }},
		{"UpdateNodeSyncState", func() error { return s.UpdateNodeSyncState(ctx, missing, 3, "synced") }},
		{"UpdateNodeHeartbeat", func() error { return s.UpdateNodeHeartbeat(ctx, missing, time.Now()) }},
		{"SetNodeLabels", func() error { return s.SetNodeLabels(ctx, missing, map[string]string{"env": "prod"}) }},
		{"DeleteNode", func() error { return s.DeleteNode(ctx, missing) }},
		{"UpdateGroup", func() error {
			return s.UpdateGroup(ctx, &models.Group{ID: missing, Name: "ghost", Type: "static"})
		}},
		{"DeleteGroup", func() error { return s.DeleteGroup(ctx, missing) }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.run(); !errors.Is(err, sql.ErrNoRows) {
				t.Fatalf("%s missing row err = %v, want sql.ErrNoRows", tc.name, err)
			}
		})
	}
}

// TestWriteMethodsMissingRowWriteNoOrphanRows 零行命中的定义更新不得产生副作用：
// 不写孤儿历史版本，不改动任何既有数据。
func TestWriteMethodsMissingRowWriteNoOrphanRows(t *testing.T) {
	s := newTestStore(t)
	ctx := t.Context()
	missing := uuid.NewString()

	updates := []struct {
		name  string
		table string
		run   func() error
	}{
		{"UpdateScript", "script_revisions", func() error {
			return s.UpdateScript(ctx, &models.Script{ID: missing, Name: "ghost", Interpreter: "shell", Content: "true", Revision: 2}, 1)
		}},
		{"UpdateTask", "task_revisions", func() error {
			return s.UpdateTask(ctx, &models.Task{ID: missing, Name: "ghost", Revision: 2}, 1)
		}},
		{"UpdateApplication", "application_revisions", func() error {
			return s.UpdateApplication(ctx, &models.Application{ID: missing, Name: "ghost", Revision: 2}, 1)
		}},
	}
	for _, tc := range updates {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.run(); !errors.Is(err, sql.ErrNoRows) {
				t.Fatalf("%s err = %v, want sql.ErrNoRows", tc.name, err)
			}
			var count int
			if err := s.DB().QueryRowContext(ctx,
				"SELECT COUNT(*) FROM "+tc.table+" WHERE id = ?", missing).Scan(&count); err != nil {
				t.Fatalf("count %s: %v", tc.table, err)
			}
			if count != 0 {
				t.Fatalf("%s rows written for missing id = %d, want 0", tc.table, count)
			}
		})
	}
}

// TestWriteMethodsExistingRowStillSucceed 正向对照：存在资源时同一批方法仍正常成功，
// 零行校验不得误伤正常路径。
func TestWriteMethodsExistingRowStillSucceed(t *testing.T) {
	s := newTestStore(t)
	ctx := t.Context()

	// node
	node := &models.Node{ID: uuid.NewString(), AgentID: "agent-" + uuid.NewString(), Hostname: "rows-node",
		Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative}
	if err := s.UpsertNode(ctx, node); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	if err := s.UpdateNodeStatus(ctx, node.ID, models.NodeStatusOffline); err != nil {
		t.Fatalf("UpdateNodeStatus existing: %v", err)
	}
	if err := s.UpdateNodeSyncState(ctx, node.ID, 5, "synced"); err != nil {
		t.Fatalf("UpdateNodeSyncState existing: %v", err)
	}
	if err := s.UpdateNodeHeartbeat(ctx, node.ID, time.Now()); err != nil {
		t.Fatalf("UpdateNodeHeartbeat existing: %v", err)
	}
	if err := s.SetNodeLabels(ctx, node.ID, map[string]string{"env": "prod"}); err != nil {
		t.Fatalf("SetNodeLabels existing: %v", err)
	}

	// script：更新写 revision，删除成功
	script := &models.Script{Name: "rows-script", Interpreter: "shell", Content: "true", Revision: 1, SHA256: "x"}
	if err := s.CreateScript(ctx, script); err != nil {
		t.Fatalf("CreateScript: %v", err)
	}
	if err := s.UpdateScript(ctx, &models.Script{ID: script.ID, Name: "rows-script-2", Interpreter: "shell", Content: "echo ok", Revision: 2}, 1); err != nil {
		t.Fatalf("UpdateScript existing: %v", err)
	}
	if err := s.DeleteScript(ctx, script.ID); err != nil {
		t.Fatalf("DeleteScript existing: %v", err)
	}

	// task + schedule：先建 task，再建 schedule，更新后删除
	task := &models.Task{Name: "rows-task", Type: models.TaskTypeCommand, Command: "true"}
	if err := s.CreateTask(ctx, task); err != nil {
		t.Fatalf("CreateTask: %v", err)
	}
	if err := s.UpdateTask(ctx, &models.Task{ID: task.ID, Name: "rows-task-2", Type: models.TaskTypeCommand, Command: "true", Revision: 2}, 1); err != nil {
		t.Fatalf("UpdateTask existing: %v", err)
	}
	schedule := &models.Schedule{TaskID: task.ID, Type: models.ScheduleTypeInterval, IntervalSec: 60, Revision: 1}
	if err := s.CreateSchedule(ctx, schedule); err != nil {
		t.Fatalf("CreateSchedule: %v", err)
	}
	if err := s.UpdateSchedule(ctx, &models.Schedule{ID: schedule.ID, TaskID: task.ID, Type: models.ScheduleTypeInterval, IntervalSec: 120, Revision: 2}, 1); err != nil {
		t.Fatalf("UpdateSchedule existing: %v", err)
	}
	if err := s.DeleteSchedule(ctx, schedule.ID); err != nil {
		t.Fatalf("DeleteSchedule existing: %v", err)
	}
	if err := s.DeleteTask(ctx, task.ID); err != nil {
		t.Fatalf("DeleteTask existing: %v", err)
	}

	// application：更新后删除
	app := &models.Application{Name: "rows-app", UnitName: "rows-app.service"}
	if err := s.CreateApplication(ctx, app); err != nil {
		t.Fatalf("CreateApplication: %v", err)
	}
	if err := s.UpdateApplication(ctx, &models.Application{ID: app.ID, Name: "rows-app-2", UnitName: "rows-app.service", Revision: 2}, 1); err != nil {
		t.Fatalf("UpdateApplication existing: %v", err)
	}
	if err := s.DeleteApplication(ctx, app.ID); err != nil {
		t.Fatalf("DeleteApplication existing: %v", err)
	}

	// artifact：删除成功
	artifact := &models.Artifact{ID: uuid.NewString(), Name: "rows-art", Version: "1", Architecture: "amd64",
		Filename: "rows.bin", Size: 1, SHA256: "x", StoragePath: "/nonexistent"}
	if err := s.CreateArtifact(ctx, artifact); err != nil {
		t.Fatalf("CreateArtifact: %v", err)
	}
	if err := s.DeleteArtifact(ctx, artifact.ID); err != nil {
		t.Fatalf("DeleteArtifact existing: %v", err)
	}

	// group：更新（含成员替换）后删除
	group := &models.Group{Name: "rows-group", Type: "static"}
	if err := s.CreateGroup(ctx, group); err != nil {
		t.Fatalf("CreateGroup: %v", err)
	}
	if err := s.UpdateGroup(ctx, &models.Group{ID: group.ID, Name: "rows-group-2", Type: "static"}); err != nil {
		t.Fatalf("UpdateGroup existing: %v", err)
	}
	if err := s.DeleteGroup(ctx, group.ID); err != nil {
		t.Fatalf("DeleteGroup existing: %v", err)
	}

	// node：删除成功
	if err := s.DeleteNode(ctx, node.ID); err != nil {
		t.Fatalf("DeleteNode existing: %v", err)
	}
}
