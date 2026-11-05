package hub

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestMarkStartedDoesNotReviveAbandonedExecution(t *testing.T) {
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	repo := monitoring.NewRepository(st.DB())
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	if err := st.WithTx(ctx, func(txCtx context.Context) error {
		if err := st.CreateExecution(txCtx, &models.Execution{ID: "exec", TaskID: "task", NodeID: "node", Status: models.ExecStatusRunning}); err != nil {
			return err
		}
		return st.LinkMonitoringExecution(store.WithMonitoringStep(txCtx, "action", 0), claim, "exec")
	}); err != nil {
		t.Fatal(err)
	}
	if err := repo.MarkPendingUnknown(ctx); err != nil {
		t.Fatal(err)
	}
	if err := repo.AbandonUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	em := NewExecutionManager(st, NewSessionManager(), NewNodeManager(st, NewRevisionManager(st)), slog.New(slog.NewTextHandler(io.Discard, nil)))
	em.MarkStarted(ctx, "node", "exec", "")
	var status, startTime string
	if err := st.DB().QueryRow(`SELECT status, start_time FROM executions WHERE id='exec'`).Scan(&status, &startTime); err != nil {
		t.Fatal(err)
	}
	if status != models.ExecStatusBlocked || startTime != "" {
		t.Fatalf("late start revived execution: status=%q start=%q", status, startTime)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "abandoned_unknown" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if err := st.CreateExecution(ctx, &models.Execution{ID: "exec-pending", TaskID: "task", NodeID: "node", Status: models.ExecStatusPending}); err != nil {
		t.Fatal(err)
	}
	em.MarkStarted(ctx, "node", "exec-pending", "")
	if err := st.DB().QueryRow(`SELECT status FROM executions WHERE id='exec-pending'`).Scan(&status); err != nil || status != models.ExecStatusRunning {
		t.Fatalf("normal start status=%q err=%v", status, err)
	}
}
