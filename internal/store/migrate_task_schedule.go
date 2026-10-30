package store

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/google/uuid"
)

// taskScheduleMigrationKey 旧 Task → Schedule 数据迁移的幂等标记。
const taskScheduleMigrationKey = "data_migration_task_schedule_model_v1"

// legacyTaskDefinition 仅用于读取迁移前的 Task JSON。
// 不能依赖 models.Task：其中的目标与策略字段已经删除。
type legacyTaskDefinition struct {
	Target        models.Target `json:"target"`
	Timeout       int           `json:"timeout"`
	Retry         int           `json:"retry"`
	OfflinePolicy string        `json:"offline_policy"`
	RunUser       string        `json:"run_user"`
}

// legacyTaskRow 迁移遍历用的 Task 行。
type legacyTaskRow struct {
	ID         string
	Name       string
	Definition string
	Revision   int64
}

// legacyScheduleRow 迁移遍历用的 Schedule 行。
type legacyScheduleRow struct {
	ID         string
	Definition string
	Revision   int64
}

// migrateTaskScheduleModel 把旧 Task 上的目标与执行策略迁移到 Schedule，
// 并为“有目标但无 Schedule”的旧 Task 创建禁用的 manual 迁移调度。
//
// 使用 settings key 做幂等标记，只执行一次；失败返回错误，由 OpenSQLite
// 向上抛出以阻止 Hub 继续启动，不会静默跳过。
func migrateTaskScheduleModel(db *sql.DB) error {
	var marker string
	err := db.QueryRow(`SELECT value FROM settings WHERE key = ?`, taskScheduleMigrationKey).Scan(&marker)
	if err == nil {
		return nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return err
	}

	tx, err := db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if err := migrateTaskScheduleModelTx(tx); err != nil {
		return err
	}
	if _, err := tx.Exec(`INSERT INTO settings (key, value) VALUES (?, ?)
		ON CONFLICT(key) DO UPDATE SET value=excluded.value`, taskScheduleMigrationKey, "1"); err != nil {
		return err
	}
	return tx.Commit()
}

