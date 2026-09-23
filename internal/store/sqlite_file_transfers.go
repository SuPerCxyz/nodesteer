package store

import (
	"context"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/google/uuid"
)

// CreateFileTransfer 原子创建传输任务和目标列表。
func (s *SQLiteStore) CreateFileTransfer(ctx context.Context, t *models.FileTransfer) error {
	if t.ID == "" {
		t.ID = uuid.NewString()
	}
	nowValue := now()
	t.CreatedAt = time.Now().UTC()
	t.UpdatedAt = t.CreatedAt
	t.Status = models.FileTransferPending
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `
		INSERT INTO file_transfers (id, source_node_id, source_path, source_mode, size, sha256, status, error, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		t.ID, t.SourceNodeID, t.SourcePath, t.SourceMode, t.Size, t.SHA256, t.Status, t.Error, nowValue, nowValue); err != nil {
		return err
	}
	for _, target := range t.Targets {
		if _, err := tx.ExecContext(ctx, `
			INSERT INTO file_transfer_targets (transfer_id, node_id, destination_path, mode, status, error, updated_at)
			VALUES (?, ?, ?, ?, ?, ?, ?)`, t.ID, target.NodeID, target.DestinationPath, target.Mode, models.FileTargetPending, "", nowValue); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *SQLiteStore) GetFileTransfer(ctx context.Context, id string) (*models.FileTransfer, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, source_node_id, source_path, source_mode, size, sha256, status, error, created_at, updated_at
		FROM file_transfers WHERE id = ?`, id)
	t := &models.FileTransfer{}
	var created, updated string
	if err := row.Scan(&t.ID, &t.SourceNodeID, &t.SourcePath, &t.SourceMode, &t.Size, &t.SHA256, &t.Status, &t.Error, &created, &updated); err != nil {
		return nil, err
	}
	t.CreatedAt, t.UpdatedAt = parseTime(created), parseTime(updated)
	t.Targets, _ = s.listFileTransferTargets(ctx, id)
	return t, nil
}

func (s *SQLiteStore) ListFileTransfers(ctx context.Context) ([]*models.FileTransfer, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, source_node_id, source_path, source_mode, size, sha256, status, error, created_at, updated_at
		FROM file_transfers ORDER BY created_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*models.FileTransfer
	for rows.Next() {
		t := &models.FileTransfer{}
		var created, updated string
		if err := rows.Scan(&t.ID, &t.SourceNodeID, &t.SourcePath, &t.SourceMode, &t.Size, &t.SHA256, &t.Status, &t.Error, &created, &updated); err != nil {
			return nil, err
		}
		t.CreatedAt, t.UpdatedAt = parseTime(created), parseTime(updated)
		t.Targets, _ = s.listFileTransferTargets(ctx, t.ID)
		out = append(out, t)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) listFileTransferTargets(ctx context.Context, id string) ([]*models.FileTransferTarget, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT transfer_id, node_id, destination_path, mode, status, error, updated_at
		FROM file_transfer_targets WHERE transfer_id = ? ORDER BY node_id`, id)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*models.FileTransferTarget
	for rows.Next() {
		t := &models.FileTransferTarget{}
		var updated string
		if err := rows.Scan(&t.TransferID, &t.NodeID, &t.DestinationPath, &t.Mode, &t.Status, &t.Error, &updated); err != nil {
			return nil, err
		}
		t.UpdatedAt = parseTime(updated)
		out = append(out, t)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) UpdateFileTransfer(ctx context.Context, t *models.FileTransfer) error {
	_, err := s.db.ExecContext(ctx, `
		UPDATE file_transfers SET source_mode=?, size=?, sha256=?, status=?, error=?, updated_at=? WHERE id=?`,
		t.SourceMode, t.Size, t.SHA256, t.Status, t.Error, now(), t.ID)
	return err
}

func (s *SQLiteStore) UpdateFileTransferTarget(ctx context.Context, t *models.FileTransferTarget) error {
	_, err := s.db.ExecContext(ctx, `
		UPDATE file_transfer_targets SET mode=?, status=?, error=?, updated_at=?
		WHERE transfer_id=? AND node_id=?`, t.Mode, t.Status, t.Error, now(), t.TransferID, t.NodeID)
	return err
}
