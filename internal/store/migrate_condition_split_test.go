package store

import (
	"database/sql"
	"encoding/json"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestConditionSplitMigration 覆盖旧 Task.Condition 的本地/远程拆分：
// - 混合条件按 local/remote 拆分；
// - 远程条件复制到全部 Schedule；
// - 纯远程条件从 Task 移除；
// - 无调度的远程条件按约定丢弃并保留本地；
// - 迁移幂等，且 execution.schedule_revision 列存在。
func TestConditionSplitMigration(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	for _, schema := range []string{schemaV1, schemaV2, schemaV3, schemaV4, schemaV5, schemaV6, schemaV7, schemaV8, schemaV9, schemaV10} {
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
	for v := 1; v <= 10; v++ {
		if _, err := db.Exec(`INSERT INTO schema_migrations (version, name) VALUES (?, 'legacy')`, v); err != nil {
			t.Fatalf("mark migration %d applied: %v", v, err)
		}
	}
	// 上一个数据迁移已执行，避免它改写本用例的 definition。
	if _, err := db.Exec(`INSERT INTO settings (key, value) VALUES (?, '1')`, taskScheduleMigrationKey); err != nil {
		t.Fatalf("mark task/schedule migration: %v", err)
	}

	local := models.Condition{Type: "local", Local: &models.LocalCondition{Metric: "cpu_usage", Operator: "<", Value: "80"}}
	remote := models.Condition{Type: "remote", Remote: &models.RemoteCondition{
		NodeID: "node-b", Property: "online", Operator: "==", Value: "online"}}
	mixed := models.Condition{Type: "and", And: []models.Condition{local, remote}}

	insertTask := func(id, name string, condition *models.Condition) {
		def := map[string]any{
			"id": id, "name": name, "type": "command", "command": "true",
			"enabled": true, "revision": 1,
		}
		if condition != nil {
			def["condition"] = condition
		}
		raw, _ := json.Marshal(def)
		if _, err := db.Exec(`INSERT INTO tasks (id, name, definition_json, revision, updated_at, created_at)
			VALUES (?, ?, ?, 1, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')`, id, name, string(raw)); err != nil {
			t.Fatalf("insert task %s: %v", id, err)
		}
	}
	insertSchedule := func(id, taskID string, condition *models.Condition) {
		def := map[string]any{
			"id": id, "task_id": taskID, "name": id, "revision": 1, "type": "manual",
			"target":          map[string]any{"type": "label", "label_key": "env"},
			"timezone":        "UTC",
			"execution_owner": "hub",
			"offline_policy":  "allow_offline",
			"misfire_policy":  "run_once",
			"enabled":         true,
		}
		if condition != nil {
			def["condition"] = condition
		}
		raw, _ := json.Marshal(def)
		if _, err := db.Exec(`INSERT INTO schedules (id, task_id, revision, definition_json, enabled, updated_at, created_at)
			VALUES (?, ?, 1, ?, 1, '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')`, id, taskID, string(raw)); err != nil {
			t.Fatalf("insert schedule %s: %v", id, err)
		}
	}

	insertTask("task-a", "mixed", &mixed)
	insertSchedule("sch-a1", "task-a", nil)
	insertSchedule("sch-a2", "task-a", nil)
	insertTask("task-b", "remote-only", &remote)
	insertSchedule("sch-b", "task-b", nil)
	insertTask("task-c", "local-only", &local)
	insertSchedule("sch-c", "task-c", nil)
	insertTask("task-d", "remote-no-schedule", &remote)

	if err := Migrate(db); err != nil {
		t.Fatalf("migrate: %v", err)
	}

	readTaskCondition := func(id string) *models.Condition {
		var def string
		if err := db.QueryRow(`SELECT definition_json FROM tasks WHERE id=?`, id).Scan(&def); err != nil {
			t.Fatalf("read task %s: %v", id, err)
		}
		var parsed struct {
			Condition *models.Condition `json:"condition"`
		}
		if err := json.Unmarshal([]byte(def), &parsed); err != nil {
			t.Fatalf("decode task %s: %v", id, err)
		}
		return parsed.Condition
	}
	readScheduleCondition := func(id string) *models.Condition {
		var def string
		if err := db.QueryRow(`SELECT definition_json FROM schedules WHERE id=?`, id).Scan(&def); err != nil {
			t.Fatalf("read schedule %s: %v", id, err)
		}
		var parsed struct {
			Condition *models.Condition `json:"condition"`
		}
		if err := json.Unmarshal([]byte(def), &parsed); err != nil {
			t.Fatalf("decode schedule %s: %v", id, err)
		}
		return parsed.Condition
	}

	if c := readTaskCondition("task-a"); c == nil || c.Type != "local" {
		t.Fatalf("task-a local condition = %+v", c)
	}
	for _, id := range []string{"sch-a1", "sch-a2"} {
		if c := readScheduleCondition(id); c == nil || c.Type != "remote" {
			t.Fatalf("%s remote condition = %+v", id, c)
		}
	}
	if c := readTaskCondition("task-b"); c != nil {
		t.Fatalf("task-b should have no task condition: %+v", c)
	}
	if c := readScheduleCondition("sch-b"); c == nil || c.Type != "remote" {
		t.Fatalf("sch-b remote condition = %+v", c)
	}
	if c := readTaskCondition("task-c"); c == nil || c.Type != "local" {
		t.Fatalf("task-c local condition = %+v", c)
	}
	if c := readScheduleCondition("sch-c"); c != nil {
		t.Fatalf("sch-c should have no remote condition: %+v", c)
	}
	if c := readTaskCondition("task-d"); c != nil {
		t.Fatalf("task-d remote-only condition should be dropped: %+v", c)
	}

	// execution.schedule_revision 列已由 v11 建立。
	rows, err := db.Query(`PRAGMA table_info(executions)`)
	if err != nil {
		t.Fatalf("table info: %v", err)
	}
	defer rows.Close()
	found := false
	for rows.Next() {
		var cid int
		var name, typ string
		var notnull, pk int
		var dflt any
		if err := rows.Scan(&cid, &name, &typ, &notnull, &dflt, &pk); err != nil {
			t.Fatalf("scan table info: %v", err)
		}
		if name == "schedule_revision" {
			found = true
		}
	}
	if !found {
		t.Fatal("executions.schedule_revision column missing")
	}

	// 幂等：再次迁移不重复合并条件。
	if err := Migrate(db); err != nil {
		t.Fatalf("second migrate: %v", err)
	}
	if c := readScheduleCondition("sch-a1"); c == nil || c.Type != "remote" {
		t.Fatalf("idempotent sch-a1 condition = %+v", c)
	}
}
