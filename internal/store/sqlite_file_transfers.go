package store

import (
	"context"
	"database/sql"
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
	row := s.queryer(ctx).QueryRowContext(ctx, `
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

// ListFileTransfers 列出全部传输及其目标。
//
// 必须用单条 JOIN 一次取回：此前实现是在 rows.Next() 循环内对每行调用
// listFileTransferTargets，等于在持有 1 条池连接（未关闭的 rows）时再申请第 2 条连接。
// OpenSQLite 设置 SetMaxOpenConns(8)，≥8 个并发调用者各持 1 条连接等第 2 条 →
// 连接池死锁、请求永久挂起并拖垮全库端点（缺陷 QA-20261018-F1；NodeManager.Delete
// 也调用本方法，因此 DELETE /api/nodes 并发时同样挂起）。
func (s *SQLiteStore) ListFileTransfers(ctx context.Context) ([]*models.FileTransfer, error) {
	rows, err := s.queryer(ctx).QueryContext(ctx, `
		SELECT t.id, t.source_node_id, t.source_path, t.source_mode, t.size, t.sha256, t.status, t.error,
		       t.created_at, t.updated_at,
		       tt.transfer_id, tt.node_id, tt.destination_path, tt.mode, tt.status, tt.error, tt.updated_at
		FROM file_transfers t
		LEFT JOIN file_transfer_targets tt ON tt.transfer_id = t.id
		ORDER BY t.created_at DESC, t.id, tt.node_id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []*models.FileTransfer{}
	byID := map[string]*models.FileTransfer{}
	for rows.Next() {
		t := &models.FileTransfer{}
		var created, updated string
		var targetID, nodeID, destinationPath, targetStatus, targetError, targetUpdated sql.NullString
		var targetMode sql.NullInt64
		if err := rows.Scan(&t.ID, &t.SourceNodeID, &t.SourcePath, &t.SourceMode, &t.Size, &t.SHA256, &t.Status, &t.Error,
			&created, &updated,
			&targetID, &nodeID, &destinationPath, &targetMode, &targetStatus, &targetError, &targetUpdated); err != nil {
			return nil, err
		}
		t.CreatedAt, t.UpdatedAt = parseTime(created), parseTime(updated)
		if existing, ok := byID[t.ID]; ok {
			t = existing
		} else {
			byID[t.ID] = t
			out = append(out, t)
		}
		// 无目标的传输由 LEFT JOIN 产生一行全 NULL 目标列，不构造空目标。
		if !nodeID.Valid {
			continue
		}
		t.Targets = append(t.Targets, &models.FileTransferTarget{
			TransferID:      targetID.String,
			NodeID:          nodeID.String,
			DestinationPath: destinationPath.String,
			Mode:            uint32(targetMode.Int64),
			Status:          targetStatus.String,
			Error:           targetError.String,
			UpdatedAt:       parseTime(targetUpdated.String),
		})
	}
	return out, rows.Err()
}

func (s *SQLiteStore) listFileTransferTargets(ctx context.Context, id string) ([]*models.FileTransferTarget, error) {
	rows, err := s.queryer(ctx).QueryContext(ctx, `
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
