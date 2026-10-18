package store

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestReadHelpersInsideTxDoNotBorrowSecondConnection 事务内（txctx）调用定义/应用/传输
// 读方法不得再申请第二条连接：连接池压到 1 条连接时必须正常返回，否则永久阻塞。
// 该测试锁定 QA-20261018-F2 同类模式的防御性修复（同族读方法统一走 queryer(ctx)，
// 避免事务内用外层 ctx 读时「持写锁再借连接」的连接池嵌套）。
func TestReadHelpersInsideTxDoNotBorrowSecondConnection(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer st.Close()
	// 池压到 1：任何「事务内再借连接」都会死锁，从而暴露回归。
	st.DB().SetMaxOpenConns(1)
	st.DB().SetMaxIdleConns(1)

	ctx := t.Context()
	if err := st.CreateTask(ctx, &models.Task{ID: "tx-task", Name: "tx-task"}); err != nil {
		t.Fatalf("create task: %v", err)
	}
	if err := st.CreateSchedule(ctx, &models.Schedule{ID: "tx-schedule", TaskID: "tx-task"}); err != nil {
		t.Fatalf("create schedule: %v", err)
	}
	if err := st.CreateApplication(ctx, &models.Application{ID: "tx-app", Name: "tx-app"}); err != nil {
		t.Fatalf("create application: %v", err)
	}
	if err := st.SetApplicationAssignment(ctx, "tx-app", "tx-node", true); err != nil {
		t.Fatalf("assign application: %v", err)
	}
	if err := st.CreateFileTransfer(ctx, &models.FileTransfer{
		ID: "tx-transfer", SourceNodeID: "tx-node", SourcePath: "/var/tmp/x",
		Targets: []*models.FileTransferTarget{{NodeID: "tx-dst", DestinationPath: "/var/tmp/y"}},
	}); err != nil {
		t.Fatalf("create transfer: %v", err)
	}

	done := make(chan error, 1)
	go func() {
		done <- st.WithTx(context.Background(), func(txCtx context.Context) error {
			if _, err := st.GetTask(txCtx, "tx-task"); err != nil {
				return fmt.Errorf("GetTask: %w", err)
			}
			if _, err := st.GetSchedule(txCtx, "tx-schedule"); err != nil {
				return fmt.Errorf("GetSchedule: %w", err)
			}
			if _, err := st.GetApplication(txCtx, "tx-app"); err != nil {
				return fmt.Errorf("GetApplication: %w", err)
			}
			if _, err := st.ListApplications(txCtx); err != nil {
				return fmt.Errorf("ListApplications: %w", err)
			}
			if _, err := st.GetApplicationNodes(txCtx, "tx-app"); err != nil {
				return fmt.Errorf("GetApplicationNodes: %w", err)
			}
			transfer, err := st.GetFileTransfer(txCtx, "tx-transfer")
			if err != nil {
				return fmt.Errorf("GetFileTransfer: %w", err)
			}
			if len(transfer.Targets) != 1 {
				return fmt.Errorf("GetFileTransfer targets = %d, want 1", len(transfer.Targets))
			}
			if _, err := st.ListFileTransfers(txCtx); err != nil {
				return fmt.Errorf("ListFileTransfers: %w", err)
			}
			return nil
		})
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("read inside tx: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("read inside tx borrowed a second connection (pool=1 deadlock)")
	}
}
