package monitoring

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestRuleClaimsDeduplicateAndRecoverUnknown(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	ctx := context.Background()
	target := Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "expiry", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", DurationSec: 10, CooldownSec: 30, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	claim, err := repo.ClaimRule(ctx, rule, target, true, now, 20*time.Second)
	if err != nil || claim != "" {
		t.Fatalf("premature claim %s: %v", claim, err)
	}
	claim, err = repo.ClaimRule(ctx, rule, target, true, now.Add(10*time.Second), 20*time.Second)
	if err != nil || claim == "" {
		t.Fatalf("missing claim %s: %v", claim, err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err == nil {
		t.Fatal("active action resolved early")
	}
	if again, err := repo.ClaimRule(ctx, rule, target, true, now.Add(11*time.Second), 20*time.Second); err != nil || again != "" {
		t.Fatalf("duplicate %s: %v", again, err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, errors.New("partial failure")); err != nil {
		t.Fatal(err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	if again, err := repo.ClaimRule(ctx, rule, target, true, now.Add(50*time.Second), 20*time.Second); err != nil || again != "" {
		t.Fatalf("retriggered active incident %s: %v", again, err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "acknowledged_unknown" {
		t.Fatalf("events %+v: %v", events, err)
	}
}

func TestKnownFailurePausesRuleUntilAcknowledged(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	ctx := context.Background()
	target := Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "rule", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%s err=%v", claim, err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, fmt.Errorf("%w: schedule disabled", ErrKnownFailure)); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "failed" || events[0].Error != knownFailureEventMessage {
		t.Fatalf("events %+v: %v", events, err)
	}
	var pending string
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != claim {
		t.Fatalf("pending_id=%q, want %q", pending, claim)
	}
	if again, err := repo.ClaimRule(ctx, rule, target, true, time.Now().Add(time.Minute), time.Minute); err != nil || again != "" {
		t.Fatalf("known failure retriggered: %s %v", again, err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	events, err = repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "acknowledged_failed" {
		t.Fatalf("events %+v: %v", events, err)
	}
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != "" {
		t.Fatalf("pending_id=%q after acknowledgement", pending)
	}
}

func TestRuleDurationDoesNotBridgeGapOrTargetChanges(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	ctx := context.Background()
	target := Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "failure", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", DurationSec: 10, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	if _, err := repo.ClaimRule(ctx, rule, target, true, now, 20*time.Second); err != nil {
		t.Fatal(err)
	}
	if claim, err := repo.ClaimRule(ctx, rule, target, true, now.Add(time.Minute), 20*time.Second); err != nil || claim != "" {
		t.Fatalf("gap claim %s: %v", claim, err)
	}
	changed := target
	changed.URL = "http://example.net"
	if err := repo.Update(ctx, changed); err != nil {
		t.Fatal(err)
	}
	if claim, err := repo.ClaimRule(ctx, rule, target, true, now.Add(70*time.Second), 20*time.Second); err != nil || claim != "" {
		t.Fatalf("old target claim %s: %v", claim, err)
	}
}

func TestEditingFiredRuleDoesNotTriggerAgain(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	ctx := context.Background()
	target := Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "failure", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	claim, err := repo.ClaimRule(ctx, rule, target, true, now, 20*time.Second)
	if err != nil || claim == "" {
		t.Fatalf("claim = %s, err = %v", claim, err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, nil); err != nil {
		t.Fatal(err)
	}
	rule.Name = "renamed"
	if err := repo.UpdateRule(ctx, rule); err != nil {
		t.Fatal(err)
	}
	if claim, err := repo.ClaimRule(ctx, rule, target, true, now.Add(time.Second), 20*time.Second); err != nil || claim != "" {
		t.Fatalf("edit retriggered: %s %v", claim, err)
	}
}

func TestUnknownCannotBeAcknowledgedWhileExecutionActive(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	ctx := context.Background()
	target := Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "rule", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%s err=%v", claim, err)
	}
	ex := &models.Execution{ID: "exec", TaskID: "task", NodeID: "node", Status: models.ExecStatusRunning}
	if err := db.WithTx(ctx, func(txCtx context.Context) error {
		if err := db.CreateExecution(txCtx, ex); err != nil {
			return err
		}
		return db.LinkMonitoringExecution(txCtx, claim, ex.ID)
	}); err != nil {
		t.Fatal(err)
	}
	if err := repo.MarkPendingUnknown(ctx); err != nil {
		t.Fatal(err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err == nil {
		t.Fatal("running execution was acknowledged")
	}
	ex.Status = models.ExecStatusSuccess
	if err := db.UpdateExecution(ctx, ex); err != nil {
		t.Fatal(err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
}

func TestPendingRuleCanOnlyBeDisabled(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	ctx := context.Background()
	target := Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "rule", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	if _, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute); err != nil {
		t.Fatal(err)
	}
	modified := rule
	modified.Name = "changed"
	modified.Enabled = false
	if err := repo.UpdateRule(ctx, modified); err == nil {
		t.Fatal("pending rule changed beyond disable")
	}
	rule.Enabled = false
	if err := repo.UpdateRule(ctx, rule); err != nil {
		t.Fatal(err)
	}
}

func TestNodeMetricRequiresOnlineNode(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	node := &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}
	if err := db.UpsertNode(ctx, node); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: node.ID, Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 2, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "high CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	if err := db.UpdateNodeStatus(ctx, node.ID, models.NodeStatusOffline); err != nil {
		t.Fatal(err)
	}
	if claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute); err != nil || claim != "" {
		t.Fatalf("offline claim=%s err=%v", claim, err)
	}
	if err := db.UpdateNodeStatus(ctx, node.ID, models.NodeStatusOnline); err != nil {
		t.Fatal(err)
	}
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("online claim=%s err=%v", claim, err)
	}
	if err := db.UpdateNodeStatus(ctx, node.ID, models.NodeStatusOffline); err != nil {
		t.Fatal(err)
	}
	if _, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute); err != nil {
		t.Fatal(err)
	}
	if err := db.UpdateNodeStatus(ctx, node.ID, models.NodeStatusOnline); err != nil {
		t.Fatal(err)
	}
	if _, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute); err != nil {
		t.Fatal(err)
	}
	err = db.WithTx(ctx, func(txCtx context.Context) error {
		return db.EnsureMonitoringEventEnabled(txCtx, claim, rule.ScheduleID)
	})
	if err == nil {
		t.Fatal("offline-online transition resumed old event")
	}
	if err := repo.MarkPendingUnknown(ctx); err != nil {
		t.Fatal(err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	if claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute); err != nil || claim != "" {
		t.Fatalf("resolved rule claim=%s err=%v", claim, err)
	}
}

func TestNodeMetricPendingUsesRealSampleTimeAndRevokesOnRecovery(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	node := &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}
	if err := db.UpsertNode(ctx, node); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: node.ID, Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 2, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	sample := time.Now().UTC().Add(-15 * time.Second).Truncate(time.Millisecond)
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, sample.Add(-time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	claim, err := repo.ClaimRule(ctx, rule, target, true, sample, 20*time.Second, HTTPObservation{ObservedAt: sample, HasValue: true, Value: json.Number("95")})
	if err != nil || claim == "" {
		t.Fatalf("claim=%s err=%v", claim, err)
	}
	var confirmed string
	if err := db.DB().QueryRow(`SELECT last_confirmed_at FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&confirmed); err != nil {
		t.Fatal(err)
	}
	stored, err := time.Parse(time.RFC3339Nano, confirmed)
	if err != nil || !stored.Equal(sample) {
		t.Fatalf("confirmation %s does not equal source sample %s", confirmed, sample)
	}
	newSample := sample.Add(10 * time.Second)
	if _, err := repo.ClaimRule(ctx, rule, target, true, newSample, 20*time.Second, HTTPObservation{ObservedAt: newSample, HasValue: true, Value: json.Number("96")}); err != nil {
		t.Fatal(err)
	}
	err = db.WithTx(ctx, func(txCtx context.Context) error {
		return db.EnsureMonitoringEventEnabled(txCtx, claim, rule.ScheduleID)
	})
	if err != nil {
		t.Fatalf("fresh confirmation denied: %v", err)
	}
	if _, err := repo.ClaimRule(ctx, rule, target, false, time.Now().UTC(), 20*time.Second, HTTPObservation{ObservedAt: time.Now().UTC(), HasValue: true, Value: json.Number("40")}); err != nil {
		t.Fatal(err)
	}
	err = db.WithTx(ctx, func(txCtx context.Context) error {
		return db.EnsureMonitoringEventEnabled(txCtx, claim, rule.ScheduleID)
	})
	if err == nil {
		t.Fatal("recovered condition still authorized another node")
	}
	if _, err := repo.ClaimRule(ctx, rule, target, true, time.Now().UTC().Add(time.Second), 20*time.Second, HTTPObservation{ObservedAt: time.Now().UTC().Add(time.Second), HasValue: true, Value: json.Number("95")}); err != nil {
		t.Fatal(err)
	}
	err = db.WithTx(ctx, func(txCtx context.Context) error {
		return db.EnsureMonitoringEventEnabled(txCtx, claim, rule.ScheduleID)
	})
	if err == nil {
		t.Fatal("reappearing condition resumed an interrupted event")
	}
}

func TestNodeMetricBatchHighLowHighDoesNotFakeContinuity(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	now := time.Now().UTC().Truncate(time.Millisecond)
	node := &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}
	if err := db.UpsertNode(ctx, node); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: node.ID, Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 30, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", DurationSec: 30, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-40*time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	samples := []MetricSample{{ObservedAt: now.Add(-30 * time.Second), Value: json.Number("95"), Valid: true}, {ObservedAt: now.Add(-25 * time.Second), Value: json.Number("20"), Valid: true}, {ObservedAt: now.Add(-20 * time.Second), Value: json.Number("95"), Valid: true}, {ObservedAt: now, Value: json.Number("95"), Valid: true}}
	obs := HTTPObservation{ObservedAt: now, Value: json.Number("95"), HasValue: true, Samples: samples}
	claim, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true)
	if err != nil || claim != "" {
		t.Fatalf("high-low-high falsely triggered: %s %v", claim, err)
	}
	var active string
	if err := db.DB().QueryRow(`SELECT active_since FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&active); err != nil {
		t.Fatal(err)
	}
	started, err := time.Parse(time.RFC3339Nano, active)
	if err != nil || !started.Equal(now.Add(-20*time.Second)) {
		t.Fatalf("active_since=%s err=%v", active, err)
	}
}

func TestNodeMetricBatchRecoveryPermanentlyInterruptsPending(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	now := time.Now().UTC().Truncate(time.Millisecond)
	node := &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}
	if err := db.UpsertNode(ctx, node); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: node.ID, Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-20*time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	claim, err := repo.ClaimRule(ctx, rule, target, true, now.Add(-10*time.Second), 20*time.Second, HTTPObservation{ObservedAt: now.Add(-10 * time.Second), HasValue: true, Value: json.Number("95")})
	if err != nil || claim == "" {
		t.Fatalf("claim=%s err=%v", claim, err)
	}
	samples := []MetricSample{{ObservedAt: now.Add(-9 * time.Second), Value: json.Number("95"), Valid: true}, {ObservedAt: now.Add(-5 * time.Second), Value: json.Number("20"), Valid: true}, {ObservedAt: now, Value: json.Number("95"), Valid: true}}
	if _, err := repo.ProcessNodeSamples(ctx, rule, target, HTTPObservation{ObservedAt: now, Value: json.Number("95"), HasValue: true, Samples: samples}, true); err != nil {
		t.Fatal(err)
	}
	if err := db.WithTx(ctx, func(txCtx context.Context) error {
		return db.EnsureMonitoringEventEnabled(txCtx, claim, rule.ScheduleID)
	}); err == nil {
		t.Fatal("old event resumed after intermediate recovery")
	}
}

func TestNodeMetricDuplicateAndSlotRetry(t *testing.T) {
	for _, tc := range []struct {
		name          string
		first, second json.Number
		same          bool
	}{
		{"high-low", "95", "20", false}, {"low-high", "20", "95", false}, {"identical", "95", "95", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			db, err := store.OpenInMemory()
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			ctx := context.Background()
			repo := NewRepository(db.DB())
			now := time.Now().UTC().Truncate(time.Millisecond)
			if err := db.UpsertNode(ctx, &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}); err != nil {
				t.Fatal(err)
			}
			target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node", Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
			if err := repo.Create(ctx, &target); err != nil {
				t.Fatal(err)
			}
			rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
				t.Fatal(err)
			}
			obs := HTTPObservation{ObservedAt: now, Value: tc.second, HasValue: true, Samples: []MetricSample{{ObservedAt: now, Value: tc.first, Valid: true}, {ObservedAt: now, Value: tc.second, Valid: true}}}
			claim, err := repo.ProcessNodeSamples(ctx, rule, target, obs, false)
			if tc.same {
				if err != nil || claim != "" {
					t.Fatalf("slot busy: %q %v", claim, err)
				}
				claim, err = repo.ProcessNodeSamples(ctx, rule, target, obs, true)
				if err != nil || claim == "" {
					t.Fatalf("slot released: %q %v", claim, err)
				}
				again, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true)
				if err != nil || again != "" {
					t.Fatalf("duplicate claim: %q %v", again, err)
				}
			} else if err == nil || claim != "" {
				t.Fatalf("conflict: %q %v", claim, err)
			}
		})
	}
}

