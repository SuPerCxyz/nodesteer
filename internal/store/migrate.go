package store

import (
	"context"
	"database/sql"
	"fmt"
	"sort"
)

// migration 单条迁移
type migration struct {
	version int
	name    string
	sql     string
}

// migrations 按版本排序的迁移列表
var migrations = []migration{
	{1, "initial_hub_schema", schemaV1},
	{2, "revision_changelog", schemaV2},
	{3, "execution_slot_partial_index", schemaV3},
	{4, "tombstones", schemaV4},
	{5, "scripts_run_user", schemaV5},
	{6, "application_node_state", schemaV6},
	{7, "execution_sync_state", schemaV7},
	{8, "file_transfers", schemaV8},
	{9, "user_avatar_url", schemaV9},
}

// Migrate 应用所有未执行的迁移
func Migrate(db *sql.DB) error {
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS schema_migrations (
		version INTEGER PRIMARY KEY,
		name TEXT NOT NULL,
		applied_at TEXT NOT NULL DEFAULT (datetime('now'))
	)`); err != nil {
		return fmt.Errorf("create schema_migrations: %w", err)
	}

	applied := map[int]bool{}
	rows, err := db.Query(`SELECT version FROM schema_migrations`)
	if err != nil {
		return err
	}
	for rows.Next() {
		var v int
		if err := rows.Scan(&v); err != nil {
			rows.Close()
			return err
		}
		applied[v] = true
	}
	rows.Close()

	sorted := make([]migration, len(migrations))
	copy(sorted, migrations)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].version < sorted[j].version })

	for _, m := range sorted {
		if applied[m.version] {
			continue
		}
		tx, err := db.Begin()
		if err != nil {
			return err
		}
		if _, err := tx.Exec(m.sql); err != nil {
			tx.Rollback()
			return fmt.Errorf("apply migration %d (%s): %w", m.version, m.name, err)
		}
		if _, err := tx.Exec(`INSERT INTO schema_migrations (version, name) VALUES (?, ?)`, m.version, m.name); err != nil {
			tx.Rollback()
			return err
		}
		if err := tx.Commit(); err != nil {
			return err
		}
	}
	return nil
}

// schemaV1 基础 schema
const schemaV1 = `
CREATE TABLE IF NOT EXISTS users (
	id TEXT PRIMARY KEY,
	username TEXT UNIQUE NOT NULL,
	password_hash TEXT NOT NULL,
	role TEXT NOT NULL DEFAULT 'viewer',
	created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
	id TEXT PRIMARY KEY,
	agent_id TEXT UNIQUE NOT NULL,
	hostname TEXT NOT NULL,
	ip TEXT NOT NULL DEFAULT '',
	os TEXT NOT NULL DEFAULT '',
	arch TEXT NOT NULL DEFAULT '',
	agent_version TEXT NOT NULL DEFAULT '',
	deployment_mode TEXT NOT NULL DEFAULT 'native',
	host_integration INTEGER NOT NULL DEFAULT 0,
	status TEXT NOT NULL DEFAULT 'offline',
	global_revision INTEGER NOT NULL DEFAULT 0,
	sync_status TEXT NOT NULL DEFAULT '',
	last_seen TEXT NOT NULL DEFAULT '',
	first_seen TEXT NOT NULL DEFAULT '',
	inventory_json TEXT NOT NULL DEFAULT '',
	capabilities_json TEXT NOT NULL DEFAULT '',
	labels_json TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS node_groups (
	id TEXT PRIMARY KEY,
	name TEXT UNIQUE NOT NULL,
	description TEXT NOT NULL DEFAULT '',
	type TEXT NOT NULL DEFAULT 'static',
	label_key TEXT NOT NULL DEFAULT '',
	label_value TEXT NOT NULL DEFAULT '',
	created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_group_members (
	group_id TEXT NOT NULL,
	node_id TEXT NOT NULL,
	PRIMARY KEY (group_id, node_id)
);

CREATE TABLE IF NOT EXISTS scripts (
	id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	description TEXT NOT NULL DEFAULT '',
	interpreter TEXT NOT NULL DEFAULT 'shell',
	content TEXT NOT NULL,
	parameters_json TEXT NOT NULL DEFAULT '[]',
	environment_json TEXT NOT NULL DEFAULT '{}',
	working_dir TEXT NOT NULL DEFAULT '',
	timeout INTEGER NOT NULL DEFAULT 300,
	enabled INTEGER NOT NULL DEFAULT 1,
	revision INTEGER NOT NULL DEFAULT 1,
	sha256 TEXT NOT NULL DEFAULT '',
	updated_at TEXT NOT NULL,
	created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS script_revisions (
	id TEXT NOT NULL,
	revision INTEGER NOT NULL,
	content TEXT NOT NULL,
	sha256 TEXT NOT NULL,
	changed_at TEXT NOT NULL,
	PRIMARY KEY (id, revision)
);

CREATE TABLE IF NOT EXISTS tasks (
	id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	definition_json TEXT NOT NULL,
	revision INTEGER NOT NULL DEFAULT 1,
	updated_at TEXT NOT NULL,
	created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_revisions (
	id TEXT NOT NULL,
	revision INTEGER NOT NULL,
	definition_json TEXT NOT NULL,
	changed_at TEXT NOT NULL,
	PRIMARY KEY (id, revision)
);

CREATE TABLE IF NOT EXISTS schedules (
	id TEXT PRIMARY KEY,
	task_id TEXT NOT NULL,
	revision INTEGER NOT NULL DEFAULT 1,
	definition_json TEXT NOT NULL,
	enabled INTEGER NOT NULL DEFAULT 1,
	updated_at TEXT NOT NULL,
	created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
	id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	version TEXT NOT NULL,
	architecture TEXT NOT NULL,
	filename TEXT NOT NULL,
	size INTEGER NOT NULL,
	sha256 TEXT NOT NULL,
	storage_path TEXT NOT NULL,
	uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
	id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	definition_json TEXT NOT NULL,
	revision INTEGER NOT NULL DEFAULT 1,
	updated_at TEXT NOT NULL,
	created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS application_revisions (
	id TEXT NOT NULL,
	revision INTEGER NOT NULL,
	definition_json TEXT NOT NULL,
	changed_at TEXT NOT NULL,
	PRIMARY KEY (id, revision)
);

CREATE TABLE IF NOT EXISTS application_assignments (
	application_id TEXT NOT NULL,
	node_id TEXT NOT NULL,
	PRIMARY KEY (application_id, node_id)
);

CREATE TABLE IF NOT EXISTS executions (
	id TEXT PRIMARY KEY,
	task_id TEXT NOT NULL,
	task_revision INTEGER NOT NULL DEFAULT 0,
	script_id TEXT NOT NULL DEFAULT '',
	script_revision INTEGER NOT NULL DEFAULT 0,
	node_id TEXT NOT NULL,
	trigger_type TEXT NOT NULL,
	scheduled_time TEXT NOT NULL DEFAULT '',
	start_time TEXT NOT NULL DEFAULT '',
	end_time TEXT NOT NULL DEFAULT '',
	status TEXT NOT NULL,
	exit_code INTEGER NOT NULL DEFAULT 0,
	stdout TEXT NOT NULL DEFAULT '',
	stderr TEXT NOT NULL DEFAULT '',
	stdout_truncated INTEGER NOT NULL DEFAULT 0,
	stderr_truncated INTEGER NOT NULL DEFAULT 0,
	offline INTEGER NOT NULL DEFAULT 0,
	block_reason TEXT NOT NULL DEFAULT '',
	created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_slot ON executions (task_id, node_id, scheduled_time);

CREATE TABLE IF NOT EXISTS agent_sync_state (
	node_id TEXT PRIMARY KEY,
	global_revision INTEGER NOT NULL DEFAULT 0,
	last_sync TEXT NOT NULL DEFAULT '',
	sync_status TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS remote_state (
	node_id TEXT NOT NULL,
	property TEXT NOT NULL,
	value TEXT NOT NULL DEFAULT '',
	observed_at TEXT NOT NULL,
	expires_at TEXT NOT NULL,
	PRIMARY KEY (node_id, property)
);

CREATE TABLE IF NOT EXISTS audit_logs (
	id TEXT PRIMARY KEY,
	user_id TEXT NOT NULL DEFAULT '',
	username TEXT NOT NULL DEFAULT '',
	action TEXT NOT NULL,
	resource TEXT NOT NULL DEFAULT '',
	resource_id TEXT NOT NULL DEFAULT '',
	detail TEXT NOT NULL DEFAULT '',
	created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
	key TEXT PRIMARY KEY,
	value TEXT NOT NULL
);
`

// schemaV2 Revision Change Log
const schemaV2 = `
CREATE TABLE IF NOT EXISTS revision_sequence (
	value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO revision_sequence (value) VALUES (0);

CREATE TABLE IF NOT EXISTS revision_changes (
	global_revision INTEGER PRIMARY KEY,
	object_type TEXT NOT NULL,
	object_id TEXT NOT NULL,
	object_revision INTEGER NOT NULL,
	operation TEXT NOT NULL,
	created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revision_changes_global ON revision_changes (global_revision);
`

// schemaV3 执行 slot 唯一索引改为部分索引：仅对计划调度（scheduled_time 非空）生效，
// 避免同一 Task+Node 的手动执行（scheduled_time 为空）被错误视为重复。
// 同时把历史写入的 Go 零值时间（0001-01-01T00:00:00Z）归一化为空串。
const schemaV3 = `
DROP INDEX IF EXISTS idx_execution_slot;
UPDATE executions SET scheduled_time='' WHERE scheduled_time='0001-01-01T00:00:00Z';
UPDATE executions SET start_time='' WHERE start_time='0001-01-01T00:00:00Z';
UPDATE executions SET end_time='' WHERE end_time='0001-01-01T00:00:00Z';
CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_slot
	ON executions (task_id, node_id, scheduled_time)
	WHERE scheduled_time != '';
`

// schemaV4 Tombstone：删除 Desired State 对象时记录，供 Agent 同步删除本地副本
const schemaV4 = `
CREATE TABLE IF NOT EXISTS tombstones (
	object_type TEXT NOT NULL,
	object_id TEXT NOT NULL,
	global_revision INTEGER NOT NULL,
	deleted_at TEXT NOT NULL,
	PRIMARY KEY (object_type, object_id)
);
CREATE INDEX IF NOT EXISTS idx_tombstones_rev ON tombstones (global_revision);

CREATE TABLE IF NOT EXISTS log_chunks (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	execution_id TEXT NOT NULL,
	stream TEXT NOT NULL,
	seq INTEGER NOT NULL DEFAULT 0,
	chunk TEXT NOT NULL,
	received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_chunks_exec ON log_chunks (execution_id, seq);
`

// schemaV5 scripts 增加 run_user 列
const schemaV5 = `
ALTER TABLE scripts ADD COLUMN run_user TEXT NOT NULL DEFAULT '';
`

// schemaV6 应用节点状态
const schemaV6 = `
CREATE TABLE IF NOT EXISTS application_node_state (
	application_id TEXT NOT NULL,
	node_id TEXT NOT NULL,
	version TEXT NOT NULL DEFAULT '',
	operation TEXT NOT NULL DEFAULT '',
	health TEXT NOT NULL DEFAULT 'unknown',
	error TEXT NOT NULL DEFAULT '',
	updated_at TEXT NOT NULL,
	PRIMARY KEY (application_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_application_node_state_node ON application_node_state (node_id);
`

const schemaV7 = `
ALTER TABLE executions ADD COLUMN synced INTEGER NOT NULL DEFAULT 0;
`

// schemaV8 Hub 中继文件传输。
const schemaV8 = `
CREATE TABLE IF NOT EXISTS file_transfers (
	id TEXT PRIMARY KEY,
	source_node_id TEXT NOT NULL,
	source_path TEXT NOT NULL,
	source_mode INTEGER NOT NULL DEFAULT 0,
	size INTEGER NOT NULL DEFAULT 0,
	sha256 TEXT NOT NULL DEFAULT '',
	status TEXT NOT NULL,
	error TEXT NOT NULL DEFAULT '',
	created_at TEXT NOT NULL,
	updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_transfers_status ON file_transfers(status, updated_at);

CREATE TABLE IF NOT EXISTS file_transfer_targets (
	transfer_id TEXT NOT NULL,
	node_id TEXT NOT NULL,
	destination_path TEXT NOT NULL,
	mode INTEGER NOT NULL DEFAULT 0,
	status TEXT NOT NULL,
	error TEXT NOT NULL DEFAULT '',
	updated_at TEXT NOT NULL,
	PRIMARY KEY (transfer_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_file_transfer_targets_node ON file_transfer_targets(node_id, status);
`

// schemaV9 users 增加头像列。ADD COLUMN 带非空默认值，
// 既有行自动回填空串，无数据回填负担。
const schemaV9 = `
ALTER TABLE users ADD COLUMN avatar_url TEXT NOT NULL DEFAULT '';
`

var _ = context.Background
