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

// ---------- Users ----------

func (s *SQLiteStore) CreateUser(ctx context.Context, u *models.User) error {
	if u.ID == "" {
		u.ID = uuid.NewString()
	}
	_, err := s.execer(ctx).ExecContext(ctx,
		`INSERT INTO users (id, username, password_hash, avatar_url, role, created_at) VALUES (?, ?, ?, ?, ?, ?)`,
		u.ID, u.Username, u.PasswordHash, u.AvatarURL, u.Role, now())
	return err
}

func (s *SQLiteStore) GetUserByID(ctx context.Context, id string) (*models.User, error) {
	row := s.queryer(ctx).QueryRowContext(ctx,
		`SELECT id, username, avatar_url, role, created_at FROM users WHERE id = ?`, id)
	var u models.User
	var created string
	if err := row.Scan(&u.ID, &u.Username, &u.AvatarURL, &u.Role, &created); err != nil {
		return nil, err
	}
	u.CreatedAt = parseTime(created)
	return &u, nil
}

func (s *SQLiteStore) GetUserByUsername(ctx context.Context, username string) (*models.User, error) {
	row := s.queryer(ctx).QueryRowContext(ctx,
		`SELECT id, username, password_hash, avatar_url, role, created_at FROM users WHERE username = ?`, username)
	var u models.User
	var created string
	if err := row.Scan(&u.ID, &u.Username, &u.PasswordHash, &u.AvatarURL, &u.Role, &created); err != nil {
		return nil, err
	}
	u.CreatedAt = parseTime(created)
	return &u, nil
}

