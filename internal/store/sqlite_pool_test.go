package store

import (
	"context"
	"path/filepath"
	"testing"
	"time"
)

func TestSQLiteReadWritePoolsSeparated(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	if st.readDB == nil || st.readDB == st.db {
		t.Fatal("read pool must be a separate sql.DB")
	}
	if got := st.db.Stats().MaxOpenConnections; got != 2 {
		t.Fatalf("write pool max = %d, want 2", got)
	}
	if got := st.readDB.Stats().MaxOpenConnections; got != 8 {
		t.Fatalf("read pool max = %d, want 8", got)
	}
	var queryOnly int
	if err := st.readDB.QueryRow(`PRAGMA query_only`).Scan(&queryOnly); err != nil {
		t.Fatal(err)
	}
	if queryOnly != 1 {
		t.Fatalf("read pool query_only = %d, want 1", queryOnly)
	}
	if _, err := st.readDB.Exec(`INSERT INTO settings (key, value) VALUES ('read-only', '1')`); err == nil {
		t.Fatal("read pool accepted a write")
	}
}

func TestSQLiteReadPoolNotBlockedByWriter(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	if err := st.SetSetting(ctx, "seed", "ok"); err != nil {
		t.Fatal(err)
	}
	tx, err := st.db.BeginTx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, `UPDATE settings SET value = 'locked' WHERE key = 'seed'`); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		value, err := st.GetSetting(ctx, "seed")
		if err == nil && value != "ok" {
			err = &poolValueError{value: value}
		}
		done <- err
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("read blocked or wrong value: %v", err)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("read blocked by writer lock")
	}
}

type poolValueError struct{ value string }

func (e *poolValueError) Error() string { return "unexpected value " + e.value }
