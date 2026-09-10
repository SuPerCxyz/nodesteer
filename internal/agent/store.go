package agent

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// LocalStore Agent 本地状态存储（SQLite + WAL）
type LocalStore struct {
	db  *sql.DB
	tx  *sql.Tx
	dir string
	mu  sync.Mutex // 串行化写事务与其他写操作，避免 SQLITE_BUSY
}

// execContext 普通写：总是通过连接池执行（写操作串行化避免 SQLITE_BUSY），
// 不读取共享事务句柄 s.tx，保证 scheduler/conn goroutine 的写入与同步事务互斥。
func (s *LocalStore) execContext(ctx context.Context, query string, args ...any) (sql.Result, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.db.ExecContext(ctx, query, args...)
}

// execInTx 同步事务内写：仅由 applySync（持有 s.mu 的 sync goroutine）调用，
// 直接使用活动事务句柄，避免与连接池写并发。
func (s *LocalStore) execInTx(ctx context.Context, query string, args ...any) (sql.Result, error) {
	if s.tx == nil {
		return nil, fmt.Errorf("no active sync transaction")
	}
	return s.tx.ExecContext(ctx, query, args...)
}

// OpenLocalStore 打开/创建本地存储
func OpenLocalStore(dir string) (*LocalStore, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	for _, sub := range []string{"scripts", "artifacts", "applications", "executions", "logs", "tmp"} {
		os.MkdirAll(filepath.Join(dir, sub), 0o755)
	}
	dsn := filepath.Join(dir, "state.db") + "?_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(4)
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, err
	}
	s := &LocalStore{db: db, dir: dir}
	if err := s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

// Dir 数据目录
func (s *LocalStore) Dir() string { return s.dir }

// DB 底层连接
func (s *LocalStore) DB() *sql.DB { return s.db }

// Close 关闭
func (s *LocalStore) Close() error { return s.db.Close() }

const agentSchema = `
CREATE TABLE IF NOT EXISTS identity (
	key TEXT PRIMARY KEY,
	value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
	id INTEGER PRIMARY KEY CHECK (id = 1),
	global_revision INTEGER NOT NULL DEFAULT 0,
	last_sync TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS scripts (
	id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	interpreter TEXT NOT NULL DEFAULT 'shell',
	content TEXT NOT NULL,
	parameters_json TEXT NOT NULL DEFAULT '[]',
	environment_json TEXT NOT NULL DEFAULT '{}',
	working_dir TEXT NOT NULL DEFAULT '',
	run_user TEXT NOT NULL DEFAULT '',
	timeout INTEGER NOT NULL DEFAULT 300,
	enabled INTEGER NOT NULL DEFAULT 1,
	revision INTEGER NOT NULL DEFAULT 0,
	sha256 TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tasks (
	id TEXT PRIMARY KEY,
	definition_json TEXT NOT NULL,
	revision INTEGER NOT NULL DEFAULT 0,
	enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS schedules (
	id TEXT PRIMARY KEY,
	definition_json TEXT NOT NULL,
	revision INTEGER NOT NULL DEFAULT 0,
	enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS applications (
	id TEXT PRIMARY KEY,
	definition_json TEXT NOT NULL,
	revision INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS artifacts (
	sha256 TEXT PRIMARY KEY,
	path TEXT NOT NULL,
	downloaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
	id TEXT PRIMARY KEY,
	task_id TEXT NOT NULL,
	task_revision INTEGER NOT NULL DEFAULT 0,
	script_id TEXT NOT NULL DEFAULT '',
	script_revision INTEGER NOT NULL DEFAULT 0,
	application_id TEXT NOT NULL DEFAULT '',
	application_version TEXT NOT NULL DEFAULT '',
	application_operation TEXT NOT NULL DEFAULT '',
	application_health TEXT NOT NULL DEFAULT '',
	node_id TEXT NOT NULL,
	trigger_type TEXT NOT NULL,
	scheduled_time TEXT NOT NULL DEFAULT '',
	status TEXT NOT NULL,
	exit_code INTEGER NOT NULL DEFAULT 0,
	stdout TEXT NOT NULL DEFAULT '',
	stderr TEXT NOT NULL DEFAULT '',
	stdout_truncated INTEGER NOT NULL DEFAULT 0,
	stderr_truncated INTEGER NOT NULL DEFAULT 0,
	start_time TEXT NOT NULL DEFAULT '',
	end_time TEXT NOT NULL DEFAULT '',
	offline INTEGER NOT NULL DEFAULT 0,
	synced INTEGER NOT NULL DEFAULT 1,
	block_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_local_exec_slot ON executions (task_id, node_id, scheduled_time) WHERE scheduled_time != '';

CREATE TABLE IF NOT EXISTS deployments (
	id TEXT PRIMARY KEY,
	application_id TEXT NOT NULL,
	from_version TEXT NOT NULL DEFAULT '',
	to_version TEXT NOT NULL DEFAULT '',
	phase TEXT NOT NULL,
	backup_path TEXT NOT NULL DEFAULT '',
	backup_config_path TEXT NOT NULL DEFAULT '',
	backup_unit_path TEXT NOT NULL DEFAULT '',
	started_at TEXT NOT NULL,
	updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unit_registry (
	application_id TEXT PRIMARY KEY,
	unit_name TEXT NOT NULL,
	registered_at TEXT NOT NULL
);
`

