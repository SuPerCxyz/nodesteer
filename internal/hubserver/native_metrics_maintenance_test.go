package hubserver

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"sync/atomic"
	"testing"
	"time"
)

func TestNativeMetricMaintenanceRunsImmediatelyAndStops(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	ctx, cancel := context.WithCancel(context.Background())
	var aggregateCalls, pruneCalls atomic.Int32
	done := startNativeMetricMaintenance(ctx, 5*time.Millisecond,
		func(context.Context, time.Time) error { aggregateCalls.Add(1); return nil },
		func(context.Context, time.Time) error { pruneCalls.Add(1); return nil },
		logger)

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && (aggregateCalls.Load() < 2 || pruneCalls.Load() < 2) {
		time.Sleep(time.Millisecond)
	}
	if aggregateCalls.Load() < 2 || pruneCalls.Load() < 2 {
		t.Fatalf("maintenance did not run periodically: aggregate=%d prune=%d", aggregateCalls.Load(), pruneCalls.Load())
	}

	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("maintenance goroutine did not stop after cancel")
	}
	aggregate, prune := aggregateCalls.Load(), pruneCalls.Load()
	time.Sleep(20 * time.Millisecond)
	if aggregateCalls.Load() != aggregate || pruneCalls.Load() != prune {
		t.Fatalf("maintenance continued after cancel: aggregate=%d->%d prune=%d->%d", aggregate, aggregateCalls.Load(), prune, pruneCalls.Load())
	}
}

func TestNativeMetricMaintenanceSkipsPruneAfterAggregateError(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var aggregateCalls, pruneCalls atomic.Int32
	done := startNativeMetricMaintenance(ctx, 5*time.Millisecond,
		func(context.Context, time.Time) error { aggregateCalls.Add(1); return errors.New("aggregate failed") },
		func(context.Context, time.Time) error { pruneCalls.Add(1); return nil },
		logger)
	defer func() {
		cancel()
		<-done
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && aggregateCalls.Load() < 3 {
		time.Sleep(time.Millisecond)
	}
	if aggregateCalls.Load() < 3 {
		t.Fatalf("maintenance stopped after aggregate error: aggregate=%d", aggregateCalls.Load())
	}
	if pruneCalls.Load() != 0 {
		t.Fatalf("prune ran after aggregate failure: prune=%d", pruneCalls.Load())
	}
}
