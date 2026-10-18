package store

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

// TestWriteTxWaitsForBusyTimeout 核验 DSN 修复的两个要点：
//  1. _txlock=immediate：WithTx 在 BEGIN 即取写锁，读→写不再走 WAL 快照升级；
//  2. busy_timeout 生效：另一个连接持写锁时，写事务等待锁释放而不是立即
//     SQLITE_BUSY（修复前 deferred 升级会立刻失败，正是 enrollment 并发 500 的根因）。
func TestWriteTxWaitsForBusyTimeout(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer st.Close()
	ctx := t.Context()

	// 预置可读数据（B 的读不依赖 holder 的未提交写入，避免读语义干扰）。
	if err := st.SetSetting(ctx, "busy-seed", "0"); err != nil {
		t.Fatalf("seed setting: %v", err)
	}

	// 连接 A：显式持有写事务（immediate），模拟并发写持有者。
	holder, err := st.DB().BeginTx(ctx, nil)
	if err != nil {
		t.Fatalf("begin holder tx: %v", err)
	}
	if _, err := holder.ExecContext(ctx,
		`INSERT INTO settings (key, value) VALUES ('busy-holder', '1')
		 ON CONFLICT(key) DO UPDATE SET value=excluded.value`); err != nil {
		holder.Rollback()
		t.Fatalf("holder write: %v", err)
	}

	// 连接 B：先读后写的 mutation 事务，必须等待 holder 释放锁。
	type txResult struct {
		err     error
		elapsed time.Duration
	}
	done := make(chan txResult, 1)
	go func() {
		t0 := time.Now()
		err := st.WithTx(ctx, func(txCtx context.Context) error {
			// 先读（deferred 事务在此建立快照，写升级即触发 BUSY 竞态）
			if _, err := st.GetSetting(txCtx, "busy-seed"); err != nil {
				return err
			}
			return st.SetSetting(txCtx, "busy-writer", "2")
		})
		done <- txResult{err: err, elapsed: time.Since(t0)}
	}()

	time.Sleep(300 * time.Millisecond) // B 应已阻塞在锁上
	if err := holder.Commit(); err != nil {
		t.Fatalf("holder commit: %v", err)
	}

	select {
	case res := <-done:
		if res.err != nil {
			t.Fatalf("concurrent read→write tx must wait for busy_timeout, got %v", res.err)
		}
		if res.elapsed < 200*time.Millisecond {
			t.Fatalf("write tx returned in %v; it must have waited for the holder lock", res.elapsed)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("concurrent write tx did not complete")
	}
	if v, err := st.GetSetting(ctx, "busy-writer"); err != nil || v != "2" {
		t.Fatalf("waited write not persisted: %q err=%v", v, err)
	}
}