func (s *LocalStore) migrate() error {
	if _, err := s.db.Exec(agentSchema); err != nil {
		return fmt.Errorf("agent schema: %w", err)
	}
	// 旧库迁移：executions slot 唯一索引改为仅对非空 scheduled_time（Manual 可重复执行）
	if _, err := s.db.Exec(`UPDATE executions SET scheduled_time='' WHERE scheduled_time='0001-01-01T00:00:00Z'`); err != nil {
		return fmt.Errorf("agent exec slot normalize: %w", err)
	}
	// 同名旧索引（完整唯一）需先删除，再重建为部分索引
	if _, err := s.db.Exec(`DROP INDEX IF EXISTS idx_local_exec_slot`); err != nil {
		return err
	}
	if _, err := s.db.Exec(`
		CREATE UNIQUE INDEX IF NOT EXISTS idx_local_exec_slot
			ON executions (task_id, node_id, scheduled_time) WHERE scheduled_time != ''`); err != nil {
		return err
	}
	if _, err := s.db.Exec(`INSERT OR IGNORE INTO sync_state (id, global_revision) VALUES (1, 0)`); err != nil {
		return err
	}
	// 旧库迁移：scripts 增加 run_user 列
	if _, err := s.db.Exec(`ALTER TABLE scripts ADD COLUMN run_user TEXT NOT NULL DEFAULT ''`); err != nil {
		// 已存在则忽略
		if !strings.Contains(err.Error(), "duplicate column") {
			return err
		}
	}
	for _, column := range []string{
		`ALTER TABLE executions ADD COLUMN task_revision INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE executions ADD COLUMN script_id TEXT NOT NULL DEFAULT ''`,
		`ALTER TABLE executions ADD COLUMN script_revision INTEGER NOT NULL DEFAULT 0`,
		`ALTER TABLE executions ADD COLUMN application_id TEXT NOT NULL DEFAULT ''`,
		`ALTER TABLE executions ADD COLUMN application_version TEXT NOT NULL DEFAULT ''`,
		`ALTER TABLE executions ADD COLUMN application_operation TEXT NOT NULL DEFAULT ''`,
		`ALTER TABLE executions ADD COLUMN application_health TEXT NOT NULL DEFAULT ''`,
	} {
		if _, err := s.db.Exec(column); err != nil && !strings.Contains(err.Error(), "duplicate column") {
			return err
		}
	}
	// 旧库迁移：deployments 增加 backup_config_path / backup_unit_path 列
	if _, err := s.db.Exec(`ALTER TABLE deployments ADD COLUMN backup_config_path TEXT NOT NULL DEFAULT ''`); err != nil {
		if !strings.Contains(err.Error(), "duplicate column") {
			return err
		}
	}
	if _, err := s.db.Exec(`ALTER TABLE deployments ADD COLUMN backup_unit_path TEXT NOT NULL DEFAULT ''`); err != nil {
		if !strings.Contains(err.Error(), "duplicate column") {
			return err
		}
	}
	return nil
}

