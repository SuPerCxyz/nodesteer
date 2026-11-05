package hubserver

import (
	"context"
	"testing"
	"time"
)

type fakeAlertRunner struct {
	started chan struct{}
	stopped chan struct{}
}

func (r *fakeAlertRunner) Run(ctx context.Context) {
	close(r.started)
	<-ctx.Done()
	close(r.stopped)
}

func TestStartAlertEvaluationRunsAndStopsWithContext(t *testing.T) {
	if alertEvaluationInterval != 30*time.Second {
		t.Fatalf("alert evaluation interval = %v, want 30s", alertEvaluationInterval)
	}
	runner := &fakeAlertRunner{started: make(chan struct{}), stopped: make(chan struct{})}
	ctx, cancel := context.WithCancel(context.Background())
	done := startAlertEvaluation(ctx, runner)
	select {
	case <-runner.started:
	case <-time.After(time.Second):
		t.Fatal("alert evaluator did not start")
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("alert evaluator did not stop after cancel")
	}
	select {
	case <-runner.stopped:
	default:
		t.Fatal("runner did not observe cancellation")
	}
}
