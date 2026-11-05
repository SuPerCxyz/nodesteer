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
	{10, "execution_schedule_created_at", schemaV10},
	{11, "execution_schedule_revision", schemaV11},
	{12, "monitoring_http_targets", schemaV12},
	{13, "monitoring_rules", schemaV13},
	{14, "monitoring_node_bindings", schemaV14},
	{15, "monitoring_rule_steps", schemaV15},
	{16, "monitoring_rule_event_context", schemaV16},
	{17, "monitoring_rule_confirmation", schemaV17},
	{18, "monitoring_rule_interruption", schemaV18},
	{19, "monitoring_rule_sample_boundary", schemaV19},
	{20, "monitoring_rule_sample_identity", schemaV20},
	{21, "monitoring_rule_sample_window", schemaV21},
	{22, "monitoring_observation_latest_index", schemaV22},
	{23, "native_node_metrics", schemaV23},
	{24, "alerting", schemaV24},
	{25, "native_metrics_rollup_index_and_legacy_binding_cleanup", schemaV25},
	{26, "node_display_name", schemaV26},
	{27, "agent_inbound_connection", schemaV27},
}

// schemaV27 Hub 主动拨号入站连接所需的节点配置与诊断列。
//
// 只新增列并给安全默认值：不回填、不修改既有 hostname。endpoint 是管理员
// 配置的拨号地址；connection_mode 只保存最近一次成功会话方向（空=从未成功）。
// 入站身份/诊断列必须从 UpsertNode 的 ON CONFLICT 更新列表排除，避免 Agent
// 注册覆盖管理员配置。
const schemaV27 = `
ALTER TABLE nodes ADD COLUMN endpoint TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN connection_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_provisioned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE nodes ADD COLUMN inbound_cert_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_cert_serial TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_cert_not_after TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_token_enc TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_last_handshake TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_last_error TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN endpoint_updated_at TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN endpoint_updated_by TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_nodes_inbound_candidates
	ON nodes(inbound_provisioned, status) WHERE endpoint != '';
`

// schemaV26 节点显示名：与 Agent 上报的 hostname 解耦，供 Operator 设置稳定名称。
// 旧行默认空，不回填；显示名不参与身份、匹配、权限或路由。UpsertNode 的
// ON CONFLICT 更新列表不得包含该列，避免 HELLO/重新注册覆盖人工命名。
const schemaV26 = `
ALTER TABLE nodes ADD COLUMN display_name TEXT NOT NULL DEFAULT '';
`

// schemaV25 原生指标查询索引与旧 Nocturne 绑定表清理。
//
// node_metric_rollups 的查询以 (node_id, tier, bucket_start) 过滤为主，
// 原主键前缀为 (node_id, metric, series_key, tier, ...)，无法直接服务
// node_id+tier 的连续范围扫描，因此补充专用索引。
//
// monitoring_node_bindings 自 Nocturne 移除后不再被任何代码、API 或配置引用，
// 仅保留历史绑定元数据；本迁移在事务内物理删除。迁移失败会随事务回滚，
// 升级前仍建议按部署文档备份 Hub DB，以便迁移提交后需要恢复历史数据时使用。
const schemaV25 = `
CREATE INDEX IF NOT EXISTS idx_node_metric_rollups_node_tier_time
	ON node_metric_rollups(node_id, tier, bucket_start);
DROP TABLE IF EXISTS monitoring_node_bindings;
`