// BeginSync 开启同步事务（持有写锁直至 Commit/Rollback）
func (s *LocalStore) BeginSync(ctx context.Context) error {
	s.mu.Lock()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		s.mu.Unlock()
		return err
	}
	s.tx = tx
	return nil
}

// CommitSync 提交同步事务
func (s *LocalStore) CommitSync() error {
	if s.tx == nil {
		s.mu.Unlock()
		return nil
	}
	err := s.tx.Commit()
	s.tx = nil
	s.mu.Unlock()
	return err
}

// RollbackSync 回滚同步事务
func (s *LocalStore) RollbackSync() error {
	if s.tx == nil {
		s.mu.Unlock()
		return nil
	}
	err := s.tx.Rollback()
	s.tx = nil
	s.mu.Unlock()
	return err
}

// InSyncTx 是否处于同步事务中
func (s *LocalStore) InSyncTx() bool { return s.tx != nil }

// ---------- identity ----------

func (s *LocalStore) SetIdentity(key, value string) error {
	_, err := s.db.Exec(`INSERT INTO identity (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value`, key, value)
	return err
}

func (s *LocalStore) GetIdentity(key string) (string, error) {
	var v string
	err := s.db.QueryRow(`SELECT value FROM identity WHERE key = ?`, key).Scan(&v)
	if err == sql.ErrNoRows {
		return "", nil
	}
	return v, err
}

// ---------- sync state ----------

func (s *LocalStore) GetGlobalRevision() (int64, error) {
	var v int64
	err := s.db.QueryRow(`SELECT global_revision FROM sync_state WHERE id = 1`).Scan(&v)
	return v, err
}

func (s *LocalStore) SetGlobalRevision(rev int64) error {
	_, err := s.execInTx(context.Background(), `UPDATE sync_state SET global_revision = ?, last_sync = ? WHERE id = 1`, rev, time.Now().UTC().Format(time.RFC3339Nano))
	return err
}

// ---------- scripts ----------

// ScriptRow 本地脚本
type ScriptRow struct {
	ID          string
	Name        string
	Interpreter string
	Content     string
	Parameters  []byte
	Environment []byte
	WorkingDir  string
	RunUser     string
	Timeout     int
	Enabled     bool
	Revision    int64
	SHA256      string
}

func (s *LocalStore) UpsertScript(ctx context.Context, r *ScriptRow) error {
	_, err := s.execInTx(ctx, `
		INSERT INTO scripts (id, name, interpreter, content, parameters_json, environment_json,
			working_dir, run_user, timeout, enabled, revision, sha256)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET name=excluded.name, interpreter=excluded.interpreter,
			content=excluded.content, parameters_json=excluded.parameters_json,
			environment_json=excluded.environment_json, working_dir=excluded.working_dir,
			run_user=excluded.run_user,
			timeout=excluded.timeout, enabled=excluded.enabled, revision=excluded.revision,
			sha256=excluded.sha256`,
		r.ID, r.Name, r.Interpreter, r.Content, string(r.Parameters), string(r.Environment),
		r.WorkingDir, r.RunUser, r.Timeout, boolInt(r.Enabled), r.Revision, r.SHA256)
	return err
}