func migrateTaskScheduleModelTx(tx *sql.Tx) error {
	tasks, err := listLegacyTasks(tx)
	if err != nil {
		return err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	for _, task := range tasks {
		schedules, err := listLegacySchedules(tx, task.ID)
		if err != nil {
			return err
		}

		var legacy legacyTaskDefinition
		if err := json.Unmarshal([]byte(task.Definition), &legacy); err != nil {
			return fmt.Errorf("task %s: parse definition: %w", task.ID, err)
		}
		var def map[string]any
		if err := json.Unmarshal([]byte(task.Definition), &def); err != nil {
			return fmt.Errorf("task %s: parse definition map: %w", task.ID, err)
		}

		for _, sch := range schedules {
			updated, changed, err := migrateScheduleDefinition(sch.Definition, legacy, task.Name)
			if err != nil {
				return fmt.Errorf("schedule %s: %w", sch.ID, err)
			}
			if !changed {
				continue
			}
			newRevision := sch.Revision + 1
			updated["revision"] = newRevision
			updated["updated_at"] = now
			raw, err := json.Marshal(updated)
			if err != nil {
				return fmt.Errorf("schedule %s: marshal definition: %w", sch.ID, err)
			}
			if _, err := tx.Exec(`UPDATE schedules SET revision=?, definition_json=?, updated_at=? WHERE id=?`,
				newRevision, string(raw), now, sch.ID); err != nil {
				return err
			}
			if err := recordMigrationChange(tx, models.ObjectSchedule, sch.ID, newRevision, "update"); err != nil {
				return err
			}
		}

		// 有目标但没有任何 Schedule 的旧 Task：创建禁用的 manual 迁移调度。
		if len(schedules) == 0 && legacy.Target.Type != "" {
			if err := createMigratedManualSchedule(tx, task, legacy, now); err != nil {
				return err
			}
		}

		changed := stripLegacyTaskFields(def)
		if !changed {
			continue
		}
		newRevision := task.Revision + 1
		def["revision"] = newRevision
		def["updated_at"] = now
		raw, err := json.Marshal(def)
		if err != nil {
			return fmt.Errorf("task %s: marshal definition: %w", task.ID, err)
		}
		if _, err := tx.Exec(`UPDATE tasks SET definition_json=?, revision=?, updated_at=? WHERE id=?`,
			string(raw), newRevision, now, task.ID); err != nil {
			return err
		}
		if _, err := tx.Exec(`INSERT INTO task_revisions (id, revision, definition_json, changed_at)
			VALUES (?, ?, ?, ?)`, task.ID, newRevision, string(raw), now); err != nil {
			return err
		}
		if err := recordMigrationChange(tx, models.ObjectTask, task.ID, newRevision, "update"); err != nil {
			return err
		}
	}
	return nil
}

func listLegacyTasks(tx *sql.Tx) ([]legacyTaskRow, error) {
	rows, err := tx.Query(`SELECT id, name, definition_json, revision FROM tasks ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []legacyTaskRow
	for rows.Next() {
		var t legacyTaskRow
		if err := rows.Scan(&t.ID, &t.Name, &t.Definition, &t.Revision); err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	return out, rows.Err()
}

func listLegacySchedules(tx *sql.Tx, taskID string) ([]legacyScheduleRow, error) {
	rows, err := tx.Query(`SELECT id, definition_json, revision FROM schedules WHERE task_id=? ORDER BY created_at`, taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []legacyScheduleRow
	for rows.Next() {
		var s legacyScheduleRow
		if err := rows.Scan(&s.ID, &s.Definition, &s.Revision); err != nil {
			return nil, err
		}
		out = append(out, s)
	}
	return out, rows.Err()
}

// migrateScheduleDefinition 把旧 Task 的目标/策略补进单条 Schedule 定义。
// 已有 target 或已有新字段的 Schedule 不覆盖（支持中断后重跑）。
func migrateScheduleDefinition(raw string, legacy legacyTaskDefinition, taskName string) (map[string]any, bool, error) {
	var def map[string]any
	if err := json.Unmarshal([]byte(raw), &def); err != nil {
		return nil, false, fmt.Errorf("parse definition: %w", err)
	}
	var parsed models.Schedule
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		return nil, false, fmt.Errorf("parse schedule: %w", err)
	}
	changed := false

	if targetEmpty(def["target"]) && legacy.Target.Type != "" {
		def["target"] = legacy.Target
		changed = true
	}

	if !hasPositiveNumber(def["timeout"]) {
		timeout := legacy.Timeout
		if timeout <= 0 {
			timeout = defaultScheduleTimeout
		}
		def["timeout"] = timeout
		changed = true
	}

	if _, ok := def["retry"]; !ok {
		def["retry"] = legacy.Retry
		changed = true
	}

	if runUser, _ := def["run_user"].(string); strings.TrimSpace(runUser) == "" {
		if legacy.RunUser != "" || def["run_user"] == nil {
			def["run_user"] = legacy.RunUser
			changed = true
		}
	}

	if policy, _ := def["offline_policy"].(string); strings.TrimSpace(policy) == "" {
		policy = legacy.OfflinePolicy
		if policy == "" {
			policy = models.OfflinePolicyAllowOffline
		}
		def["offline_policy"] = policy
		changed = true
	}

	if name, _ := def["name"].(string); strings.TrimSpace(name) == "" {
		def["name"] = models.ScheduleDisplayName(taskName, &parsed)
		changed = true
	}
	return def, changed, nil
}

// defaultScheduleTimeout 调度默认执行超时秒数（与 Hub ExecutionStrategy 保持一致）。
const defaultScheduleTimeout = 300

func targetEmpty(v any) bool {
	if v == nil {
		return true
	}
	raw, err := json.Marshal(v)
	if err != nil {
		return true
	}
	var t models.Target
	if err := json.Unmarshal(raw, &t); err != nil {
		return true
	}
	return t.Type == ""
}

func hasPositiveNumber(v any) bool {
	n, ok := v.(float64)
	return ok && n > 0
}

func stripLegacyTaskFields(def map[string]any) bool {
	changed := false
	for _, key := range []string{"target", "timeout", "retry", "offline_policy", "run_user", "schedule"} {
		if _, ok := def[key]; ok {
			delete(def, key)
			changed = true
		}
	}
	return changed
}

func createMigratedManualSchedule(tx *sql.Tx, task legacyTaskRow, legacy legacyTaskDefinition, now string) error {
	timeout := legacy.Timeout
	if timeout <= 0 {
		timeout = defaultScheduleTimeout
	}
	policy := legacy.OfflinePolicy
	if policy == "" {
		policy = models.OfflinePolicyHubOnlineRequired
	}
	s := &models.Schedule{
		ID:             uuid.NewString(),
		TaskID:         task.ID,
		Name:           "迁移：" + task.Name,
		Revision:       1,
		Target:         legacy.Target,
		Type:           models.ScheduleTypeManual,
		Timeout:        timeout,
		Retry:          legacy.Retry,
		RunUser:        legacy.RunUser,
		Timezone:       "UTC",
		ExecutionOwner: models.ExecutionOwnerHub,
		OfflinePolicy:  policy,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		Enabled:        false,
	}
	raw, err := json.Marshal(s)
	if err != nil {
		return err
	}
	if _, err := tx.Exec(`INSERT INTO schedules (id, task_id, revision, definition_json, enabled, updated_at, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		s.ID, s.TaskID, s.Revision, string(raw), 0, now, now); err != nil {
		return err
	}
	return recordMigrationChange(tx, models.ObjectSchedule, s.ID, s.Revision, "create")
}

func recordMigrationChange(tx *sql.Tx, objectType, objectID string, objectRevision int64, operation string) error {
	var rev int64
	if err := tx.QueryRow(`UPDATE revision_sequence SET value = value + 1 RETURNING value`).Scan(&rev); err != nil {
		return err
	}
	_, err := tx.Exec(`INSERT INTO revision_changes
		(global_revision, object_type, object_id, object_revision, operation, created_at)
		VALUES (?, ?, ?, ?, ?, ?)`,
		rev, objectType, objectID, objectRevision, operation, time.Now().UTC().Format(time.RFC3339Nano))
	return err
}