// schemaV23 NodeSteer 原生节点指标：原始样本与聚合桶。
// timestamp/bucket_start 使用固定宽度 UTC RFC3339Nano 文本，保证字典序等于时间序。
const schemaV23 = `
CREATE TABLE node_metric_samples (
	node_id TEXT NOT NULL,
	metric TEXT NOT NULL,
	series_key TEXT NOT NULL,
	timestamp TEXT NOT NULL,
	value REAL,
	quality INTEGER NOT NULL,
	dimensions_json TEXT NOT NULL DEFAULT '{}',
	PRIMARY KEY (node_id, metric, series_key, timestamp)
);
CREATE INDEX idx_node_metric_samples_time ON node_metric_samples(timestamp);
CREATE INDEX idx_node_metric_samples_node_time ON node_metric_samples(node_id, timestamp);

CREATE TABLE node_metric_rollups (
	node_id TEXT NOT NULL,
	metric TEXT NOT NULL,
	series_key TEXT NOT NULL,
	tier TEXT NOT NULL,
	bucket_start TEXT NOT NULL,
	count INTEGER NOT NULL,
	expected INTEGER NOT NULL,
	missing INTEGER NOT NULL,
	sum REAL NOT NULL,
	min REAL NOT NULL,
	max REAL NOT NULL,
	first REAL NOT NULL,
	last REAL NOT NULL,
	first_ts TEXT NOT NULL,
	last_ts TEXT NOT NULL,
	quality INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY (node_id, metric, series_key, tier, bucket_start)
);
CREATE INDEX idx_node_metric_rollups_tier_time ON node_metric_rollups(tier, bucket_start);
`

// schemaV24 原生指标阈值告警与多渠道通知。
//
// 敏感配置只保存环境变量名（url_env/token_env/password_env/to_env），
// 实际值在发送时从进程环境读取，绝不落库。
const schemaV24 = `
CREATE TABLE IF NOT EXISTS alert_rules (
	id TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	description TEXT NOT NULL DEFAULT '',
	node_id TEXT NOT NULL DEFAULT '',
	metric TEXT NOT NULL,
	series TEXT NOT NULL DEFAULT '',
	operator TEXT NOT NULL,
	threshold REAL NOT NULL,
	duration_sec INTEGER NOT NULL DEFAULT 0,
	recovery_sec INTEGER NOT NULL DEFAULT 0,
	cooldown_sec INTEGER NOT NULL DEFAULT 0,
	severity TEXT NOT NULL DEFAULT 'warning',
	channels_json TEXT NOT NULL DEFAULT '[]',
	enabled INTEGER NOT NULL DEFAULT 1,
	silence_until TEXT NOT NULL DEFAULT '',
	created_at TEXT NOT NULL,
	updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled ON alert_rules(enabled, metric);
CREATE INDEX IF NOT EXISTS idx_alert_rules_node ON alert_rules(node_id);

CREATE TABLE IF NOT EXISTS alert_states (
	rule_id TEXT NOT NULL,
	node_id TEXT NOT NULL,
	metric TEXT NOT NULL,
	series TEXT NOT NULL DEFAULT '',
	status TEXT NOT NULL,
	condition_since TEXT NOT NULL DEFAULT '',
	recovery_since TEXT NOT NULL DEFAULT '',
	last_value REAL,
	last_observed_at TEXT NOT NULL DEFAULT '',
	last_evaluated_at TEXT NOT NULL DEFAULT '',
	last_notified_at TEXT NOT NULL DEFAULT '',
	event_id TEXT NOT NULL DEFAULT '',
	message TEXT NOT NULL DEFAULT '',
	updated_at TEXT NOT NULL,
	PRIMARY KEY (rule_id, node_id, metric, series)
);
CREATE INDEX IF NOT EXISTS idx_alert_states_status ON alert_states(status);

CREATE TABLE IF NOT EXISTS alert_events (
	id TEXT PRIMARY KEY,
	rule_id TEXT NOT NULL,
	rule_name TEXT NOT NULL DEFAULT '',
	node_id TEXT NOT NULL,
	node_hostname TEXT NOT NULL DEFAULT '',
	metric TEXT NOT NULL,
	series TEXT NOT NULL DEFAULT '',
	operator TEXT NOT NULL DEFAULT '',
	status TEXT NOT NULL,
	severity TEXT NOT NULL,
	value REAL,
	threshold REAL NOT NULL DEFAULT 0,
	started_at TEXT NOT NULL DEFAULT '',
	fired_at TEXT NOT NULL DEFAULT '',
	resolved_at TEXT NOT NULL DEFAULT '',
	acknowledged_at TEXT NOT NULL DEFAULT '',
	acknowledged_by TEXT NOT NULL DEFAULT '',
	silenced_until TEXT NOT NULL DEFAULT '',
	message TEXT NOT NULL DEFAULT '',
	error TEXT NOT NULL DEFAULT '',
	notified INTEGER NOT NULL DEFAULT 0,
	created_at TEXT NOT NULL,
	updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_events_status_time ON alert_events(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_events_rule_node ON alert_events(rule_id, node_id, metric, series);
CREATE INDEX IF NOT EXISTS idx_alert_events_time ON alert_events(updated_at DESC);
-- 同一规则+节点+指标+序列最多只有一个活动事件（pending/firing/silenced），
-- 从数据库层保证去重，避免重复通知。
CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_events_active
	ON alert_events(rule_id, node_id, metric, series)
	WHERE status IN ('pending', 'firing', 'silenced');

CREATE TABLE IF NOT EXISTS notification_channels (
	id TEXT PRIMARY KEY,
	name TEXT NOT NULL UNIQUE,
	type TEXT NOT NULL,
	config_json TEXT NOT NULL DEFAULT '{}',
	enabled INTEGER NOT NULL DEFAULT 1,
	created_at TEXT NOT NULL,
	updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_notifications (
	id TEXT PRIMARY KEY,
	event_id TEXT NOT NULL,
	channel_id TEXT NOT NULL,
	channel_type TEXT NOT NULL DEFAULT '',
	status TEXT NOT NULL,
	attempts INTEGER NOT NULL DEFAULT 0,
	last_error TEXT NOT NULL DEFAULT '',
	next_attempt_at TEXT NOT NULL DEFAULT '',
	sent_at TEXT NOT NULL DEFAULT '',
	created_at TEXT NOT NULL,
	updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_notifications_event ON alert_notifications(event_id);
CREATE INDEX IF NOT EXISTS idx_alert_notifications_pending ON alert_notifications(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_alert_notifications_channel ON alert_notifications(channel_id, created_at DESC);

CREATE TABLE IF NOT EXISTS alert_notification_attempts (
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	notification_id TEXT NOT NULL,
	attempt INTEGER NOT NULL,
	status TEXT NOT NULL,
	error TEXT NOT NULL DEFAULT '',
	attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_notification_attempts_notification
	ON alert_notification_attempts(notification_id, attempt);
`