func (s *LocalStore) GetScript(ctx context.Context, id string) (*ScriptRow, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, name, interpreter, content, parameters_json, environment_json,
			working_dir, run_user, timeout, enabled, revision, sha256 FROM scripts WHERE id = ?`, id)
	var r ScriptRow
	var params, env string
	var enabled int
	if err := row.Scan(&r.ID, &r.Name, &r.Interpreter, &r.Content, &params, &env,
		&r.WorkingDir, &r.RunUser, &r.Timeout, &enabled, &r.Revision, &r.SHA256); err != nil {
		return nil, err
	}
	r.Parameters = []byte(params)
	r.Environment = []byte(env)
	r.Enabled = enabled == 1
	return &r, nil
}

func (s *LocalStore) ListScripts(ctx context.Context) ([]*ScriptRow, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, name, interpreter, content, parameters_json, environment_json,
			working_dir, run_user, timeout, enabled, revision, sha256 FROM scripts`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*ScriptRow
	for rows.Next() {
		var r ScriptRow
		var params, env string
		var enabled int
		if err := rows.Scan(&r.ID, &r.Name, &r.Interpreter, &r.Content, &params, &env,
			&r.WorkingDir, &r.RunUser, &r.Timeout, &enabled, &r.Revision, &r.SHA256); err != nil {
			return nil, err
		}
		r.Parameters = []byte(params)
		r.Environment = []byte(env)
		r.Enabled = enabled == 1
		out = append(out, &r)
	}
	return out, rows.Err()
}

func (s *LocalStore) DeleteScript(ctx context.Context, id string) error {
	_, err := s.execInTx(ctx, `DELETE FROM scripts WHERE id = ?`, id)
	return err
}

// ---------- tasks ----------

func (s *LocalStore) UpsertTask(ctx context.Context, id string, definition []byte, revision int64, enabled bool) error {
	_, err := s.execInTx(ctx, `
		INSERT INTO tasks (id, definition_json, revision, enabled) VALUES (?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET definition_json=excluded.definition_json,
			revision=excluded.revision, enabled=excluded.enabled`,
		id, string(definition), revision, boolInt(enabled))
	return err
}

func (s *LocalStore) GetTask(ctx context.Context, id string) ([]byte, error) {
	var def string
	err := s.db.QueryRowContext(ctx, `SELECT definition_json FROM tasks WHERE id = ?`, id).Scan(&def)
	if err != nil {
		return nil, err
	}
	return []byte(def), nil
}