func TestNodeMetricCrossBatchConflictInterruptsPending(t *testing.T) {
	for _, changed := range []json.Number{"20", "95.0"} {
		t.Run(string(changed), func(t *testing.T) {
			db, err := store.OpenInMemory()
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			ctx := context.Background()
			repo := NewRepository(db.DB())
			now := time.Now().UTC().Truncate(time.Millisecond)
			if err := db.UpsertNode(ctx, &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}); err != nil {
				t.Fatal(err)
			}
			target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node", Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
			if err := repo.Create(ctx, &target); err != nil {
				t.Fatal(err)
			}
			rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
				t.Fatal(err)
			}
			obs := HTTPObservation{ObservedAt: now, Value: json.Number("95"), HasValue: true}
			claim, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true)
			if err != nil || claim == "" {
				t.Fatalf("claim %q %v", claim, err)
			}
			obs.Value = changed
			if _, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true); err == nil {
				t.Fatal("conflicting sample accepted")
			}
			var interrupted int
			if err := db.DB().QueryRow(`SELECT interrupted FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&interrupted); err != nil || interrupted != 1 {
				t.Fatalf("interrupted=%d err=%v", interrupted, err)
			}
		})
	}
}

func TestNodeMetricBackfillChangesOlderPointInterruptsPending(t *testing.T) {
	for _, invalid := range []bool{false, true} {
		t.Run(fmt.Sprintf("invalid=%v", invalid), func(t *testing.T) {
			db, err := store.OpenInMemory()
			if err != nil {
				t.Fatal(err)
			}
			defer db.Close()
			ctx := context.Background()
			repo := NewRepository(db.DB())
			now := time.Now().UTC().Truncate(time.Second)
			if err := db.UpsertNode(ctx, &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}); err != nil {
				t.Fatal(err)
			}
			target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node", Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
			if err := repo.Create(ctx, &target); err != nil {
				t.Fatal(err)
			}
			rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-61*time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
				t.Fatal(err)
			}
			samples := []MetricSample{{ObservedAt: now.Add(-60*time.Second + 500*time.Millisecond), Value: json.Number("95"), Valid: true}, {ObservedAt: now.Add(-5 * time.Second), Value: json.Number("95"), Valid: true}, {ObservedAt: now, Value: json.Number("95"), Valid: true}}
			obs := HTTPObservation{ObservedAt: now, Value: json.Number("95"), HasValue: true, Samples: samples}
			claim, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true)
			if err != nil || claim == "" {
				t.Fatalf("claim=%q err=%v", claim, err)
			}
			obs.Samples[0].Value = json.Number("20")
			obs.Samples[0].Valid = !invalid
			if claim, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true); err == nil || claim != "" {
				t.Fatalf("backfill accepted: %q %v", claim, err)
			}
			var interrupted int
			if err := db.DB().QueryRow(`SELECT interrupted FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&interrupted); err != nil || interrupted != 1 {
				t.Fatalf("interrupted=%d err=%v", interrupted, err)
			}
		})
	}
}

