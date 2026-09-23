package store

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/google/uuid"
)

// ---------- Executions ----------

func (s *SQLiteStore) CreateExecution(ctx context.Context, e *models.Execution) error {
	if e.ID == "" {
		e.ID = uuid.NewString()
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO executions (id, task_id, task_revision, script_id, script_revision, node_id,
			trigger_type, scheduled_time, start_time, end_time, status, exit_code, stdout, stderr,
			stdout_truncated, stderr_truncated, offline, synced, block_reason, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		e.ID, e.TaskID, e.TaskRevision, e.ScriptID, e.ScriptRevision, e.NodeID,
		e.TriggerType, timeToDB(e.ScheduledTime), timeToDB(e.StartTime),
		timeToDB(e.EndTime), e.Status, e.ExitCode, e.Stdout, e.Stderr,
		boolToInt(e.StdoutTruncated), boolToInt(e.StderrTruncated), boolToInt(e.Offline), boolToInt(e.Synced),
		e.BlockReason, now())
	return err
}

func (s *SQLiteStore) UpdateExecution(ctx context.Context, e *models.Execution) error {
	_, err := s.db.ExecContext(ctx, `
		UPDATE executions SET task_id=?, task_revision=?, script_id=?, script_revision=?, node_id=?,
			trigger_type=?, scheduled_time=?, start_time=?, end_time=?, status=?, exit_code=?,
			stdout=?, stderr=?, stdout_truncated=?, stderr_truncated=?, offline=?, synced=?, block_reason=?
		WHERE id=?`,
		e.TaskID, e.TaskRevision, e.ScriptID, e.ScriptRevision, e.NodeID,
		e.TriggerType, timeToDB(e.ScheduledTime), timeToDB(e.StartTime),
		timeToDB(e.EndTime), e.Status, e.ExitCode, e.Stdout, e.Stderr,
		boolToInt(e.StdoutTruncated), boolToInt(e.StderrTruncated), boolToInt(e.Offline), boolToInt(e.Synced),
		e.BlockReason, e.ID)
	return err
}

// timeToDB 零值时间写空串，避免 0001-01-01T00:00:00Z 占用唯一索引
func timeToDB(t time.Time) string {
	if t.IsZero() {
		return ""
	}
	return t.Format(time.RFC3339Nano)
}

func scanExecution(sc interface{ Scan(...any) error }) (*models.Execution, error) {
	var e models.Execution
	var sched, start, end string
	var outT, errT int
	var offline, synced int
	if err := sc.Scan(&e.ID, &e.TaskID, &e.TaskRevision, &e.ScriptID, &e.ScriptRevision, &e.NodeID,
		&e.TriggerType, &sched, &start, &end, &e.Status, &e.ExitCode, &e.Stdout, &e.Stderr,
		&outT, &errT, &offline, &synced, &e.BlockReason); err != nil {
		return nil, err
	}
	e.ScheduledTime = parseTime(sched)
	e.StartTime = parseTime(start)
	e.EndTime = parseTime(end)
	e.StdoutTruncated = outT == 1
	e.StderrTruncated = errT == 1
	e.Offline = offline == 1
	e.Synced = synced == 1
	return &e, nil
}

const execColumns = `id, task_id, task_revision, script_id, script_revision, node_id,
	trigger_type, scheduled_time, start_time, end_time, status, exit_code, stdout, stderr,
	stdout_truncated, stderr_truncated, offline, synced, block_reason`

func (s *SQLiteStore) GetExecution(ctx context.Context, id string) (*models.Execution, error) {
	row := s.db.QueryRowContext(ctx, `SELECT `+execColumns+` FROM executions WHERE id = ?`, id)
	e, err := scanExecution(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, sql.ErrNoRows
	}
	return e, err
}

// executionFilterWhere 构造执行过滤条件（不含分页），List/Count 共用以保证计数与列表语义一致。
func executionFilterWhere(filter ExecutionFilter) (string, []any) {
	query := ` WHERE 1=1`
	var args []any
	if filter.NodeID != "" {
		query += ` AND node_id = ?`
		args = append(args, filter.NodeID)
	}
	if filter.TaskID != "" {
		query += ` AND task_id = ?`
		args = append(args, filter.TaskID)
	}
	if filter.Status != "" {
		query += ` AND status = ?`
		args = append(args, filter.Status)
	}
	return query, args
}

func (s *SQLiteStore) ListExecutions(ctx context.Context, filter ExecutionFilter) ([]*models.Execution, error) {
	where, args := executionFilterWhere(filter)
	query := `SELECT ` + execColumns + ` FROM executions` + where + ` ORDER BY created_at DESC`
	if filter.Limit > 0 {
		query += ` LIMIT ?`
		args = append(args, filter.Limit)
	}
	if filter.Offset > 0 {
		query += ` OFFSET ?`
		args = append(args, filter.Offset)
	}
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Execution{}
	for rows.Next() {
		e, err := scanExecution(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// CountExecutions 统计匹配过滤条件的执行总数，忽略 Limit/Offset。
func (s *SQLiteStore) CountExecutions(ctx context.Context, filter ExecutionFilter) (int64, error) {
	where, args := executionFilterWhere(filter)
	var n int64
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM executions`+where, args...).Scan(&n); err != nil {
		return 0, err
	}
	return n, nil
}

func (s *SQLiteStore) FindExecutionBySlot(ctx context.Context, taskID, nodeID, scheduledTime string) (*models.Execution, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT `+execColumns+` FROM executions WHERE task_id = ? AND node_id = ? AND scheduled_time = ?`,
		taskID, nodeID, scheduledTime)
	e, err := scanExecution(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, sql.ErrNoRows
	}
	return e, err
}

func (s *SQLiteStore) CountExecutionsByStatus(ctx context.Context) (map[string]int, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT status, COUNT(*) FROM executions GROUP BY status`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int{}
	for rows.Next() {
		var st string
		var c int
		if err := rows.Scan(&st, &c); err != nil {
			return nil, err
		}
		out[st] = c
	}
	return out, rows.Err()
}

// ---------- Remote State ----------
func (s *SQLiteStore) SetRemoteState(ctx context.Context, nodeID, property, value string, observedAt time.Time, ttl int64) error {
	expires := observedAt.Add(time.Duration(ttl) * time.Second)
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO remote_state (node_id, property, value, observed_at, expires_at) VALUES (?, ?, ?, ?, ?)
		ON CONFLICT(node_id, property) DO UPDATE SET
			value=excluded.value, observed_at=excluded.observed_at, expires_at=excluded.expires_at`,
		nodeID, property, value, observedAt.Format(time.RFC3339Nano), expires.Format(time.RFC3339Nano))
	return err
}

func (s *SQLiteStore) GetRemoteState(ctx context.Context, nodeID, property string) (string, time.Time, time.Time, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT value, observed_at, expires_at FROM remote_state WHERE node_id = ? AND property = ?`,
		nodeID, property)
	var value, observed, expires string
	if err := row.Scan(&value, &observed, &expires); err != nil {
		return "", time.Time{}, time.Time{}, err
	}
	return value, parseTime(observed), parseTime(expires), nil
}

// ---------- Sync / Revision ----------

func (s *SQLiteStore) NextGlobalRevision(ctx context.Context) (int64, error) {
	var v int64
	err := s.queryer(ctx).QueryRowContext(ctx, `UPDATE revision_sequence SET value = value + 1 RETURNING value`).Scan(&v)
	if errors.Is(err, sql.ErrNoRows) {
		if tx := s.txFrom(ctx); tx != nil {
			var cur int64
			if err := tx.QueryRowContext(ctx, `SELECT value FROM revision_sequence`).Scan(&cur); err != nil {
				return 0, err
			}
			cur++
			if _, err := tx.ExecContext(ctx, `UPDATE revision_sequence SET value = ?`, cur); err != nil {
				return 0, err
			}
			return cur, nil
		}
		// 兼容不支持 RETURNING 的驱动
		tx, err := s.db.BeginTx(ctx, nil)
		if err != nil {
			return 0, err
		}
		defer tx.Rollback()
		var cur int64
		if err := tx.QueryRowContext(ctx, `SELECT value FROM revision_sequence`).Scan(&cur); err != nil {
			return 0, err
		}
		cur++
		if _, err := tx.ExecContext(ctx, `UPDATE revision_sequence SET value = ?`, cur); err != nil {
			return 0, err
		}
		if err := tx.Commit(); err != nil {
			return 0, err
		}
		return cur, nil
	}
	return v, err
}

func (s *SQLiteStore) CurrentGlobalRevision(ctx context.Context) (int64, error) {
	var v int64
	err := s.db.QueryRowContext(ctx, `SELECT value FROM revision_sequence`).Scan(&v)
	return v, err
}

func (s *SQLiteStore) AppendChangeLog(ctx context.Context, globalRev int64, objectType, objectID string, objectRev int64, operation string) error {
	_, err := s.execer(ctx).ExecContext(ctx, `
		INSERT INTO revision_changes (global_revision, object_type, object_id, object_revision, operation, created_at)
		VALUES (?, ?, ?, ?, ?, ?)`, globalRev, objectType, objectID, objectRev, operation, now())
	return err
}

func (s *SQLiteStore) GetChangesSince(ctx context.Context, since int64) ([]*ChangeLogEntry, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT global_revision, object_type, object_id, object_revision, operation, created_at
		FROM revision_changes WHERE global_revision > ? ORDER BY global_revision`, since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*ChangeLogEntry{}
	for rows.Next() {
		var e ChangeLogEntry
		var created string
		if err := rows.Scan(&e.GlobalRevision, &e.ObjectType, &e.ObjectID, &e.ObjectRevision, &e.Operation, &created); err != nil {
			return nil, err
		}
		e.CreatedAt = parseTime(created)
		out = append(out, &e)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) PruneChangeLog(ctx context.Context, keepFrom int64) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM revision_changes WHERE global_revision < ?`, keepFrom)
	return err
}

func (s *SQLiteStore) RecordTombstone(ctx context.Context, objectType, objectID string, globalRev int64) error {
	_, err := s.execer(ctx).ExecContext(ctx, `
		INSERT INTO tombstones (object_type, object_id, global_revision, deleted_at) VALUES (?, ?, ?, ?)
		ON CONFLICT(object_type, object_id) DO UPDATE SET
			global_revision=excluded.global_revision, deleted_at=excluded.deleted_at`,
		objectType, objectID, globalRev, now())
	return err
}

func (s *SQLiteStore) ListTombstones(ctx context.Context) ([]Tombstone, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT object_type, object_id, global_revision, deleted_at FROM tombstones ORDER BY global_revision`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Tombstone{}
	for rows.Next() {
		var t Tombstone
		var deleted string
		if err := rows.Scan(&t.ObjectType, &t.ObjectID, &t.GlobalRevision, &deleted); err != nil {
			return nil, err
		}
		t.DeletedAt = parseTime(deleted)
		out = append(out, t)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) DeleteTombstone(ctx context.Context, objectType, objectID string) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM tombstones WHERE object_type = ? AND object_id = ?`, objectType, objectID)
	return err
}

func (s *SQLiteStore) AppendLogChunk(ctx context.Context, executionID, stream string, seq int64, chunk string) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO log_chunks (execution_id, stream, seq, chunk, received_at) VALUES (?, ?, ?, ?, ?)`,
		executionID, stream, seq, chunk, now())
	return err
}

func (s *SQLiteStore) ListLogChunks(ctx context.Context, executionID string) ([]LogChunk, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT execution_id, stream, seq, chunk FROM log_chunks
		WHERE execution_id = ? ORDER BY seq`, executionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []LogChunk{}
	for rows.Next() {
		var c LogChunk
		if err := rows.Scan(&c.ExecutionID, &c.Stream, &c.Seq, &c.Chunk); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) GetAgentSyncState(ctx context.Context, nodeID string) (*AgentSyncState, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT node_id, global_revision, last_sync, sync_status FROM agent_sync_state WHERE node_id = ?`, nodeID)
	var st AgentSyncState
	var last string
	if err := row.Scan(&st.NodeID, &st.GlobalRevision, &last, &st.SyncStatus); err != nil {
		return nil, err
	}
	st.LastSync = parseTime(last)
	return &st, nil
}