func (s *LocalStore) ListTasks(ctx context.Context) (map[string]int64, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, revision FROM tasks`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]int64{}
	for rows.Next() {
		var id string
		var rev int64
		if err := rows.Scan(&id, &rev); err != nil {
			return nil, err
		}
		out[id] = rev
	}
	return out, nil
}

func (s *LocalStore) DeleteTask(ctx context.Context, id string) error {
	_, err := s.execInTx(ctx, `DELETE FROM tasks WHERE id = ?`, id)
	return err
}

// ---------- schedules ----------

func (s *LocalStore) UpsertSchedule(ctx context.Context, id string, definition []byte, revision int64, enabled bool) error {
	_, err := s.execInTx(ctx, `
		INSERT INTO schedules (id, definition_json, revision, enabled) VALUES (?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET definition_json=excluded.definition_json,
			revision=excluded.revision, enabled=excluded.enabled`,
		id, string(definition), revision, boolInt(enabled))
	return err
}

func (s *LocalStore) ListSchedules(ctx context.Context) ([][]byte, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT definition_json FROM schedules`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out [][]byte
	for rows.Next() {
		var def string
		if err := rows.Scan(&def); err != nil {
			return nil, err
		}
		out = append(out, []byte(def))
	}
	return out, nil
}

func (s *LocalStore) DeleteSchedule(ctx context.Context, id string) error {
	_, err := s.execInTx(ctx, `DELETE FROM schedules WHERE id = ?`, id)
	return err
}

// ---------- applications ----------

func (s *LocalStore) UpsertApplication(ctx context.Context, id string, definition []byte, revision int64) error {
	_, err := s.execInTx(ctx, `
		INSERT INTO applications (id, definition_json, revision) VALUES (?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET definition_json=excluded.definition_json,
			revision=excluded.revision`,
		id, string(definition), revision)
	return err
}

func (s *LocalStore) ListApplications(ctx context.Context) ([][]byte, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT definition_json FROM applications`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out [][]byte
	for rows.Next() {
		var def string
		if err := rows.Scan(&def); err != nil {
			return nil, err
		}
		out = append(out, []byte(def))
	}
	return out, nil
}

func (s *LocalStore) DeleteApplication(ctx context.Context, id string) error {
	_, err := s.execInTx(ctx, `DELETE FROM applications WHERE id = ?`, id)
	return err
}

// PruneObjects 删除不在当前 Hub Desired State 快照中的本地对象。
func (s *LocalStore) PruneObjects(ctx context.Context, scriptIDs, taskIDs, scheduleIDs, appIDs []string) error {
	if s.tx != nil {
		return s.pruneObjects(ctx, s.tx, scriptIDs, taskIDs, scheduleIDs, appIDs)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := s.pruneObjects(ctx, tx, scriptIDs, taskIDs, scheduleIDs, appIDs); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *LocalStore) pruneObjects(ctx context.Context, execer interface {
	ExecContext(context.Context, string, ...any) (sql.Result, error)
}, scriptIDs, taskIDs, scheduleIDs, appIDs []string) error {
	for _, item := range []struct {
		table string
		ids   []string
	}{
		{table: "scripts", ids: scriptIDs},
		{table: "tasks", ids: taskIDs},
		{table: "schedules", ids: scheduleIDs},
		{table: "applications", ids: appIDs},
	} {
		query := "DELETE FROM " + item.table
		var args []any
		if len(item.ids) > 0 {
			placeholders := strings.TrimSuffix(strings.Repeat("?,", len(item.ids)), ",")
			query += " WHERE id NOT IN (" + placeholders + ")"
			for _, id := range item.ids {
				args = append(args, id)
			}
		}
		if _, err := execer.ExecContext(ctx, query, args...); err != nil {
			return err
		}
	}
	return nil
}

// ---------- artifacts ----------

func (s *LocalStore) ArtifactExists(sha string) bool {
	var n int
	_ = s.db.QueryRow(`SELECT COUNT(*) FROM artifacts WHERE sha256 = ?`, sha).Scan(&n)
	return n > 0
}

func (s *LocalStore) RegisterArtifact(sha, path string) error {
	_, err := s.db.Exec(`INSERT OR REPLACE INTO artifacts (sha256, path, downloaded_at) VALUES (?, ?, ?)`,
		sha, path, time.Now().UTC().Format(time.RFC3339Nano))
	return err
}

// ---------- executions ----------

// LocalExecution 本地执行记录
type LocalExecution struct {
	ID                   string
	TaskID               string
	TaskRevision         int64
	ScriptID             string
	ScriptRevision       int64
	ApplicationID        string
	ApplicationVersion   string
	ApplicationOperation string
	ApplicationHealth    string
	NodeID               string
	TriggerType          string
	ScheduledTime        string
	Status               string
	ExitCode             int
	Stdout               string
	Stderr               string
	StdoutTruncated      bool
	StderrTruncated      bool
	StartTime            string
	EndTime              string
	Offline              bool
	Synced               bool
	BlockReason          string
}

func (s *LocalStore) CreateExecution(ctx context.Context, e *LocalExecution) error {
	_, err := s.execContext(ctx, `
		INSERT INTO executions (id, task_id, task_revision, script_id, script_revision, application_id, application_version, application_operation, application_health, node_id, trigger_type, scheduled_time, status, exit_code,
			stdout, stderr, stdout_truncated, stderr_truncated, start_time, end_time, offline, synced, block_reason)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		e.ID, e.TaskID, e.TaskRevision, e.ScriptID, e.ScriptRevision, e.ApplicationID, e.ApplicationVersion, e.ApplicationOperation, e.ApplicationHealth, e.NodeID, e.TriggerType, e.ScheduledTime, e.Status, e.ExitCode,
		e.Stdout, e.Stderr, boolInt(e.StdoutTruncated), boolInt(e.StderrTruncated),
		e.StartTime, e.EndTime, boolInt(e.Offline), boolInt(e.Synced), e.BlockReason)
	return err
}