func (s *SQLiteStore) ListUsers(ctx context.Context) ([]*models.User, error) {
	rows, err := s.queryer(ctx).QueryContext(ctx, `SELECT id, username, avatar_url, role, created_at FROM users ORDER BY created_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.User{}
	for rows.Next() {
		var u models.User
		var created string
		if err := rows.Scan(&u.ID, &u.Username, &u.AvatarURL, &u.Role, &created); err != nil {
			return nil, err
		}
		u.CreatedAt = parseTime(created)
		out = append(out, &u)
	}
	return out, rows.Err()
}

// execExpectRow 执行必须命中至少一行的 UPDATE/DELETE：缺行返回 sql.ErrNoRows，
// 避免「更新不存在的资源仍返回成功」的静默成功（缺陷 QA-20261018-F5：
// PUT /api/users/{id} 对不存在用户返回 200）。
func execExpectRow(ctx context.Context, ex txExecer, query string, args ...any) error {
	res, err := ex.ExecContext(ctx, query, args...)
	if err != nil {
		return err
	}
	affected, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if affected == 0 {
		return sql.ErrNoRows
	}
	return nil
}

func (s *SQLiteStore) UpdateUserRole(ctx context.Context, id, role string) error {
	return execExpectRow(ctx, s.execer(ctx), `UPDATE users SET role = ? WHERE id = ?`, role, id)
}

func (s *SQLiteStore) UpdateUserAvatar(ctx context.Context, id, avatarURL string) error {
	_, err := s.execer(ctx).ExecContext(ctx, `UPDATE users SET avatar_url = ? WHERE id = ?`, avatarURL, id)
	return err
}

func (s *SQLiteStore) UpdateUserPassword(ctx context.Context, id, passwordHash string) error {
	return execExpectRow(ctx, s.execer(ctx), `UPDATE users SET password_hash = ? WHERE id = ?`, passwordHash, id)
}

// ---------- Nodes ----------

// nodeColumns 节点表全部读取列。顺序必须与 scanNode 的 Scan 参数一致。
const nodeColumns = `id, agent_id, hostname, display_name, ip, os, arch, agent_version, deployment_mode,
	host_integration, status, global_revision, sync_status, last_seen, first_seen,
	inventory_json, capabilities_json, labels_json,
	endpoint, connection_mode, inbound_provisioned, inbound_cert_fingerprint, inbound_cert_serial,
	inbound_cert_not_after, inbound_token_enc, inbound_last_handshake, inbound_last_error,
	endpoint_updated_at, endpoint_updated_by`

func (s *SQLiteStore) UpsertNode(ctx context.Context, n *models.Node) error {
	labels, _ := json.Marshal(n.Labels)
	if labels == nil {
		labels = []byte("{}")
	}
	caps, _ := json.Marshal(n.Capabilities)
	if caps == nil {
		caps = []byte("{}")
	}
	inv, _ := json.Marshal(n.Inventory)
	if inv == nil {
		inv = []byte("{}")
	}
	// endpoint 仅在首次 INSERT 写入；ON CONFLICT 不更新任何入站配置/诊断列，
	// 避免 Agent 注册、HELLO 元数据刷新或重新纳管覆盖管理员配置。
	_, err := s.execer(ctx).ExecContext(ctx, `
		INSERT INTO nodes (id, agent_id, hostname, display_name, ip, os, arch, agent_version, deployment_mode,
			host_integration, status, global_revision, sync_status, last_seen, first_seen,
			inventory_json, capabilities_json, labels_json, endpoint)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			hostname=excluded.hostname, ip=excluded.ip, os=excluded.os, arch=excluded.arch,
			agent_version=excluded.agent_version, deployment_mode=excluded.deployment_mode,
			host_integration=excluded.host_integration, inventory_json=excluded.inventory_json,
			capabilities_json=excluded.capabilities_json, labels_json=excluded.labels_json`,
		n.ID, n.AgentID, n.Hostname, n.DisplayName, n.IP, n.OS, n.Arch, n.AgentVersion, n.DeploymentMode,
		boolToInt(n.HostIntegration), n.Status, n.GlobalRevision, n.SyncStatus,
		timeToDB(n.LastSeen), timeToDB(n.FirstSeen),
		string(inv), string(caps), string(labels), n.Endpoint)
	return err
}

func (s *SQLiteStore) GetNode(ctx context.Context, id string) (*models.Node, error) {
	row := s.queryer(ctx).QueryRowContext(ctx,
		`SELECT `+nodeColumns+` FROM nodes WHERE id = ?`, id)
	return scanNode(row)
}

func (s *SQLiteStore) GetNodeByAgentID(ctx context.Context, agentID string) (*models.Node, error) {
	row := s.queryer(ctx).QueryRowContext(ctx,
		`SELECT `+nodeColumns+` FROM nodes WHERE agent_id = ?`, agentID)
	return scanNode(row)
}

// rowScanner 同时兼容 *sql.Row 与 *sql.Rows。
type rowScanner interface {
	Scan(dest ...any) error
}

func scanNode(row rowScanner) (*models.Node, error) {
	var n models.Node
	var hostInt, inboundProvisioned int
	var lastSeen, firstSeen sql.NullString
	var invJSON, capsJSON, labelsJSON string
	var endpointUpdatedAt, endpointUpdatedBy sql.NullString
	if err := row.Scan(&n.ID, &n.AgentID, &n.Hostname, &n.DisplayName, &n.IP, &n.OS, &n.Arch, &n.AgentVersion,
		&n.DeploymentMode, &hostInt, &n.Status, &n.GlobalRevision, &n.SyncStatus,
		&lastSeen, &firstSeen, &invJSON, &capsJSON, &labelsJSON,
		&n.Endpoint, &n.LastConnectionMode, &inboundProvisioned, &n.InboundCertFingerprint,
		&n.InboundCertSerial, &n.InboundCertNotAfter, &n.InboundTokenEnc,
		&n.InboundLastHandshake, &n.InboundLastError, &endpointUpdatedAt, &endpointUpdatedBy); err != nil {
		return nil, err
	}
	n.HostIntegration = hostInt == 1
	n.InboundProvisioned = inboundProvisioned == 1
	n.EndpointUpdatedAt = endpointUpdatedAt.String
	n.EndpointUpdatedBy = endpointUpdatedBy.String
	n.LastSeen = parseTime(lastSeen.String)
	n.FirstSeen = parseTime(firstSeen.String)
	json.Unmarshal([]byte(invJSON), &n.Inventory)
	json.Unmarshal([]byte(capsJSON), &n.Capabilities)
	json.Unmarshal([]byte(labelsJSON), &n.Labels)
	n.SyncInboundView()
	return &n, nil
}

func (s *SQLiteStore) ListNodes(ctx context.Context) ([]*models.Node, error) {
	rows, err := s.queryer(ctx).QueryContext(ctx,
		`SELECT `+nodeColumns+` FROM nodes ORDER BY hostname`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Node{}
	for rows.Next() {
		n, err := scanNode(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, n)
	}
	return out, rows.Err()
}

// ListInboundCandidateNodes 返回可尝试入站拨号的节点（未考虑内存会话/宽限期）：
// endpoint 非空、bundle 已签发且不是 disabled。首次引导允许节点尚无
// inbound_connection 能力。
func (s *SQLiteStore) ListInboundCandidateNodes(ctx context.Context) ([]*models.Node, error) {
	rows, err := s.queryer(ctx).QueryContext(ctx, `SELECT `+nodeColumns+`
		FROM nodes
		WHERE endpoint != '' AND inbound_provisioned = 1 AND status != ?
		ORDER BY id`, models.NodeStatusDisabled)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Node{}
	for rows.Next() {
		n, err := scanNode(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, n)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) UpdateNodeStatus(ctx context.Context, id, status string) error {
	return execExpectRow(ctx, s.execer(ctx), `UPDATE nodes SET status = ? WHERE id = ?`, status, id)
}

func (s *SQLiteStore) UpdateNodeSyncState(ctx context.Context, id string, rev int64, syncStatus string) error {
	return execExpectRow(ctx, s.execer(ctx), `UPDATE nodes SET global_revision = ?, sync_status = ? WHERE id = ?`, rev, syncStatus, id)
}

func (s *SQLiteStore) UpdateNodeHeartbeat(ctx context.Context, id string, lastSeen time.Time) error {
	// 仅当当前状态为 online 时更新为 online；maintenance/disabled 不被心跳覆盖。
	// 零行命中（节点已被删除）返回 sql.ErrNoRows：Gateway 据此回收孤儿会话，
	// 不再对不存在节点静默成功（同类缺陷审计，F5 同族）。
	return execExpectRow(ctx, s.execer(ctx), `
		UPDATE nodes SET last_seen = ?,
			status = CASE WHEN status IN ('maintenance', 'disabled') THEN status ELSE ? END
		WHERE id = ?`,
		lastSeen.Format(time.RFC3339Nano), models.NodeStatusOnline, id)
}

func (s *SQLiteStore) SetNodeLabels(ctx context.Context, id string, labels map[string]string) error {
	b, _ := json.Marshal(labels)
	return execExpectRow(ctx, s.execer(ctx), `UPDATE nodes SET labels_json = ? WHERE id = ?`, string(b), id)
}

// UpdateNodeDisplayName 更新节点显示名；不存在返回 sql.ErrNoRows。
// 该字段独立于 hostname，Agent 注册路径不得调用此方法。
func (s *SQLiteStore) UpdateNodeDisplayName(ctx context.Context, id, value string) error {
	return execExpectRow(ctx, s.execer(ctx), `UPDATE nodes SET display_name = ? WHERE id = ?`, value, id)
}

// UpdateNodeEndpoint 更新管理员配置的拨号地址，并暂停既有入站身份：
// inbound_provisioned 置 0，重新签发 bundle 前拨号器不得使用新 endpoint。
// 证书指纹/令牌密文保留用于展示与 stale 判定。
func (s *SQLiteStore) UpdateNodeEndpoint(ctx context.Context, id, endpoint, updatedBy string, updatedAt time.Time) error {
	return execExpectRow(ctx, s.execer(ctx), `
		UPDATE nodes SET endpoint = ?, endpoint_updated_at = ?, endpoint_updated_by = ?,
			inbound_provisioned = 0
		WHERE id = ?`,
		endpoint, timeToDB(updatedAt), updatedBy, id)
}

// UpdateNodeInboundIdentity 保存 bundle 签发后的证书/令牌身份（不含私钥）。
func (s *SQLiteStore) UpdateNodeInboundIdentity(ctx context.Context, id string, provisioned bool,
	fingerprint, serial string, notAfter time.Time, tokenEnc string) error {
	return execExpectRow(ctx, s.execer(ctx), `
		UPDATE nodes SET inbound_provisioned = ?, inbound_cert_fingerprint = ?,
			inbound_cert_serial = ?, inbound_cert_not_after = ?, inbound_token_enc = ?
		WHERE id = ?`,
		boolToInt(provisioned), fingerprint, serial, timeToDB(notAfter), tokenEnc, id)
}

// UpdateNodeInboundDiagnostics 保存最近一次入站握手时间与结构化错误（不含秘密）。
func (s *SQLiteStore) UpdateNodeInboundDiagnostics(ctx context.Context, id string, lastHandshake time.Time, lastError string) error {
	return execExpectRow(ctx, s.execer(ctx), `
		UPDATE nodes SET inbound_last_handshake = ?, inbound_last_error = ?
		WHERE id = ?`,
		timeToDB(lastHandshake), lastError, id)
}

// UpdateNodeLastConnectionMode 保存最近一次成功会话方向（outbound/inbound）。
func (s *SQLiteStore) UpdateNodeLastConnectionMode(ctx context.Context, id, mode string) error {
	return execExpectRow(ctx, s.execer(ctx), `UPDATE nodes SET connection_mode = ? WHERE id = ?`, mode, id)
}

// DeleteNode 删除节点本身及其可安全清理的运行态关联；执行历史保留用于审计。
// 节点行零行命中 → sql.ErrNoRows（不存在资源不得静默成功，F5 同族）。
func (s *SQLiteStore) DeleteNode(ctx context.Context, id string) error {
	execer := s.execer(ctx)
	for _, query := range []string{
		`DELETE FROM node_group_members WHERE node_id = ?`,
		`DELETE FROM application_assignments WHERE node_id = ?`,
		`DELETE FROM application_node_state WHERE node_id = ?`,
		`DELETE FROM agent_sync_state WHERE node_id = ?`,
		`DELETE FROM remote_state WHERE node_id = ?`,
		// 原生指标随节点删除清理，避免孤儿样本被后续批量查询/聚合继续扫描。
		`DELETE FROM node_metric_samples WHERE node_id = ?`,
		`DELETE FROM node_metric_rollups WHERE node_id = ?`,
	} {
		if _, err := execer.ExecContext(ctx, query, id); err != nil {
			return err
		}
	}
	return execExpectRow(ctx, execer, `DELETE FROM nodes WHERE id = ?`, id)
}

// ---------- Groups ----------

func (s *SQLiteStore) CreateGroup(ctx context.Context, g *models.Group) error {
	if g.ID == "" {
		g.ID = uuid.NewString()
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
		INSERT INTO node_groups (id, name, description, type, label_key, label_value, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)`,
		g.ID, g.Name, g.Description, g.Type, g.LabelKey, g.LabelValue, now()); err != nil {
		return err
	}
	for _, m := range g.Members {
		if _, err := execer.ExecContext(ctx,
			`INSERT INTO node_group_members (group_id, node_id) VALUES (?, ?)`, g.ID, m); err != nil {
			return err
		}
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func (s *SQLiteStore) GetGroup(ctx context.Context, id string) (*models.Group, error) {
	row := s.queryer(ctx).QueryRowContext(ctx,
		`SELECT id, name, description, type, label_key, label_value, created_at FROM node_groups WHERE id = ?`, id)
	var g models.Group
	var created string
	if err := row.Scan(&g.ID, &g.Name, &g.Description, &g.Type, &g.LabelKey, &g.LabelValue, &created); err != nil {
		return nil, err
	}
	g.CreatedAt = parseTime(created)
	members, err := s.groupMemberIDsForDisplay(ctx, &g)
	if err != nil {
		return nil, err
	}
	g.Members = members
	return &g, nil
}

func (s *SQLiteStore) ListGroups(ctx context.Context) ([]*models.Group, error) {
	rows, err := s.queryer(ctx).QueryContext(ctx,
		`SELECT id, name, description, type, label_key, label_value, created_at FROM node_groups ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Group{}
	for rows.Next() {
		var g models.Group
		var created string
		if err := rows.Scan(&g.ID, &g.Name, &g.Description, &g.Type, &g.LabelKey, &g.LabelValue, &created); err != nil {
			return nil, err
		}
		g.CreatedAt = parseTime(created)
		out = append(out, &g)
	}
	for _, g := range out {
		members, err := s.groupMemberIDsForDisplay(ctx, g)
		if err != nil {
			return nil, err
		}
		g.Members = members
	}
	return out, rows.Err()
}

func (s *SQLiteStore) groupMemberIDsForDisplay(ctx context.Context, g *models.Group) ([]string, error) {
	if g.Type != "label" {
		return s.GroupMemberIDs(ctx, g.ID)
	}
	nodes, err := s.ListNodes(ctx)
	if err != nil {
		return nil, err
	}
	var members []string
	for _, node := range nodes {
		if value, ok := node.Labels[g.LabelKey]; ok && (g.LabelValue == "" || value == g.LabelValue) {
			members = append(members, node.ID)
		}
	}
	return members, nil
}

func (s *SQLiteStore) UpdateGroup(ctx context.Context, g *models.Group) error {
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
	res, err := execer.ExecContext(ctx, `
		UPDATE node_groups SET name=?, description=?, type=?, label_key=?, label_value=? WHERE id=?`,
		g.Name, g.Description, g.Type, g.LabelKey, g.LabelValue, g.ID)
	if err != nil {
		return err
	}
	affected, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if affected == 0 {
		// 不存在的组：不得继续写成员（产生孤儿行）或让调用方推进无效 revision。
		return sql.ErrNoRows
	}
	if _, err := execer.ExecContext(ctx, `DELETE FROM node_group_members WHERE group_id = ?`, g.ID); err != nil {
		return err
	}
	for _, m := range g.Members {
		if _, err := execer.ExecContext(ctx,
			`INSERT INTO node_group_members (group_id, node_id) VALUES (?, ?)`, g.ID, m); err != nil {
			return err
		}
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func (s *SQLiteStore) DeleteGroup(ctx context.Context, id string) error {
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
	if _, err := execer.ExecContext(ctx, `DELETE FROM node_group_members WHERE group_id = ?`, id); err != nil {
		return err
	}
	// 组行零行命中 → sql.ErrNoRows（不存在资源不得静默成功，F5 同族）。
	if err := execExpectRow(ctx, execer, `DELETE FROM node_groups WHERE id = ?`, id); err != nil {
		return err
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func (s *SQLiteStore) AddGroupMember(ctx context.Context, groupID, nodeID string) error {
	_, err := s.execer(ctx).ExecContext(ctx,
		`INSERT OR IGNORE INTO node_group_members (group_id, node_id) VALUES (?, ?)`, groupID, nodeID)
	return err
}

func (s *SQLiteStore) RemoveGroupMember(ctx context.Context, groupID, nodeID string) error {
	_, err := s.execer(ctx).ExecContext(ctx,
		`DELETE FROM node_group_members WHERE group_id = ? AND node_id = ?`, groupID, nodeID)
	return err
}

func (s *SQLiteStore) GroupMemberIDs(ctx context.Context, groupID string) ([]string, error) {
	rows, err := s.queryer(ctx).QueryContext(ctx,
		`SELECT node_id FROM node_group_members WHERE group_id = ?`, groupID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []string{}
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

func marshalOrEmpty(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	return string(b)
}

var _ = fmt.Sprintf