const schemaV22 = `CREATE INDEX monitoring_observations_latest ON monitoring_http_observations(target_id, id DESC);`

const schemaV21 = `
CREATE TABLE monitoring_rule_samples (
 rule_id TEXT NOT NULL REFERENCES monitoring_rules(id) ON DELETE CASCADE,
 observed_at TEXT NOT NULL,
 identity TEXT NOT NULL,
 PRIMARY KEY(rule_id, observed_at)
);
`

const schemaV20 = `ALTER TABLE monitoring_rules ADD COLUMN last_sample TEXT NOT NULL DEFAULT '';`

const schemaV19 = `
ALTER TABLE monitoring_rules ADD COLUMN not_before TEXT NOT NULL DEFAULT '';
UPDATE monitoring_rules SET not_before = strftime('%Y-%m-%dT%H:%M:%fZ','now');
`

const schemaV18 = `ALTER TABLE monitoring_rules ADD COLUMN interrupted INTEGER NOT NULL DEFAULT 0;`

const schemaV17 = `ALTER TABLE monitoring_rules ADD COLUMN last_confirmed_at TEXT NOT NULL DEFAULT '';`

const schemaV16 = `
ALTER TABLE monitoring_rule_events ADD COLUMN observation_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE monitoring_rule_events ADD COLUMN rule_snapshot TEXT NOT NULL DEFAULT '{}';
`