func (s *LocalStore) UpdateExecution(ctx context.Context, e *LocalExecution) error {
	_, err := s.execContext(ctx, `
		UPDATE executions SET task_revision=?, script_id=?, script_revision=?, application_id=?, application_version=?, application_operation=?, application_health=?, status=?, exit_code=?, stdout=?, stderr=?, stdout_truncated=?,
			stderr_truncated=?, start_time=?, end_time=?, offline=?, synced=?, block_reason=?
		WHERE id=?`,
		e.TaskRevision, e.ScriptID, e.ScriptRevision, e.ApplicationID, e.ApplicationVersion, e.ApplicationOperation, e.ApplicationHealth, e.Status, e.ExitCode, e.Stdout, e.Stderr, boolInt(e.StdoutTruncated),
		boolInt(e.StderrTruncated), e.StartTime, e.EndTime, boolInt(e.Offline),
		boolInt(e.Synced), e.BlockReason, e.ID)
	return err
}

func scanExec(sc interface{ Scan(...any) error }) (*LocalExecution, error) {
	var e LocalExecution
	var outT, errT, offline, synced int
	if err := sc.Scan(&e.ID, &e.TaskID, &e.TaskRevision, &e.ScriptID, &e.ScriptRevision, &e.ApplicationID, &e.ApplicationVersion, &e.ApplicationOperation, &e.ApplicationHealth, &e.NodeID, &e.TriggerType, &e.ScheduledTime, &e.Status,
		&e.ExitCode, &e.Stdout, &e.Stderr, &outT, &errT, &e.StartTime, &e.EndTime,
		&offline, &synced, &e.BlockReason); err != nil {
		return nil, err
	}
	e.StdoutTruncated = outT == 1
	e.StderrTruncated = errT == 1
	e.Offline = offline == 1
	e.Synced = synced == 1
	return &e, nil
}

const execCols = `id, task_id, task_revision, script_id, script_revision, application_id, application_version, application_operation, application_health, node_id, trigger_type, scheduled_time, status, exit_code,
	stdout, stderr, stdout_truncated, stderr_truncated, start_time, end_time, offline, synced, block_reason`

func (s *LocalStore) GetExecution(ctx context.Context, id string) (*LocalExecution, error) {
	row := s.db.QueryRowContext(ctx, `SELECT `+execCols+` FROM executions WHERE id = ?`, id)
	e, err := scanExec(row)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return e, err
}

func (s *LocalStore) FindExecutionBySlot(ctx context.Context, taskID, nodeID, scheduledTime string) (*LocalExecution, error) {
	row := s.db.QueryRowContext(ctx, `SELECT `+execCols+` FROM executions WHERE task_id = ? AND node_id = ? AND scheduled_time = ?`,
		taskID, nodeID, scheduledTime)
	e, err := scanExec(row)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	return e, err
}

