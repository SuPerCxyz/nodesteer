package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/google/uuid"
)

// ---------- Users ----------

func (s *SQLiteStore) CreateUser(ctx context.Context, u *models.User) error {
	if u.ID == "" {
		u.ID = uuid.NewString()
	}
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)`,
		u.ID, u.Username, u.PasswordHash, u.Role, now())
	return err
}

func (s *SQLiteStore) GetUserByUsername(ctx context.Context, username string) (*models.User, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?`, username)
	var u models.User
	var created string
	if err := row.Scan(&u.ID, &u.Username, &u.PasswordHash, &u.Role, &created); err != nil {
		return nil, err
	}
	u.CreatedAt = parseTime(created)
	return &u, nil
}

func (s *SQLiteStore) ListUsers(ctx context.Context) ([]*models.User, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, username, role, created_at FROM users ORDER BY created_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.User{}
	for rows.Next() {
		var u models.User
		var created string
		if err := rows.Scan(&u.ID, &u.Username, &u.Role, &created); err != nil {
			return nil, err
		}
		u.CreatedAt = parseTime(created)
		out = append(out, &u)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) UpdateUserRole(ctx context.Context, id, role string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE users SET role = ? WHERE id = ?`, role, id)
	return err
}

func (s *SQLiteStore) UpdateUserPassword(ctx context.Context, id, passwordHash string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE users SET password_hash = ? WHERE id = ?`, passwordHash, id)
	return err
}

// ---------- Nodes ----------

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
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO nodes (id, agent_id, hostname, ip, os, arch, agent_version, deployment_mode,
			host_integration, status, global_revision, sync_status, last_seen, first_seen,
			inventory_json, capabilities_json, labels_json)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			hostname=excluded.hostname, ip=excluded.ip, os=excluded.os, arch=excluded.arch,
			agent_version=excluded.agent_version, deployment_mode=excluded.deployment_mode,
			host_integration=excluded.host_integration, inventory_json=excluded.inventory_json,
			capabilities_json=excluded.capabilities_json, labels_json=excluded.labels_json`,
		n.ID, n.AgentID, n.Hostname, n.IP, n.OS, n.Arch, n.AgentVersion, n.DeploymentMode,
		boolToInt(n.HostIntegration), n.Status, n.GlobalRevision, n.SyncStatus,
		timeToDB(n.LastSeen), timeToDB(n.FirstSeen),
		string(inv), string(caps), string(labels))
	return err
}

func (s *SQLiteStore) GetNode(ctx context.Context, id string) (*models.Node, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, agent_id, hostname, ip, os, arch, agent_version, deployment_mode,
			host_integration, status, global_revision, sync_status, last_seen, first_seen,
			inventory_json, capabilities_json, labels_json FROM nodes WHERE id = ?`, id)
	return scanNode(row)
}

