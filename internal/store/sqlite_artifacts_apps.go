package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/google/uuid"
)

// ---------- Artifacts ----------

func (s *SQLiteStore) CreateArtifact(ctx context.Context, a *models.Artifact) error {
	if a.ID == "" {
		a.ID = uuid.NewString()
	}
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO artifacts (id, name, version, architecture, filename, size, sha256, storage_path, uploaded_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		a.ID, a.Name, a.Version, a.Architecture, a.Filename, a.Size, a.SHA256, a.StoragePath, now())
	return err
}

func (s *SQLiteStore) GetArtifact(ctx context.Context, id string) (*models.Artifact, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, name, version, architecture, filename, size, sha256, storage_path, uploaded_at
		FROM artifacts WHERE id = ?`, id)
	var a models.Artifact
	var uploaded string
	if err := row.Scan(&a.ID, &a.Name, &a.Version, &a.Architecture, &a.Filename, &a.Size,
		&a.SHA256, &a.StoragePath, &uploaded); err != nil {
		return nil, err
	}
	a.UploadedAt = parseTime(uploaded)
	return &a, nil
}

func (s *SQLiteStore) ListArtifacts(ctx context.Context) ([]*models.Artifact, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, name, version, architecture, filename, size, sha256, storage_path, uploaded_at
		FROM artifacts ORDER BY uploaded_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Artifact{}
	for rows.Next() {
		var a models.Artifact
		var uploaded string
		if err := rows.Scan(&a.ID, &a.Name, &a.Version, &a.Architecture, &a.Filename, &a.Size,
			&a.SHA256, &a.StoragePath, &uploaded); err != nil {
			return nil, err
		}
		a.UploadedAt = parseTime(uploaded)
		out = append(out, &a)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) DeleteArtifact(ctx context.Context, id string) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM artifacts WHERE id = ?`, id)
	return err
}

// ---------- Applications ----------