func (s *LocalStore) ListUnsyncedExecutions(ctx context.Context) ([]*LocalExecution, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+execCols+` FROM executions WHERE synced = 0 ORDER BY start_time`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*LocalExecution
	for rows.Next() {
		e, err := scanExec(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

func (s *LocalStore) ListRunningExecutions(ctx context.Context) ([]*LocalExecution, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT `+execCols+` FROM executions WHERE status = 'RUNNING'`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*LocalExecution
	for rows.Next() {
		e, err := scanExec(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, e)
	}
	return out, rows.Err()
}

// ---------- deployments ----------

// DeploymentRow 部署 Journal
type DeploymentRow struct {
	ID               string
	ApplicationID    string
	FromVersion      string
	ToVersion        string
	Phase            string
	BackupPath       string
	BackupConfigPath string
	BackupUnitPath   string
	StartedAt        string
	UpdatedAt        string
}

func (s *LocalStore) CreateDeployment(ctx context.Context, d *DeploymentRow) error {
	_, err := s.execContext(ctx, `
		INSERT INTO deployments (id, application_id, from_version, to_version, phase, backup_path, backup_config_path, backup_unit_path, started_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		d.ID, d.ApplicationID, d.FromVersion, d.ToVersion, d.Phase, d.BackupPath,
		d.BackupConfigPath, d.BackupUnitPath, d.StartedAt, d.UpdatedAt)
	return err
}

// SetDeploymentBackups 记录备份路径
func (s *LocalStore) SetDeploymentBackups(ctx context.Context, id, backupPath, backupConfigPath, backupUnitPath string) error {
	_, err := s.execContext(ctx, `UPDATE deployments SET backup_path = ?, backup_config_path = ?, backup_unit_path = ?, updated_at = ? WHERE id = ?`,
		backupPath, backupConfigPath, backupUnitPath, time.Now().UTC().Format(time.RFC3339Nano), id)
	return err
}

func (s *LocalStore) UpdateDeploymentPhase(ctx context.Context, id, phase, backupPath string) error {
	_, err := s.execContext(ctx, `UPDATE deployments SET phase = ?, backup_path = ?, updated_at = ? WHERE id = ?`,
		phase, backupPath, time.Now().UTC().Format(time.RFC3339Nano), id)
	return err
}

func (s *LocalStore) GetActiveDeployments(ctx context.Context) ([]*DeploymentRow, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, application_id, from_version, to_version, phase, backup_path, backup_config_path, backup_unit_path, started_at, updated_at
		FROM deployments WHERE phase NOT IN ('DONE') ORDER BY started_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*DeploymentRow
	for rows.Next() {
		var d DeploymentRow
		if err := rows.Scan(&d.ID, &d.ApplicationID, &d.FromVersion, &d.ToVersion, &d.Phase,
			&d.BackupPath, &d.BackupConfigPath, &d.BackupUnitPath, &d.StartedAt, &d.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, &d)
	}
	return out, rows.Err()
}

// ListAllDeployments 所有部署记录
func (s *LocalStore) ListAllDeployments(ctx context.Context) ([]*DeploymentRow, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, application_id, from_version, to_version, phase, backup_path, backup_config_path, backup_unit_path, started_at, updated_at
		FROM deployments ORDER BY started_at DESC LIMIT 100`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*DeploymentRow
	for rows.Next() {
		var d DeploymentRow
		if err := rows.Scan(&d.ID, &d.ApplicationID, &d.FromVersion, &d.ToVersion, &d.Phase,
			&d.BackupPath, &d.BackupConfigPath, &d.BackupUnitPath, &d.StartedAt, &d.UpdatedAt); err != nil {
			return nil, err
		}
		out = append(out, &d)
	}
	return out, rows.Err()
}

// MarshalJSON 辅助
func marshalJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		return nil
	}
	return b
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// RegisterUnit 登记 Managed Unit（systemd Unit Registry）
func (s *LocalStore) RegisterUnit(ctx context.Context, applicationID, unitName string) error {
	_, err := s.execContext(ctx, `
		INSERT INTO unit_registry (application_id, unit_name, registered_at) VALUES (?, ?, ?)
		ON CONFLICT(application_id) DO UPDATE SET unit_name=excluded.unit_name, registered_at=excluded.registered_at`,
		applicationID, unitName, time.Now().UTC().Format(time.RFC3339Nano))
	return err
}

// IsUnitRegistered 校验 Unit 是否已登记
func (s *LocalStore) IsUnitRegistered(ctx context.Context, applicationID, unitName string) (bool, error) {
	var stored string
	err := s.db.QueryRowContext(ctx, `SELECT unit_name FROM unit_registry WHERE application_id = ?`, applicationID).Scan(&stored)
	if err != nil {
		if err == sql.ErrNoRows {
			return false, nil
		}
		return false, err
	}
	return stored == unitName, nil
}

// DeleteUnit 移除受管控应用的 Unit 登记。
func (s *LocalStore) DeleteUnit(ctx context.Context, applicationID string) error {
	_, err := s.execContext(ctx, `DELETE FROM unit_registry WHERE application_id = ?`, applicationID)
	return err
}