const schemaV15 = `
CREATE TABLE monitoring_rule_steps (
 event_id TEXT NOT NULL REFERENCES monitoring_rule_events(id) ON DELETE CASCADE,
 execution_id TEXT PRIMARY KEY REFERENCES executions(id),
 node_id TEXT NOT NULL,
 phase TEXT NOT NULL,
 batch_index INTEGER NOT NULL
);
CREATE INDEX monitoring_rule_steps_event ON monitoring_rule_steps(event_id);
`

// schemaV14 是旧 Nocturne 节点绑定表，仅作为遗留数据保留。
// 当前代码、API 与配置不再读取或写入该表；schemaV25 在升级时物理删除该表，
// 本迁移自身不删除用户数据。
const schemaV14 = `
CREATE TABLE monitoring_node_bindings (
 node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
 nocturne_node_id TEXT NOT NULL UNIQUE
);
`

const schemaV13 = `
CREATE TABLE monitoring_rules (
 id TEXT PRIMARY KEY,
 target_id TEXT NOT NULL REFERENCES monitoring_http_targets(id),
 config_json TEXT NOT NULL,
 active_since TEXT NOT NULL DEFAULT '',
 last_observed TEXT NOT NULL DEFAULT '',
 last_trigger TEXT NOT NULL DEFAULT '',
 pending_id TEXT NOT NULL DEFAULT '',
 fired INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX monitoring_rules_target ON monitoring_rules(target_id);
CREATE TABLE monitoring_rule_events (
 id TEXT PRIMARY KEY,
 rule_id TEXT NOT NULL,
 target_id TEXT NOT NULL,
 observed_at TEXT NOT NULL,
 status TEXT NOT NULL,
 execution_ids TEXT NOT NULL DEFAULT '[]',
 error TEXT NOT NULL DEFAULT ''
);
`

const schemaV12 = `
CREATE TABLE monitoring_http_targets (
 id TEXT PRIMARY KEY,
 config_json TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE monitoring_http_observations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 target_id TEXT NOT NULL REFERENCES monitoring_http_targets(id) ON DELETE CASCADE,
 observed_at TEXT NOT NULL,
 status_code INTEGER NOT NULL,
 duration_ns INTEGER NOT NULL,
 value_json TEXT NOT NULL,
 error TEXT NOT NULL
);
CREATE INDEX monitoring_http_observations_target_time ON monitoring_http_observations(target_id, observed_at DESC, id DESC);
`

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
	// 数据迁移在 schema 迁移之后执行；失败必须阻止 Hub 继续启动。
	if err := migrateTaskScheduleModel(db); err != nil {
		return fmt.Errorf("migrate task/schedule model: %w", err)
	}
	if err := migrateConditionSplitModel(db); err != nil {
		return fmt.Errorf("migrate condition ownership: %w", err)
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

// schemaV10 Execution 记录来源调度与创建时间。created_at 自 v1 已存在，这里只回填空值；
// 历史记录无法反查来源调度，schedule_id 保持空串。
const schemaV10 = `
ALTER TABLE executions ADD COLUMN schedule_id TEXT NOT NULL DEFAULT '';
UPDATE executions SET created_at = COALESCE(NULLIF(start_time,''), NULLIF(scheduled_time,''), NULLIF(end_time,''), datetime('now')) WHERE created_at = '';
CREATE INDEX IF NOT EXISTS idx_executions_schedule_id ON executions(schedule_id) WHERE schedule_id != '';
`

// schemaV11 Execution 记录触发时的 Schedule Revision，用于追溯远程条件/策略版本。
const schemaV11 = `
ALTER TABLE executions ADD COLUMN schedule_revision INTEGER NOT NULL DEFAULT 0;
`

var _ = context.Background