// ---------- Audit ----------

func (s *SQLiteStore) AddAudit(ctx context.Context, a *models.AuditLog) error {
	if a.ID == "" {
		a.ID = uuid.NewString()
	}
	_, err := s.execer(ctx).ExecContext(ctx, `
		INSERT INTO audit_logs (id, user_id, username, action, resource, resource_id, detail, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		a.ID, a.UserID, a.Username, a.Action, a.Resource, a.ResourceID, a.Detail, now())
	return err
}

// auditFilterWhere 构造审计过滤条件（不含分页），List/Count 共用。
func auditFilterWhere(filter AuditFilter) (string, []any) {
	query := ` WHERE 1=1`
	var args []any
	if filter.UserID != "" {
		query += ` AND user_id = ?`
		args = append(args, filter.UserID)
	}
	if filter.Action != "" {
		query += ` AND action = ?`
		args = append(args, filter.Action)
	}
	return query, args
}

func (s *SQLiteStore) ListAudit(ctx context.Context, filter AuditFilter) ([]*models.AuditLog, error) {
	where, args := auditFilterWhere(filter)
	query := `SELECT id, user_id, username, action, resource, resource_id, detail, created_at FROM audit_logs` +
		where + ` ORDER BY created_at DESC`
	if filter.Limit > 0 {
		query += ` LIMIT ?`
		args = append(args, filter.Limit)
	}
	if filter.Offset > 0 {
		query += ` OFFSET ?`
		args = append(args, filter.Offset)
	}
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.AuditLog{}
	for rows.Next() {
		var a models.AuditLog
		var created string
		if err := rows.Scan(&a.ID, &a.UserID, &a.Username, &a.Action, &a.Resource, &a.ResourceID,
			&a.Detail, &created); err != nil {
			return nil, err
		}
		a.CreatedAt = parseTime(created)
		out = append(out, &a)
	}
	return out, rows.Err()
}

// CountAudit 统计匹配过滤条件的审计总数，忽略 Limit/Offset。
func (s *SQLiteStore) CountAudit(ctx context.Context, filter AuditFilter) (int64, error) {
	where, args := auditFilterWhere(filter)
	var n int64
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM audit_logs`+where, args...).Scan(&n); err != nil {
		return 0, err
	}
	return n, nil
}

// ---------- Settings ----------

func (s *SQLiteStore) GetSetting(ctx context.Context, key string) (string, error) {
	var v string
	err := s.db.QueryRowContext(ctx, `SELECT value FROM settings WHERE key = ?`, key).Scan(&v)
	return v, err
}

func (s *SQLiteStore) SetSetting(ctx context.Context, key, value string) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO settings (key, value) VALUES (?, ?)
		ON CONFLICT(key) DO UPDATE SET value=excluded.value`, key, value)
	return err
}

func (s *SQLiteStore) DeleteSetting(ctx context.Context, key string) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM settings WHERE key = ?`, key)
	return err
}
