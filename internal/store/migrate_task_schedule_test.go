package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestTaskScheduleModelMigration 覆盖旧 Task.Target/策略迁移到 Schedule：
// - 已有 Schedule 继承旧 Task 的目标与策略；
// - 有目标但无 Schedule 的 Task 生成禁用 manual 迁移调度；
// - Execution created_at 回填、schedule_id 保持空；
// - 迁移幂等，重复启动不重复创建。
func TestTaskScheduleModelMigration(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	// 构造 v9 旧库：schema 与 schema_migrations 都停在 v9，数据迁移尚未执行。
	for _, schema := range []string{schemaV1, schemaV2, schemaV3, schemaV4, schemaV5, schemaV6, schemaV7, schemaV8, schemaV9} {
		if _, err := db.Exec(schema); err != nil {
			t.Fatalf("create legacy schema: %v", err)
		}
	}
	if _, err := db.Exec(`CREATE TABLE schema_migrations (
		version INTEGER PRIMARY KEY,
		name TEXT NOT NULL,
		applied_at TEXT NOT NULL DEFAULT (datetime('now'))
	)`); err != nil {
		t.Fatalf("create schema_migrations: %v", err)
	}
	for v := 1; v <= 9; v++ {
		if _, err := db.Exec(`INSERT INTO schema_migrations (version, name) VALUES (?, 'legacy')`, v); err != nil {
			t.Fatalf("mark migration %d applied: %v", v, err)
		}
	}

	oldNodeTarget := `{"type":"node","node_ids":["node-a"]}`
	taskWithSchedule := `{"id":"task-1","name":"legacy-task","type":"command","command":"true",` +
		`"target":` + oldNodeTarget + `,"timeout":45,"retry":2,"offline_policy":"allow_offline","run_user":"ops",` +
		`"enabled":true,"revision":1,"created_at":"2024-01-01T00:00:00Z","updated_at":"2024-01-01T00:00:00Z"}`
	taskWithoutSchedule := `{"id":"task-2","name":"manual-only","type":"command","command":"true",` +
		`"target":` + oldNodeTarget + `,"timeout":30,"retry":0,"offline_policy":"hub_online_required",` +
		`"enabled":true,"revision":1,"created_at":"2024-01-01T00:00:00Z","updated_at":"2024-01-01T00:00:00Z"}`
	for _, row := range []struct{ id, name, def string }{
		{"task-1", "legacy-task", taskWithSchedule},
		{"task-2", "manual-only", taskWithoutSchedule},
	} {
		if _, err := db.Exec(`INSERT INTO tasks (id, name, definition_json, revision, updated_at, created_at)
			VALUES (?, ?, ?, 1, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')`, row.id, row.name, row.def); err != nil {
			t.Fatalf("insert legacy task: %v", err)
		}
	}
	oldSchedule := `{"id":"sch-1","task_id":"task-1","revision":1,"type":"interval","interval_sec":60,` +
		`"timezone":"UTC","execution_owner":"hub","offline_policy":"hub_online_required",` +
		`"misfire_policy":"run_once","enabled":true,"created_at":"2024-01-01T00:00:00Z","updated_at":"2024-01-01T00:00:00Z"}`
	if _, err := db.Exec(`INSERT INTO schedules (id, task_id, revision, definition_json, enabled, updated_at, created_at)
		VALUES ('sch-1', 'task-1', 1, ?, 1, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')`, oldSchedule); err != nil {
		t.Fatalf("insert legacy schedule: %v", err)
	}
	if _, err := db.Exec(`INSERT INTO executions (
		id, task_id, task_revision, script_id, script_revision, node_id, trigger_type, scheduled_time,
		start_time, end_time, status, exit_code, stdout, stderr, stdout_truncated, stderr_truncated,
		offline, synced, block_reason, created_at)
		VALUES ('exec-1','task-1',1,'',0,'node-a','schedule','2024-01-01T00:00:00Z',
		'2024-01-01T00:00:00Z','2024-01-01T00:00:05Z','SUCCESS',0,'ok','',0,0,0,1,'','')`); err != nil {
		t.Fatalf("insert legacy execution: %v", err)
	}

	if err := Migrate(db); err != nil {
		t.Fatalf("migrate: %v", err)
	}

	// 已有调度继承目标与策略；已有 offline_policy 不被旧 Task 覆盖。
	var schDef string
	if err := db.QueryRow(`SELECT definition_json FROM schedules WHERE id='sch-1'`).Scan(&schDef); err != nil {
		t.Fatalf("read migrated schedule: %v", err)
	}
	var sch models.Schedule
	if err := json.Unmarshal([]byte(schDef), &sch); err != nil {
		t.Fatalf("decode migrated schedule: %v", err)
	}
	if sch.Target.Type != "node" || len(sch.Target.NodeIDs) != 1 || sch.Target.NodeIDs[0] != "node-a" {
		t.Fatalf("schedule target not migrated: %+v", sch.Target)
	}
	if sch.Timeout != 45 || sch.Retry != 2 || sch.RunUser != "ops" {
		t.Fatalf("schedule strategy not migrated: %+v", sch)
	}
	if sch.OfflinePolicy != models.OfflinePolicyHubOnlineRequired {
		t.Fatalf("existing offline policy must win: %q", sch.OfflinePolicy)
	}
	if strings.TrimSpace(sch.Name) == "" {
		t.Fatal("schedule name should be generated")
	}

	// 有目标但无 Schedule 的 Task 生成禁用 manual 迁移调度。
	var manualDef string
	var manualEnabled int
	if err := db.QueryRow(`SELECT definition_json, enabled FROM schedules WHERE task_id='task-2'`).Scan(&manualDef, &manualEnabled); err != nil {
		t.Fatalf("read manual migration schedule: %v", err)
	}
	var manual models.Schedule
	if err := json.Unmarshal([]byte(manualDef), &manual); err != nil {
		t.Fatalf("decode manual schedule: %v", err)
	}
	if manual.Type != models.ScheduleTypeManual || manualEnabled != 0 {
		t.Fatalf("manual migration schedule = type %q enabled %d", manual.Type, manualEnabled)
	}
	if !strings.HasPrefix(manual.Name, "迁移：") {
		t.Fatalf("manual migration schedule name = %q", manual.Name)
	}
	if manual.Target.Type != "node" || manual.Timeout != 30 || manual.OfflinePolicy != models.OfflinePolicyHubOnlineRequired {
		t.Fatalf("manual migration schedule fields = %+v", manual)
	}

	// Task JSON 不再包含旧字段。
	var taskDef string
	if err := db.QueryRow(`SELECT definition_json FROM tasks WHERE id='task-1'`).Scan(&taskDef); err != nil {
		t.Fatalf("read migrated task: %v", err)
	}
	var taskMap map[string]any
	if err := json.Unmarshal([]byte(taskDef), &taskMap); err != nil {
		t.Fatalf("decode migrated task: %v", err)
	}
	for _, key := range []string{"target", "timeout", "retry", "offline_policy", "run_user", "schedule"} {
		if _, ok := taskMap[key]; ok {
			t.Fatalf("task JSON still contains %q: %s", key, taskDef)
		}
	}

	// Execution created_at 回填，schedule_id 为空。
	var created, scheduleID string
	if err := db.QueryRow(`SELECT created_at, schedule_id FROM executions WHERE id='exec-1'`).Scan(&created, &scheduleID); err != nil {
		t.Fatalf("read migrated execution: %v", err)
	}
	if created == "" || scheduleID != "" {
		t.Fatalf("execution migration = created_at %q schedule_id %q", created, scheduleID)
	}

	// 迁移产生变更日志，且重复执行不重复创建 manual 调度。
	var changes int
	if err := db.QueryRow(`SELECT COUNT(*) FROM revision_changes`).Scan(&changes); err != nil {
		t.Fatalf("count revision changes: %v", err)
	}
	if changes == 0 {
		t.Fatal("migration should record revision changes")
	}
	if err := Migrate(db); err != nil {
		t.Fatalf("second migrate: %v", err)
	}
	var manualCount int
	if err := db.QueryRow(`SELECT COUNT(*) FROM schedules WHERE task_id='task-2'`).Scan(&manualCount); err != nil {
		t.Fatalf("count manual schedules: %v", err)
	}
	if manualCount != 1 {
		t.Fatalf("migration must be idempotent, manual schedules = %d", manualCount)
	}

	// 迁移结果可被 Store 读取。
	st := &SQLiteStore{db: db}
	migratedTask, err := st.GetTask(context.Background(), "task-1")
	if err != nil {
		t.Fatalf("get migrated task: %v", err)
	}
	if migratedTask.Name != "legacy-task" || migratedTask.Revision != 2 {
		t.Fatalf("migrated task = %+v", migratedTask)
	}
}