func TestNodeMetricLateInsertedPointInterruptsPending(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	now := time.Now().UTC().Truncate(time.Second)
	if err := db.UpsertNode(ctx, &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node", Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-20*time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	samples := []MetricSample{{ObservedAt: now.Add(-10 * time.Second), Value: json.Number("95"), Valid: true}, {ObservedAt: now, Value: json.Number("95"), Valid: true}}
	obs := HTTPObservation{ObservedAt: now, Value: json.Number("95"), HasValue: true, Samples: samples}
	claim, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	obs.Samples = []MetricSample{samples[0], {ObservedAt: now.Add(-5 * time.Second), Value: json.Number("20"), Valid: true}, samples[1]}
	if _, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true); err == nil {
		t.Fatal("late recovery accepted")
	}
	var interrupted int
	if err := db.DB().QueryRow(`SELECT interrupted FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&interrupted); err != nil || interrupted != 1 {
		t.Fatalf("interrupted=%d err=%v", interrupted, err)
	}
}

func TestNodeMetricTargetUpdateClearsPreviousSeriesWindow(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	now := time.Now().UTC().Add(-time.Second)
	if err := db.UpsertNode(ctx, &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node", Metric: "cpu.usage", Series: "cpu.usage|cpu=1", IntervalSec: 10, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	obs := HTTPObservation{ObservedAt: now, Value: json.Number("95"), HasValue: true}
	if _, err := repo.ProcessNodeSamples(ctx, rule, target, obs, false); err != nil {
		t.Fatal(err)
	}
	target.Series = "cpu.usage|cpu=2"
	if err := repo.Update(ctx, target); err != nil {
		t.Fatal(err)
	}
	var count int
	var last, identity, active, boundary string
	if err := db.DB().QueryRow(`SELECT (SELECT count(*) FROM monitoring_rule_samples WHERE rule_id=?),last_observed,last_sample,active_since,not_before FROM monitoring_rules WHERE id=?`, rule.ID, rule.ID).Scan(&count, &last, &identity, &active, &boundary); err != nil {
		t.Fatal(err)
	}
	if count != 0 || last != "" || identity != "" || active != "" || boundary == "" {
		t.Fatalf("state: %d %q %q %q %q", count, last, identity, active, boundary)
	}
	obs.ObservedAt = time.Now().UTC()
	obs.Value = json.Number("99")
	if claim, err := repo.ProcessNodeSamples(ctx, rule, target, obs, true); err != nil || claim == "" {
		t.Fatalf("new series claim=%q err=%v", claim, err)
	}
	if err := repo.Update(ctx, target); err == nil {
		t.Fatal("updated target with pending execution")
	}
}

func TestNodeMetricGapInterruptsPending(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	now := time.Now().UTC().Truncate(time.Millisecond)
	if err := db.UpsertNode(ctx, &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node", Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 30, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", DurationSec: 5, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-20*time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	first := HTTPObservation{ObservedAt: now.Add(-10 * time.Second), Value: json.Number("95"), HasValue: true, Samples: []MetricSample{{ObservedAt: now.Add(-15 * time.Second), Value: json.Number("95"), Valid: true}, {ObservedAt: now.Add(-10 * time.Second), Value: json.Number("95"), Valid: true}}}
	claim, err := repo.ProcessNodeSamples(ctx, rule, target, first, true)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	next := HTTPObservation{ObservedAt: first.ObservedAt, Value: json.Number("95"), HasValue: true, ContinuityStart: now.Add(-12 * time.Second)}
	if _, err := repo.ProcessNodeSamples(ctx, rule, target, next, true); err != nil {
		t.Fatal(err)
	}
	var interrupted int
	if err := db.DB().QueryRow(`SELECT interrupted FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&interrupted); err != nil || interrupted != 1 {
		t.Fatalf("interrupted=%d err=%v", interrupted, err)
	}
}

func TestNodeMetricSlowSamplingDoesNotResetContinuity(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	node := &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}
	if err := db.UpsertNode(ctx, node); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: node.ID, Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", DurationSec: 300, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Add(-time.Second)
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-700*time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	samples := []MetricSample{
		{ObservedAt: now.Add(-600 * time.Second), Value: json.Number("95"), Valid: true},
		{ObservedAt: now.Add(-300 * time.Second), Value: json.Number("95"), Valid: true},
		{ObservedAt: now, Value: json.Number("95"), Valid: true},
	}
	observation := HTTPObservation{ObservedAt: now, Value: json.Number("95"), HasValue: true, Samples: samples}
	claim, err := repo.ProcessNodeSamples(ctx, rule, target, observation, true)
	if err != nil || claim == "" {
		t.Fatalf("slow sampling reset continuity: claim=%q err=%v", claim, err)
	}
}
