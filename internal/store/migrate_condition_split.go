package store

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// conditionSplitMigrationKey 条件归属拆分迁移的幂等标记。
const conditionSplitMigrationKey = "data_migration_condition_split_v1"

// migrateConditionSplitModel 把旧 Task.Condition 拆成 Task 本地条件与 Schedule 远程条件。
// 迁移在任务/调度字段迁移之后执行，复用已有的手动迁移调度。
func migrateConditionSplitModel(db *sql.DB) error {
	var marker string
	err := db.QueryRow(`SELECT value FROM settings WHERE key = ?`, conditionSplitMigrationKey).Scan(&marker)
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

	if err := migrateConditionSplitModelTx(tx); err != nil {
		return err
	}
	if _, err := tx.Exec(`INSERT INTO settings (key, value) VALUES (?, ?)
		ON CONFLICT(key) DO UPDATE SET value=excluded.value`, conditionSplitMigrationKey, "1"); err != nil {
		return err
	}
	return tx.Commit()
}

func migrateConditionSplitModelTx(tx *sql.Tx) error {
	tasks, err := listLegacyTasks(tx)
	if err != nil {
		return err
	}
	now := time.Now().UTC().Format(time.RFC3339Nano)
	for _, task := range tasks {
		var legacy struct {
			Condition *models.Condition `json:"condition"`
		}
		if err := json.Unmarshal([]byte(task.Definition), &legacy); err != nil {
			return fmt.Errorf("task %s: parse condition: %w", task.ID, err)
		}
		if legacy.Condition == nil {
			continue
		}
		local, remote := splitCondition(legacy.Condition)
		if remote == nil {
			// 已经是纯本地条件，无需迁移。
			continue
		}

		var def map[string]any
		if err := json.Unmarshal([]byte(task.Definition), &def); err != nil {
			return fmt.Errorf("task %s: parse definition: %w", task.ID, err)
		}
		if local == nil {
			delete(def, "condition")
		} else {
			def["condition"] = local
		}

		schedules, err := listLegacySchedules(tx, task.ID)
		if err != nil {
			return err
		}
		if len(schedules) == 0 {
			// 没有可绑定的调度：远程条件无法决定目标，保留本地并记录告警。
			slog.Warn("condition split: remote condition dropped for task without schedule",
				"task", task.ID, "name", task.Name)
		}
		for _, sch := range schedules {
			updated, changed, err := attachRemoteCondition(sch.Definition, remote)
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

// splitCondition 递归拆分混合条件树，返回纯本地与纯远程两棵树。
// 单个子条件直接返回；多个子条件保持 and 分组，不改变原有评估语义。
func splitCondition(c *models.Condition) (local, remote *models.Condition) {
	if c == nil {
		return nil, nil
	}
	switch c.Type {
	case "local":
		return c, nil
	case "remote":
		return nil, c
	case "and":
		var locals, remotes []models.Condition
		for i := range c.And {
			l, r := splitCondition(&c.And[i])
			if l != nil {
				locals = append(locals, *l)
			}
			if r != nil {
				remotes = append(remotes, *r)
			}
		}
		return groupConditions(locals), groupConditions(remotes)
	default:
		// 未知类型保持原样，交给后续校验拒绝。
		return c, nil
	}
}

func groupConditions(items []models.Condition) *models.Condition {
	switch len(items) {
	case 0:
		return nil
	case 1:
		item := items[0]
		return &item
	default:
		return &models.Condition{Type: "and", And: items}
	}
}

// attachRemoteCondition 把远程条件写入调度定义；已有条件时合并为 AND。
func attachRemoteCondition(raw string, remote *models.Condition) (map[string]any, bool, error) {
	var def map[string]any
	if err := json.Unmarshal([]byte(raw), &def); err != nil {
		return nil, false, fmt.Errorf("parse definition: %w", err)
	}
	var sch models.Schedule
	if err := json.Unmarshal([]byte(raw), &sch); err != nil {
		return nil, false, fmt.Errorf("parse schedule: %w", err)
	}
	merged := models.MergeConditions(sch.Condition, remote)
	if merged == nil {
		return def, false, nil
	}
	def["condition"] = merged
	return def, true, nil
}
