package store

import (
	"context"
	"fmt"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func seedFileTransfer(t *testing.T, st *SQLiteStore, id string, targetNodes ...string) {
	t.Helper()
	tr := &models.FileTransfer{ID: id, SourceNodeID: "src-node", SourcePath: "/var/tmp/" + id}
	for _, nodeID := range targetNodes {
		tr.Targets = append(tr.Targets, &models.FileTransferTarget{
			NodeID: nodeID, DestinationPath: "/var/tmp/dst-" + nodeID,
		})
	}
	if err := st.CreateFileTransfer(t.Context(), tr); err != nil {
		t.Fatalf("create transfer %s: %v", id, err)
	}
}

// TestListFileTransfersReturnsTargets 单条 JOIN 查询必须完整返回传输与目标：
// 无目标传输不产生空目标，多目标传输按 node_id 升序，字段（mode/status/error）不丢失。
func TestListFileTransfersReturnsTargets(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer st.Close()
	ctx := t.Context()

	seedFileTransfer(t, st, "xfer-no-target")
	seedFileTransfer(t, st, "xfer-two-targets", "node-b", "node-a")
	if err := st.UpdateFileTransferTarget(ctx, &models.FileTransferTarget{
		TransferID: "xfer-two-targets", NodeID: "node-a",
		DestinationPath: "/var/tmp/dst-node-a", Mode: 0640,
		Status: models.FileTargetDelivering, Error: "in progress",
	}); err != nil {
		t.Fatalf("update target: %v", err)
	}

	items, err := st.ListFileTransfers(ctx)
	if err != nil {
		t.Fatalf("list transfers: %v", err)
	}
	if len(items) != 2 {
		t.Fatalf("transfers = %d, want 2 (%+v)", len(items), items)
	}
	byID := map[string]*models.FileTransfer{}
	for _, item := range items {
		byID[item.ID] = item
	}
	if got := byID["xfer-no-target"]; got == nil || len(got.Targets) != 0 {
		t.Fatalf("no-target transfer = %+v, want 0 targets", got)
	}
	two := byID["xfer-two-targets"]
	if two == nil || len(two.Targets) != 2 {
		t.Fatalf("two-target transfer = %+v, want 2 targets", two)
	}
	if two.Targets[0].NodeID != "node-a" || two.Targets[1].NodeID != "node-b" {
		t.Fatalf("target order = %s,%s, want node-a,node-b", two.Targets[0].NodeID, two.Targets[1].NodeID)
	}
	if two.Targets[0].Mode != 0640 || two.Targets[0].Status != models.FileTargetDelivering ||
		two.Targets[0].Error != "in progress" || two.Targets[0].DestinationPath != "/var/tmp/dst-node-a" {
		t.Fatalf("target fields lost: %+v", two.Targets[0])
	}
	if two.Targets[1].Status != models.FileTargetPending {
		t.Fatalf("untouched target status = %q, want PENDING", two.Targets[1].Status)
	}
}

// TestListFileTransfersConcurrentNoPoolDeadlock 并发 ListFileTransfers 不得耗尽连接池：
// 旧实现在 rows.Next() 循环内为每行再申请第 2 条连接，OpenSQLite 的
// SetMaxOpenConns(8) 下 ≥8 个并发调用者互相等待 → 永久挂起（缺陷 QA-20261018-F1）。
func TestListFileTransfersConcurrentNoPoolDeadlock(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer st.Close()

	// 31 条数据（与压测复现一致），每条 1 个目标。
	for i := 0; i < 31; i++ {
		seedFileTransfer(t, st, fmt.Sprintf("xfer-%02d", i), fmt.Sprintf("node-%02d", i))
	}

	const concurrency = 16
	var wg sync.WaitGroup
	start := make(chan struct{})
	errs := make(chan error, concurrency)
	for i := 0; i < concurrency; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			items, err := st.ListFileTransfers(context.Background())
			if err != nil {
				errs <- err
				return
			}
			if len(items) != 31 {
				errs <- fmt.Errorf("transfers = %d, want 31", len(items))
				return
			}
			for _, item := range items {
				if len(item.Targets) != 1 {
					errs <- fmt.Errorf("transfer %s targets = %d, want 1", item.ID, len(item.Targets))
					return
				}
			}
		}()
	}
	close(start)

	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(20 * time.Second):
		t.Fatal("concurrent ListFileTransfers hung (connection pool deadlock)")
	}
	close(errs)
	for err := range errs {
		t.Fatalf("concurrent list: %v", err)
	}
}
