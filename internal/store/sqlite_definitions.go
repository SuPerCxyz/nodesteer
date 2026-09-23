package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/google/uuid"
)

// ---------- Scripts ----------

func (s *SQLiteStore) CreateScript(ctx context.Context, sc *models.Script) error {
	if sc.ID == "" {
		sc.ID = uuid.NewString()
	}
	if sc.Revision == 0 {
		sc.Revision = 1
	}
	var tx *sql.Tx
	if s.txFrom(ctx) == nil {
		var err error
		tx, err = s.db.BeginTx(ctx, nil)
		if err != nil {
			return err
		}
		ctx = context.WithValue(ctx, txContextKey{}, tx)
		defer tx.Rollback()
	}
	execer := s.execer(ctx)
	if _, err := execer.ExecContext(ctx, `
		INSERT INTO scripts (id, name, description, interpreter, content, parameters_json, environment_json,
			working_dir, run_user, timeout, enabled, revision, sha256, updated_at, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		sc.ID, sc.Name, sc.Description, sc.Interpreter, sc.Content,
		marshalOrEmpty(sc.Parameters), marshalOrEmpty(sc.Environment),
		sc.WorkingDir, sc.RunUser, sc.Timeout, boolToInt(sc.Enabled), sc.Revision, sc.SHA256, now(), now()); err != nil {
		return err
	}
	if _, err := execer.ExecContext(ctx, `
		INSERT INTO script_revisions (id, revision, content, sha256, changed_at) VALUES (?, ?, ?, ?, ?)`,
		sc.ID, sc.Revision, sc.Content, sc.SHA256, now()); err != nil {
		return err
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func (s *SQLiteStore) UpdateScript(ctx context.Context, sc *models.Script, prevRevision int64) error {
	var tx *sql.Tx
	if s.txFrom(ctx) == nil {
		var err error
		tx, err = s.db.BeginTx(ctx, nil)
		if err != nil {
			return err
		}
		ctx = context.WithValue(ctx, txContextKey{}, tx)
		defer tx.Rollback()
	}
	execer := s.execer(ctx)
	if _, err := execer.ExecContext(ctx, `
		UPDATE scripts SET name=?, description=?, interpreter=?, content=?, parameters_json=?,
			environment_json=?, working_dir=?, run_user=?, timeout=?, enabled=?, revision=?, sha256=?, updated_at=?
		WHERE id=?`,
		sc.Name, sc.Description, sc.Interpreter, sc.Content,
		marshalOrEmpty(sc.Parameters), marshalOrEmpty(sc.Environment),
		sc.WorkingDir, sc.RunUser, sc.Timeout, boolToInt(sc.Enabled), sc.Revision, sc.SHA256, now(), sc.ID); err != nil {
		return err
	}
	if _, err := execer.ExecContext(ctx, `
		INSERT INTO script_revisions (id, revision, content, sha256, changed_at) VALUES (?, ?, ?, ?, ?)`,
		sc.ID, sc.Revision, sc.Content, sc.SHA256, now()); err != nil {
		return err
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func (s *SQLiteStore) GetScript(ctx context.Context, id string) (*models.Script, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, name, description, interpreter, content, parameters_json, environment_json,
			working_dir, run_user, timeout, enabled, revision, sha256, updated_at, created_at FROM scripts WHERE id = ?`, id)
	var sc models.Script
	var params, env string
	var updated, created string
	var enabled int
	if err := row.Scan(&sc.ID, &sc.Name, &sc.Description, &sc.Interpreter, &sc.Content, &params, &env,
		&sc.WorkingDir, &sc.RunUser, &sc.Timeout, &enabled, &sc.Revision, &sc.SHA256, &updated, &created); err != nil {
		return nil, err
	}
	sc.Enabled = enabled == 1
	json.Unmarshal([]byte(params), &sc.Parameters)
	json.Unmarshal([]byte(env), &sc.Environment)
	sc.UpdatedAt = parseTime(updated)
	sc.CreatedAt = parseTime(created)
	return &sc, nil
}

