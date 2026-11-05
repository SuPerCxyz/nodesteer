package store

import (
	"context"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func TestMonitoringExecutionLinkRejectsMissingExecution(t *testing.T) {
	st, err := OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	if _, err := st.DB().Exec(`INSERT INTO monitoring_rule_events (id, rule_id, target_id, observed_at, status) VALUES ('event', 'rule', 'target', '2026-01-01T00:00:00Z', 'pending')`); err != nil {
		t.Fatal(err)
	}
	err = st.WithTx(ctx, func(txCtx context.Context) error {
		return st.LinkMonitoringExecution(txCtx, "event", "missing")
	})
	if err == nil {
		t.Fatal("missing execution unexpectedly linked")
	}
	var linked string
	if err := st.DB().QueryRow(`SELECT execution_ids FROM monitoring_rule_events WHERE id='event'`).Scan(&linked); err != nil || linked != `[]` {
		t.Fatalf("linked=%s err=%v", linked, err)
	}
}

func TestMonitoringExecutionLinkIsAtomic(t *testing.T) {
	st, err := OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	execution := &models.Execution{ID: "exec-1", TaskID: "task", NodeID: "node", Status: models.ExecStatusPending}
	err = st.WithTx(ctx, func(txCtx context.Context) error {
		if err := st.CreateExecution(txCtx, execution); err != nil {
			return err
		}
		return st.LinkMonitoringExecution(txCtx, "missing", execution.ID)
	})
	if err == nil {
		t.Fatal("missing event unexpectedly linked")
	}
	var count int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM executions WHERE id = 'exec-1'`).Scan(&count); err != nil || count != 0 {
		t.Fatalf("execution after rollback = %d, err = %v", count, err)
	}
	if _, err := st.DB().Exec(`INSERT INTO monitoring_rule_events (id, rule_id, target_id, observed_at, status) VALUES ('event', 'rule', 'target', '2026-01-01T00:00:00Z', 'pending')`); err != nil {
		t.Fatal(err)
	}
	err = st.WithTx(ctx, func(txCtx context.Context) error {
		if err := st.CreateExecution(txCtx, execution); err != nil {
			return err
		}
		return st.LinkMonitoringExecution(txCtx, "event", execution.ID)
	})
	if err != nil {
		t.Fatal(err)
	}
	var linked string
	if err := st.DB().QueryRow(`SELECT execution_ids FROM monitoring_rule_events WHERE id = 'event'`).Scan(&linked); err != nil || linked != `["exec-1"]` {
		t.Fatalf("linked = %s, err = %v", linked, err)
	}
}