func (s *SQLiteStore) GetNodeByAgentID(ctx context.Context, agentID string) (*models.Node, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, agent_id, hostname, ip, os, arch, agent_version, deployment_mode,
			host_integration, status, global_revision, sync_status, last_seen, first_seen,
			inventory_json, capabilities_json, labels_json FROM nodes WHERE agent_id = ?`, agentID)
	return scanNode(row)
}

func scanNode(row *sql.Row) (*models.Node, error) {
	var n models.Node
	var hostInt int
	var lastSeen, firstSeen sql.NullString
	var invJSON, capsJSON, labelsJSON string
	if err := row.Scan(&n.ID, &n.AgentID, &n.Hostname, &n.IP, &n.OS, &n.Arch, &n.AgentVersion,
		&n.DeploymentMode, &hostInt, &n.Status, &n.GlobalRevision, &n.SyncStatus,
		&lastSeen, &firstSeen, &invJSON, &capsJSON, &labelsJSON); err != nil {
		return nil, err
	}
	n.HostIntegration = hostInt == 1
	n.LastSeen = parseTime(lastSeen.String)
	n.FirstSeen = parseTime(firstSeen.String)
	json.Unmarshal([]byte(invJSON), &n.Inventory)
	json.Unmarshal([]byte(capsJSON), &n.Capabilities)
	json.Unmarshal([]byte(labelsJSON), &n.Labels)
	return &n, nil
}

func (s *SQLiteStore) ListNodes(ctx context.Context) ([]*models.Node, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, agent_id, hostname, ip, os, arch, agent_version, deployment_mode,
			host_integration, status, global_revision, sync_status, last_seen, first_seen,
			inventory_json, capabilities_json, labels_json FROM nodes ORDER BY hostname`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Node{}
	for rows.Next() {
		var n models.Node
		var hostInt int
		var lastSeen, firstSeen sql.NullString
		var invJSON, capsJSON, labelsJSON string
		if err := rows.Scan(&n.ID, &n.AgentID, &n.Hostname, &n.IP, &n.OS, &n.Arch, &n.AgentVersion,
			&n.DeploymentMode, &hostInt, &n.Status, &n.GlobalRevision, &n.SyncStatus,
			&lastSeen, &firstSeen, &invJSON, &capsJSON, &labelsJSON); err != nil {
			return nil, err
		}
		n.HostIntegration = hostInt == 1
		n.LastSeen = parseTime(lastSeen.String)
		n.FirstSeen = parseTime(firstSeen.String)
		json.Unmarshal([]byte(invJSON), &n.Inventory)
		json.Unmarshal([]byte(capsJSON), &n.Capabilities)
		json.Unmarshal([]byte(labelsJSON), &n.Labels)
		out = append(out, &n)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) UpdateNodeStatus(ctx context.Context, id, status string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE nodes SET status = ? WHERE id = ?`, status, id)
	return err
}

func (s *SQLiteStore) UpdateNodeSyncState(ctx context.Context, id string, rev int64, syncStatus string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE nodes SET global_revision = ?, sync_status = ? WHERE id = ?`, rev, syncStatus, id)
	return err
}

func (s *SQLiteStore) UpdateNodeHeartbeat(ctx context.Context, id string, lastSeen time.Time) error {
	// 仅当当前状态为 online 时更新为 online；maintenance/disabled 不被心跳覆盖
	_, err := s.db.ExecContext(ctx, `
		UPDATE nodes SET last_seen = ?,
			status = CASE WHEN status IN ('maintenance', 'disabled') THEN status ELSE ? END
		WHERE id = ?`,
		lastSeen.Format(time.RFC3339Nano), models.NodeStatusOnline, id)
	return err
}

func (s *SQLiteStore) SetNodeLabels(ctx context.Context, id string, labels map[string]string) error {
	b, _ := json.Marshal(labels)
	_, err := s.execer(ctx).ExecContext(ctx, `UPDATE nodes SET labels_json = ? WHERE id = ?`, string(b), id)
	return err
}

// DeleteNode 删除节点本身及其可安全清理的运行态关联；执行历史保留用于审计。
func (s *SQLiteStore) DeleteNode(ctx context.Context, id string) error {
	execer := s.execer(ctx)
	for _, query := range []string{
		`DELETE FROM node_group_members WHERE node_id = ?`,
		`DELETE FROM application_assignments WHERE node_id = ?`,
		`DELETE FROM application_node_state WHERE node_id = ?`,
		`DELETE FROM agent_sync_state WHERE node_id = ?`,
		`DELETE FROM remote_state WHERE node_id = ?`,
		`DELETE FROM nodes WHERE id = ?`,
	} {
		if _, err := execer.ExecContext(ctx, query, id); err != nil {
			return err
		}
	}
	return nil
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
	row := s.db.QueryRowContext(ctx,
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
	rows, err := s.db.QueryContext(ctx,
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
	if _, err := execer.ExecContext(ctx, `
		UPDATE node_groups SET name=?, description=?, type=?, label_key=?, label_value=? WHERE id=?`,
		g.Name, g.Description, g.Type, g.LabelKey, g.LabelValue, g.ID); err != nil {
		return err
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
	if _, err := execer.ExecContext(ctx, `DELETE FROM node_groups WHERE id = ?`, id); err != nil {
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
	rows, err := s.db.QueryContext(ctx,
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