func (s *SQLiteStore) CreateApplication(ctx context.Context, a *models.Application) error {
	if a.ID == "" {
		a.ID = uuid.NewString()
	}
	if a.Revision == 0 {
		a.Revision = 1
	}
	def := marshalOrEmpty(a)
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
		INSERT INTO applications (id, name, definition_json, revision, updated_at, created_at)
		VALUES (?, ?, ?, ?, ?, ?)`, a.ID, a.Name, def, a.Revision, now(), now()); err != nil {
		return err
	}
	if _, err := execer.ExecContext(ctx, `
		INSERT INTO application_revisions (id, revision, definition_json, changed_at) VALUES (?, ?, ?, ?)`,
		a.ID, a.Revision, def, now()); err != nil {
		return err
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func (s *SQLiteStore) UpdateApplication(ctx context.Context, a *models.Application, prevRevision int64) error {
	def := marshalOrEmpty(a)
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
		UPDATE applications SET name=?, definition_json=?, revision=?, updated_at=? WHERE id=?`,
		a.Name, def, a.Revision, now(), a.ID); err != nil {
		return err
	}
	if _, err := execer.ExecContext(ctx, `
		INSERT INTO application_revisions (id, revision, definition_json, changed_at) VALUES (?, ?, ?, ?)`,
		a.ID, a.Revision, def, now()); err != nil {
		return err
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func scanAppFromJSON(def string) (*models.Application, error) {
	var a models.Application
	if err := json.Unmarshal([]byte(def), &a); err != nil {
		return nil, err
	}
	return &a, nil
}

func (s *SQLiteStore) GetApplication(ctx context.Context, id string) (*models.Application, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, name, definition_json, revision, updated_at, created_at FROM applications WHERE id = ?`, id)
	var aID, name, def, updated, created string
	var revision int64
	if err := row.Scan(&aID, &name, &def, &revision, &updated, &created); err != nil {
		return nil, err
	}
	a, err := scanAppFromJSON(def)
	if err != nil {
		return nil, err
	}
	a.ID = aID
	a.Revision = revision
	a.UpdatedAt = parseTime(updated)
	a.CreatedAt = parseTime(created)
	return a, nil
}

func (s *SQLiteStore) ListApplications(ctx context.Context) ([]*models.Application, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, name, definition_json, revision, updated_at, created_at FROM applications ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.Application{}
	for rows.Next() {
		var aID, name, def, updated, created string
		var revision int64
		if err := rows.Scan(&aID, &name, &def, &revision, &updated, &created); err != nil {
			return nil, err
		}
		a, err := scanAppFromJSON(def)
		if err != nil {
			return nil, err
		}
		a.ID = aID
		a.Revision = revision
		a.UpdatedAt = parseTime(updated)
		a.CreatedAt = parseTime(created)
		out = append(out, a)
	}
	return out, rows.Err()
}

// ListApplicationRevisions 应用历史版本
func (s *SQLiteStore) ListApplicationRevisions(ctx context.Context, id string) ([]*ApplicationRevisionEntry, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT revision, definition_json, changed_at FROM application_revisions WHERE id = ? ORDER BY revision DESC`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*ApplicationRevisionEntry{}
	for rows.Next() {
		var e ApplicationRevisionEntry
		var def, changed string
		if err := rows.Scan(&e.Revision, &def, &changed); err != nil {
			return nil, err
		}
		e.Content = def
		e.ChangedAt = parseTime(changed)
		out = append(out, &e)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) DeleteApplication(ctx context.Context, id string) error {
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
	if _, err := execer.ExecContext(ctx, `DELETE FROM application_assignments WHERE application_id = ?`, id); err != nil {
		return err
	}
	if _, err := execer.ExecContext(ctx, `DELETE FROM application_node_state WHERE application_id = ?`, id); err != nil {
		return err
	}
	if _, err := execer.ExecContext(ctx, `DELETE FROM applications WHERE id = ?`, id); err != nil {
		return err
	}
	if tx != nil {
		return tx.Commit()
	}
	return nil
}

func (s *SQLiteStore) SetApplicationAssignment(ctx context.Context, appID, nodeID string, assigned bool) error {
	if assigned {
		_, err := s.execer(ctx).ExecContext(ctx,
			`INSERT OR IGNORE INTO application_assignments (application_id, node_id) VALUES (?, ?)`, appID, nodeID)
		return err
	}
	_, err := s.execer(ctx).ExecContext(ctx,
		`DELETE FROM application_assignments WHERE application_id = ? AND node_id = ?`, appID, nodeID)
	return err
}

func (s *SQLiteStore) GetApplicationNodes(ctx context.Context, appID string) ([]string, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT node_id FROM application_assignments WHERE application_id = ?`, appID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, id)
	}
	return out, rows.Err()
}

// SetApplicationNodeState 保存应用在节点上的最近部署/健康状态。
func (s *SQLiteStore) SetApplicationNodeState(ctx context.Context, state *models.ApplicationNodeState) error {
	_, err := s.db.ExecContext(ctx, `
		INSERT INTO application_node_state (application_id, node_id, version, operation, health, error, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(application_id, node_id) DO UPDATE SET
			version=excluded.version, operation=excluded.operation, health=excluded.health,
			error=excluded.error, updated_at=excluded.updated_at`,
		state.ApplicationID, state.NodeID, state.Version, state.Operation, state.Health, state.Error, now())
	return err
}

func (s *SQLiteStore) DeleteApplicationNodeState(ctx context.Context, appID, nodeID string) error {
	_, err := s.execer(ctx).ExecContext(ctx,
		`DELETE FROM application_node_state WHERE application_id = ? AND node_id = ?`, appID, nodeID)
	return err
}

// ListApplicationNodeStates 查询应用的节点状态。
func (s *SQLiteStore) ListApplicationNodeStates(ctx context.Context, appID string) ([]*models.ApplicationNodeState, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT application_id, node_id, version, operation, health, error, updated_at
		FROM application_node_state WHERE application_id = ? ORDER BY node_id`, appID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.ApplicationNodeState{}
	for rows.Next() {
		state := &models.ApplicationNodeState{}
		var updated string
		if err := rows.Scan(&state.ApplicationID, &state.NodeID, &state.Version, &state.Operation,
			&state.Health, &state.Error, &updated); err != nil {
			return nil, err
		}
		state.UpdatedAt = parseTime(updated)
		out = append(out, state)
	}
	return out, rows.Err()
}

var _ = sql.ErrNoRows
var _ = time.Time{}