func (s *SQLiteStore) ListScripts(ctx context.Context) ([]*models.Script, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, name, description, interpreter, content, parameters_json, environment_json,
			working_dir, run_user, timeout, enabled, revision, sha256, updated_at, created_at FROM scripts ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Script{}
	for rows.Next() {
		var sc models.Script
		var params, env string
		var updated, created string
		var enabled int
		if err := rows.Scan(&sc.ID, &sc.Name, &sc.Description, &sc.Interpreter, &sc.Content, &params, &env,
			&sc.WorkingDir, &sc.RunUser, &sc.Timeout, &enabled, &sc.Revision, &sc.SHA256, &updated, &created); err != nil {
			return nil, err
		}
		sc.Enabled = enabled == 1
		json.Unmarshal([]byte(params), &sc.Parameters)
		json.Unmarshal([]byte(env), &sc.Environment)
		sc.UpdatedAt = parseTime(updated)
		sc.CreatedAt = parseTime(created)
		out = append(out, &sc)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) DeleteScript(ctx context.Context, id string) error {
	_, err := s.execer(ctx).ExecContext(ctx, `DELETE FROM scripts WHERE id = ?`, id)
	return err
}

func (s *SQLiteStore) GetScriptRevision(ctx context.Context, id string, revision int64) (*ScriptRevisionEntry, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT revision, content, sha256, changed_at FROM script_revisions WHERE id = ? AND revision = ?`, id, revision)
	var e ScriptRevisionEntry
	var changed string
	if err := row.Scan(&e.Revision, &e.Content, &e.SHA256, &changed); err != nil {
		return nil, err
	}
	e.ChangedAt = parseTime(changed)
	return &e, nil
}

func (s *SQLiteStore) ListScriptRevisions(ctx context.Context, id string) ([]*ScriptRevisionEntry, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT revision, content, sha256, changed_at FROM script_revisions WHERE id = ? ORDER BY revision DESC`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*ScriptRevisionEntry{}
	for rows.Next() {
		var e ScriptRevisionEntry
		var changed string
		if err := rows.Scan(&e.Revision, &e.Content, &e.SHA256, &changed); err != nil {
			return nil, err
		}
		e.ChangedAt = parseTime(changed)
		out = append(out, &e)
	}
	return out, rows.Err()
}

// ---------- Tasks ----------

func (s *SQLiteStore) CreateTask(ctx context.Context, t *models.Task) error {
	if t.ID == "" {
		t.ID = uuid.NewString()
	}
	if t.Revision == 0 {
		t.Revision = 1
	}
	def := marshalOrEmpty(t)
	var tx *sql.Tx
	if s.txFrom(ctx) == nil {
		var err error
		tx, err = s.db.BeginTx(ctx, nil)
		if err != nil {
			return err
		}
		ctx = context.WithValue(ctx, txContextKey{}, tx)
		defer tx.Rollback()
	}
	execer := s.execer(ctx)
	if _, err := execer.ExecContext(ctx, `
		INSERT INTO tasks (id, name, definition_json, revision, updated_at, created_at) VALUES (?, ?, ?, ?, ?, ?)`,
		t.ID, t.Name, def, t.Revision, now(), now()); err != nil {
		return err
	}
	if _, err := execer.ExecContext(ctx, `
		INSERT INTO task_revisions (id, revision, definition_json, changed_at) VALUES (?, ?, ?, ?)`,
		t.ID, t.Revision, def, now()); err != nil {
		return err
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func (s *SQLiteStore) UpdateTask(ctx context.Context, t *models.Task, prevRevision int64) error {
	def := marshalOrEmpty(t)
	var tx *sql.Tx
	if s.txFrom(ctx) == nil {
		var err error
		tx, err = s.db.BeginTx(ctx, nil)
		if err != nil {
			return err
		}
		ctx = context.WithValue(ctx, txContextKey{}, tx)
		defer tx.Rollback()
	}
	execer := s.execer(ctx)
	if _, err := execer.ExecContext(ctx, `
		UPDATE tasks SET name=?, definition_json=?, revision=?, updated_at=? WHERE id=?`,
		t.Name, def, t.Revision, now(), t.ID); err != nil {
		return err
	}
	if _, err := execer.ExecContext(ctx, `
		INSERT INTO task_revisions (id, revision, definition_json, changed_at) VALUES (?, ?, ?, ?)`,
		t.ID, t.Revision, def, now()); err != nil {
		return err
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func scanTaskFromJSON(jsonStr string) (*models.Task, error) {
	var t models.Task
	if err := json.Unmarshal([]byte(jsonStr), &t); err != nil {
		return nil, err
	}
	return &t, nil
}

func (s *SQLiteStore) GetTask(ctx context.Context, id string) (*models.Task, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT id, name, definition_json, revision, updated_at, created_at FROM tasks WHERE id = ?`, id)
	var tID, name, def, updated, created string
	var revision int64
	if err := row.Scan(&tID, &name, &def, &revision, &updated, &created); err != nil {
		return nil, err
	}
	t, err := scanTaskFromJSON(def)
	if err != nil {
		return nil, err
	}
	t.ID = tID
	t.Revision = revision
	t.UpdatedAt = parseTime(updated)
	t.CreatedAt = parseTime(created)
	return t, nil
}

func (s *SQLiteStore) ListTasks(ctx context.Context) ([]*models.Task, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, name, definition_json, revision, updated_at, created_at FROM tasks ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Task{}
	for rows.Next() {
		var tID, name, def, updated, created string
		var revision int64
		if err := rows.Scan(&tID, &name, &def, &revision, &updated, &created); err != nil {
			return nil, err
		}
		t, err := scanTaskFromJSON(def)
		if err != nil {
			return nil, err
		}
		t.ID = tID
		t.Revision = revision
		t.UpdatedAt = parseTime(updated)
		t.CreatedAt = parseTime(created)
		out = append(out, t)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) DeleteTask(ctx context.Context, id string) error {
	_, err := s.execer(ctx).ExecContext(ctx, `DELETE FROM tasks WHERE id = ?`, id)
	return err
}

// ---------- Schedules ----------

func (s *SQLiteStore) CreateSchedule(ctx context.Context, sch *models.Schedule) error {
	if sch.ID == "" {
		sch.ID = uuid.NewString()
	}
	if sch.Revision == 0 {
		sch.Revision = 1
	}
	_, err := s.execer(ctx).ExecContext(ctx, `
		INSERT INTO schedules (id, task_id, revision, definition_json, enabled, updated_at, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		sch.ID, sch.TaskID, sch.Revision, marshalOrEmpty(sch), boolToInt(sch.Enabled), now(), now())
	return err
}

func (s *SQLiteStore) UpdateSchedule(ctx context.Context, sch *models.Schedule, prevRevision int64) error {
	_, err := s.execer(ctx).ExecContext(ctx, `
		UPDATE schedules SET task_id=?, revision=?, definition_json=?, enabled=?, updated_at=? WHERE id=?`,
		sch.TaskID, sch.Revision, marshalOrEmpty(sch), boolToInt(sch.Enabled), now(), sch.ID)
	return err
}

func (s *SQLiteStore) GetSchedule(ctx context.Context, id string) (*models.Schedule, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT id, task_id, revision, definition_json, enabled, updated_at, created_at FROM schedules WHERE id = ?`, id)
	var sch models.Schedule
	var schID, taskID, def, updated, created string
	var revision int64
	var enabled int
	if err := row.Scan(&schID, &taskID, &revision, &def, &enabled, &updated, &created); err != nil {
		return nil, err
	}
	if err := json.Unmarshal([]byte(def), &sch); err != nil {
		return nil, err
	}
	sch.ID = schID
	sch.TaskID = taskID
	sch.Revision = revision
	sch.Enabled = enabled == 1
	sch.UpdatedAt = parseTime(updated)
	sch.CreatedAt = parseTime(created)
	return &sch, nil
}

func (s *SQLiteStore) ListSchedules(ctx context.Context) ([]*models.Schedule, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, task_id, revision, definition_json, enabled, updated_at, created_at FROM schedules ORDER BY created_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Schedule{}
	for rows.Next() {
		var sch models.Schedule
		var schID, taskID, def, updated, created string
		var revision int64
		var enabled int
		if err := rows.Scan(&schID, &taskID, &revision, &def, &enabled, &updated, &created); err != nil {
			return nil, err
		}
		if err := json.Unmarshal([]byte(def), &sch); err != nil {
			return nil, err
		}
		sch.ID = schID
		sch.TaskID = taskID
		sch.Revision = revision
		sch.Enabled = enabled == 1
		sch.UpdatedAt = parseTime(updated)
		sch.CreatedAt = parseTime(created)
		out = append(out, &sch)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) DeleteSchedule(ctx context.Context, id string) error {
	_, err := s.execer(ctx).ExecContext(ctx, `DELETE FROM schedules WHERE id = ?`, id)
	return err
}

var _ = fmt.Sprintf
var _ = time.Time{}
