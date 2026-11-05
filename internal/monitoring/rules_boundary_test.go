package monitoring

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type synchronizedBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (b *synchronizedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.b.Write(p)
}

func (b *synchronizedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.b.String()
}

func openMonitoringTestStore(t *testing.T) *store.SQLiteStore {
	t.Helper()
	db, err := store.OpenSQLite(filepath.Join(t.TempDir(), "monitoring.db"))
	if err != nil {
		t.Fatal(err)
	}
	// Keep a single connection so concurrent scheduler goroutines and test
	// assertions cannot race on separate SQLite connections.
	db.DB().SetMaxOpenConns(1)
	t.Cleanup(func() { db.Close() })
	return db
}

func newHTTPRuleFixture(t *testing.T, mutate func(*Rule)) (*store.SQLiteStore, *Repository, context.Context, Target, Rule) {
	t.Helper()
	db := openMonitoringTestStore(t)
	repo := NewRepository(db.DB())
	ctx := context.Background()
	target := Target{Name: "service", URL: "http://example.test", JSONPath: []string{"value"}, IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "rule", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", Enabled: true}
	if mutate != nil {
		mutate(&rule)
	}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	return db, repo, ctx, target, rule
}

func newNodeMetricRuleFixture(t *testing.T, mutate func(*Rule)) (*store.SQLiteStore, *Repository, context.Context, Target, Rule) {
	t.Helper()
	db := openMonitoringTestStore(t)
	repo := NewRepository(db.DB())
	ctx := context.Background()
	if err := db.UpsertNode(ctx, &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}); err != nil {
		t.Fatal(err)
	}
	target := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node", Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 5, MaxAgeSec: 60, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
	if mutate != nil {
		mutate(&rule)
	}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	return db, repo, ctx, target, rule
}

func TestRuleCooldownAfterRecoveryAndPendingRevocation(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, func(r *Rule) { r.CooldownSec = 30 })
	t0 := time.Now().UTC().Truncate(time.Millisecond)
	claim1, err := repo.ClaimRule(ctx, rule, target, true, t0, 20*time.Second)
	if err != nil || claim1 == "" {
		t.Fatalf("initial claim=%q err=%v", claim1, err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim1, nil); err != nil {
		t.Fatal(err)
	}
	if again, err := repo.ClaimRule(ctx, rule, target, true, t0.Add(5*time.Second), 20*time.Second); err != nil || again != "" {
		t.Fatalf("active incident retriggered=%q err=%v", again, err)
	}
	if again, err := repo.ClaimRule(ctx, rule, target, false, t0.Add(10*time.Second), 20*time.Second); err != nil || again != "" {
		t.Fatalf("recovery claimed=%q err=%v", again, err)
	}
	if again, err := repo.ClaimRule(ctx, rule, target, true, t0.Add(20*time.Second), 20*time.Second); err != nil || again != "" {
		t.Fatalf("cooldown claimed=%q err=%v", again, err)
	}
	claim2, err := repo.ClaimRule(ctx, rule, target, true, t0.Add(30*time.Second), 20*time.Second)
	if err != nil || claim2 == "" || claim2 == claim1 {
		t.Fatalf("post-cooldown claim=%q err=%v", claim2, err)
	}
	if _, err := repo.ClaimRule(ctx, rule, target, false, t0.Add(40*time.Second), 20*time.Second); err != nil {
		t.Fatal(err)
	}
	err = db.WithTx(ctx, func(txCtx context.Context) error {
		return db.EnsureMonitoringEventEnabled(txCtx, claim2, rule.ScheduleID)
	})
	if err == nil {
		t.Fatal("recovered condition still authorized the pending event")
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 2 {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if events[0].ID != claim2 || events[0].Status != "pending" || events[1].ID != claim1 || events[1].Status != "completed" {
		t.Fatalf("events=%+v", events)
	}
}

func TestRuleDurationBoundary(t *testing.T) {
	_, repo, ctx, target, rule := newHTTPRuleFixture(t, func(r *Rule) { r.DurationSec = 30 })
	t0 := time.Now().UTC().Truncate(time.Millisecond)
	steps := []struct {
		at        time.Time
		wantClaim bool
	}{
		{t0, false},
		{t0.Add(10 * time.Second), false},
		{t0.Add(20 * time.Second), false},
		{t0.Add(29 * time.Second), false},
		{t0.Add(30 * time.Second), true},
	}
	for _, step := range steps {
		claim, err := repo.ClaimRule(ctx, rule, target, true, step.at, 20*time.Second)
		if err != nil {
			t.Fatalf("at %s: %v", step.at, err)
		}
		if step.wantClaim && claim == "" {
			t.Fatalf("no claim at exactly %s", step.at)
		}
		if !step.wantClaim && claim != "" {
			t.Fatalf("premature claim at %s", step.at)
		}
	}
}

func TestCompareHTTPMaxAgeBoundary(t *testing.T) {
	now := time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		name       string
		observedAt time.Time
		maxAge     time.Duration
		wantErr    bool
	}{
		{"fresh", now, time.Minute, false},
		{"exact max age", now.Add(-time.Minute), time.Minute, false},
		{"one nanosecond past max age", now.Add(-time.Minute - time.Nanosecond), time.Minute, true},
		{"zero max age", now, 0, true},
		{"zero observation time", time.Time{}, time.Minute, true},
		{"future observation", now.Add(time.Nanosecond), time.Minute, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			observation := HTTPObservation{ObservedAt: tc.observedAt, HasValue: true, Value: json.Number("1")}
			matched, err := CompareHTTP(observation, now, tc.maxAge, "eq", "1")
			if tc.wantErr {
				if err == nil {
					t.Fatal("stale or invalid observation accepted")
				}
				return
			}
			if err != nil || !matched {
				t.Fatalf("matched=%v err=%v", matched, err)
			}
		})
	}
}

func TestSchedulerRejectsStaleOrMissingHTTPObservations(t *testing.T) {
	now := time.Now().UTC()
	for _, tc := range []struct {
		name        string
		observation HTTPObservation
		wantClaim   bool
		wantReset   bool
	}{
		{
			name:        "expiry within window",
			observation: HTTPObservation{ObservedAt: now, HasValue: true, Value: now.Add(30 * time.Minute).Format(time.RFC3339)},
			wantClaim:   true,
		},
		{
			name:        "expiry after window",
			observation: HTTPObservation{ObservedAt: now, HasValue: true, Value: now.Add(2 * time.Hour).Format(time.RFC3339)},
		},
		{
			name:        "missing expiry value",
			observation: HTTPObservation{ObservedAt: now},
			wantReset:   true,
		},
		{
			name:        "stale observation",
			observation: HTTPObservation{ObservedAt: now.Add(-21 * time.Second), HasValue: true, Value: now.Add(30 * time.Minute).Format(time.RFC3339)},
			wantReset:   true,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			db, repo, ctx, target, rule := newHTTPRuleFixture(t, func(r *Rule) {
				r.Operator = "expires_within"
				r.Expected = "1h"
			})
			scheduler := NewScheduler(repo, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
			claimed := make(chan string, 1)
			scheduler.SetTrigger(func(_ context.Context, _ Rule, claim string) error {
				claimed <- claim
				return nil
			})
			scheduler.evaluate(ctx, target, tc.observation, nil)
			if tc.wantClaim {
				select {
				case claim := <-claimed:
					if claim == "" {
						t.Fatal("empty claim")
					}
				case <-time.After(time.Second):
					t.Fatal("fresh observation did not claim")
				}
				deadline := time.Now().Add(time.Second)
				for time.Now().Before(deadline) {
					events, err := repo.RuleEvents(ctx, rule.ID)
					if err == nil && len(events) == 1 && events[0].Status == "completed" {
						return
					}
					time.Sleep(10 * time.Millisecond)
				}
				t.Fatal("claim was not finished")
			}
			select {
			case claim := <-claimed:
				t.Fatalf("unexpected claim %q", claim)
			case <-time.After(50 * time.Millisecond):
			}
			var events, active, notBefore int
			if err := db.DB().QueryRow(`SELECT (SELECT COUNT(*) FROM monitoring_rule_events WHERE rule_id=?), (SELECT COUNT(*) FROM monitoring_rules WHERE id=? AND active_since=''), (SELECT COUNT(*) FROM monitoring_rules WHERE id=? AND not_before<>'')`, rule.ID, rule.ID, rule.ID).Scan(&events, &active, &notBefore); err != nil {
				t.Fatal(err)
			}
			if events != 0 || active != 1 {
				t.Fatalf("events=%d active-cleared=%d", events, active)
			}
			if tc.wantReset && notBefore != 1 {
				t.Fatalf("duration was not reset for invalid observation")
			}
		})
	}
}

func TestNodeMetricMissingSampleBreaksDuration(t *testing.T) {
	db, repo, ctx, target, rule := newNodeMetricRuleFixture(t, func(r *Rule) { r.DurationSec = 10 })
	now := time.Now().UTC().Add(-20 * time.Second).Truncate(time.Millisecond)
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-30*time.Second).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	observation := HTTPObservation{ObservedAt: now, Value: json.Number("95"), HasValue: true, Samples: []MetricSample{
		{ObservedAt: now.Add(-20 * time.Second), Value: json.Number("95"), Valid: true},
		{ObservedAt: now.Add(-10 * time.Second), Valid: false},
		{ObservedAt: now, Value: json.Number("95"), Valid: true},
	}}
	claim, err := repo.ProcessNodeSamples(ctx, rule, target, observation, true)
	if err != nil || claim != "" {
		t.Fatalf("missing sample bridged duration: claim=%q err=%v", claim, err)
	}
	var active string
	if err := db.DB().QueryRow(`SELECT active_since FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&active); err != nil {
		t.Fatal(err)
	}
	started, err := time.Parse(time.RFC3339Nano, active)
	if err != nil || !started.Equal(now) {
		t.Fatalf("active_since=%q err=%v", active, err)
	}
	next := HTTPObservation{ObservedAt: now.Add(10 * time.Second), Value: json.Number("95"), HasValue: true, Samples: []MetricSample{
		{ObservedAt: now, Value: json.Number("95"), Valid: true},
		{ObservedAt: now.Add(10 * time.Second), Value: json.Number("95"), Valid: true},
	}}
	claim, err = repo.ProcessNodeSamples(ctx, rule, target, next, true)
	if err != nil || claim == "" {
		t.Fatalf("duration did not complete after the missing sample: claim=%q err=%v", claim, err)
	}
}

func TestPruneObservationsRetainsUnresolvedEvents(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	now := time.Now().UTC()
	old := now.Add(-40 * 24 * time.Hour).Format(time.RFC3339Nano)
	recent := now.Add(-24 * time.Hour).Format(time.RFC3339Nano)
	oldStatuses := []string{"pending", "unknown", "failed", "completed", "compensated", "acknowledged_unknown", "acknowledged_failed"}
	for _, status := range oldStatuses {
		if _, err := db.DB().Exec(`INSERT INTO monitoring_rule_events(id,rule_id,target_id,observed_at,status) VALUES(?,?,?,?,?)`, "old-"+status, rule.ID, target.ID, old, status); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := db.DB().Exec(`INSERT INTO monitoring_rule_events(id,rule_id,target_id,observed_at,status) VALUES(?,?,?,?,?)`, "recent-completed", rule.ID, target.ID, recent, "completed"); err != nil {
		t.Fatal(err)
	}
	for _, observed := range []string{old, recent} {
		if _, err := db.DB().Exec(`INSERT INTO monitoring_http_observations(target_id,observed_at,status_code,duration_ns,value_json,error) VALUES(?,?,200,0,'null','')`, target.ID, observed); err != nil {
			t.Fatal(err)
		}
	}
	if err := repo.PruneObservations(ctx, now); err != nil {
		t.Fatal(err)
	}
	rows, err := db.DB().Query(`SELECT id FROM monitoring_rule_events ORDER BY id`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	kept := map[string]bool{}
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			t.Fatal(err)
		}
		kept[id] = true
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"old-pending", "old-unknown", "old-failed", "recent-completed"} {
		if !kept[id] {
			t.Fatalf("pruned unresolved event %s: %+v", id, kept)
		}
	}
	for _, id := range []string{"old-completed", "old-compensated", "old-acknowledged_unknown", "old-acknowledged_failed"} {
		if kept[id] {
			t.Fatalf("retained resolved event %s: %+v", id, kept)
		}
	}
	var observations int
	if err := db.DB().QueryRow(`SELECT COUNT(*) FROM monitoring_http_observations WHERE target_id=?`, target.ID).Scan(&observations); err != nil {
		t.Fatal(err)
	}
	if observations != 1 {
		t.Fatalf("observations=%d, want 1 recent row", observations)
	}
}

func TestPruneObservationsCascadesCompletedSteps(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	if err := db.WithTx(ctx, func(txCtx context.Context) error {
		if err := db.CreateExecution(txCtx, &models.Execution{ID: "exec", TaskID: "task", NodeID: "node", Status: models.ExecStatusSuccess}); err != nil {
			return err
		}
		return db.LinkMonitoringExecution(store.WithMonitoringStep(txCtx, "action", 0), claim, "exec")
	}); err != nil {
		t.Fatal(err)
	}
	old := time.Now().UTC().Add(-40 * 24 * time.Hour).Format(time.RFC3339Nano)
	if _, err := db.DB().Exec(`UPDATE monitoring_rule_events SET observed_at=?, status='completed' WHERE id=?`, old, claim); err != nil {
		t.Fatal(err)
	}
	if err := repo.PruneObservations(ctx, time.Now()); err != nil {
		t.Fatal(err)
	}
	var events, steps int
	if err := db.DB().QueryRow(`SELECT (SELECT COUNT(*) FROM monitoring_rule_events WHERE id=?), (SELECT COUNT(*) FROM monitoring_rule_steps WHERE event_id=?)`, claim, claim).Scan(&events, &steps); err != nil {
		t.Fatal(err)
	}
	if events != 0 || steps != 0 {
		t.Fatalf("events=%d steps=%d, want cascade delete", events, steps)
	}
}

func TestRuleEventStepsAndAuditTraceability(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	executions := []struct {
		id, phase, status string
		batch             int
	}{
		{"exec-action", "action", models.ExecStatusSuccess, 0},
		{"exec-verify", "verify", models.ExecStatusFailed, 0},
		{"exec-compensate", "compensate", models.ExecStatusRunning, 1},
	}
	for _, item := range executions {
		item := item
		if err := db.WithTx(ctx, func(txCtx context.Context) error {
			if err := db.CreateExecution(txCtx, &models.Execution{ID: item.id, TaskID: "task", NodeID: "node", Status: item.status}); err != nil {
				return err
			}
			return db.LinkMonitoringExecution(store.WithMonitoringStep(txCtx, item.phase, item.batch), claim, item.id)
		}); err != nil {
			t.Fatal(err)
		}
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	event := events[0]
	var linked []string
	if err := json.Unmarshal(event.ExecutionIDs, &linked); err != nil {
		t.Fatal(err)
	}
	if len(linked) != len(executions) {
		t.Fatalf("linked=%v", linked)
	}
	if len(event.Steps) != len(executions) {
		t.Fatalf("steps=%+v", event.Steps)
	}
	for i, step := range event.Steps {
		want := executions[i]
		if step.ExecutionID != want.id || step.Phase != want.phase || step.Batch != want.batch || step.Status != want.status || step.NodeID != "node" {
			t.Fatalf("step[%d]=%+v want %+v", i, step, want)
		}
	}
	if len(event.Observation) == 0 || len(event.RuleSnapshot) == 0 {
		t.Fatalf("event context missing: observation=%s snapshot=%s", event.Observation, event.RuleSnapshot)
	}
}

func TestRuleConfigurationChangesWriteAuditLog(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	ctx := WithAuditActor(context.Background(), "user-1", "alice")
	target := Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "rule", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	rule.Name = "renamed"
	if err := repo.UpdateRule(ctx, rule); err != nil {
		t.Fatal(err)
	}
	if _, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute); err != nil {
		t.Fatal(err)
	}
	rule.Enabled = false
	if err := repo.UpdateRule(ctx, rule); err != nil {
		t.Fatal(err)
	}
	rows, err := db.DB().Query(`SELECT action, username FROM audit_logs WHERE resource='monitoring_rule' AND resource_id=? ORDER BY rowid`, rule.ID)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	actions := []string{}
	for rows.Next() {
		var action, username string
		if err := rows.Scan(&action, &username); err != nil {
			t.Fatal(err)
		}
		if username != "alice" {
			t.Fatalf("audit username=%q", username)
		}
		actions = append(actions, action)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	want := []string{"create", "update", "disable"}
	if strings.Join(actions, ",") != strings.Join(want, ",") {
		t.Fatalf("audit actions=%v want %v", actions, want)
	}
}

func TestPendingRuleDisableBlocksAuthorizationAndReenableKeepsOldEventClosed(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, func(r *Rule) {
		r.VerifyScheduleID = "verify"
		r.CompensateScheduleID = "compensate"
	})
	t0 := time.Now().UTC().Truncate(time.Millisecond)
	claim, err := repo.ClaimRule(ctx, rule, target, true, t0, time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	disabled := rule
	disabled.Enabled = false
	if err := repo.UpdateRule(ctx, disabled); err != nil {
		t.Fatal(err)
	}
	if again, err := repo.ClaimRule(ctx, disabled, target, true, t0.Add(time.Second), time.Minute); err != nil || again != "" {
		t.Fatalf("disabled rule claimed=%q err=%v", again, err)
	}
	scheduleByPhase := map[string]string{"action": rule.ScheduleID, "verify": rule.VerifyScheduleID, "compensate": rule.CompensateScheduleID}
	for phase, scheduleID := range scheduleByPhase {
		if err := db.WithTx(ctx, func(txCtx context.Context) error {
			return db.EnsureMonitoringEventEnabled(store.WithMonitoringStep(txCtx, phase, 0), claim, scheduleID)
		}); err == nil {
			t.Fatalf("disabled rule authorized %s", phase)
		}
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, fmt.Errorf("%w: disabled", ErrKnownFailure)); err != nil {
		t.Fatal(err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	enabled := disabled
	enabled.Enabled = true
	if err := repo.UpdateRule(ctx, enabled); err != nil {
		t.Fatal(err)
	}
	for phase, scheduleID := range scheduleByPhase {
		if err := db.WithTx(ctx, func(txCtx context.Context) error {
			return db.EnsureMonitoringEventEnabled(store.WithMonitoringStep(txCtx, phase, 0), claim, scheduleID)
		}); err == nil {
			t.Fatalf("closed event authorized %s after re-enable", phase)
		}
	}
	now := time.Now().UTC().Add(time.Second)
	if again, err := repo.ClaimRule(ctx, enabled, target, true, now, time.Minute); err != nil || again != "" {
		t.Fatalf("re-enable advanced old event: claim=%q err=%v", again, err)
	}
	if _, err := repo.ClaimRule(ctx, enabled, target, false, now.Add(time.Second), time.Minute); err != nil {
		t.Fatal(err)
	}
	claim2, err := repo.ClaimRule(ctx, enabled, target, true, now.Add(2*time.Second), time.Minute)
	if err != nil || claim2 == "" || claim2 == claim {
		t.Fatalf("new incident claim=%q err=%v", claim2, err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 2 || events[1].Status != "acknowledged_failed" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
}

func TestFinishRuleNormalizesKnownFailureAndDoesNotClearNonPendingEvent(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, fmt.Errorf("%w: %v", ErrKnownFailure, sql.ErrNoRows)); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "failed" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if events[0].Error != knownFailureEventMessage || strings.Contains(events[0].Error, "sql:") {
		t.Fatalf("event error leaked raw storage error: %q", events[0].Error)
	}
	var pending string
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != claim {
		t.Fatalf("pending_id=%q want %q", pending, claim)
	}
	// A duplicate/late callback must not clear pending_id once the event is no
	// longer pending.
	if err := repo.FinishRule(ctx, rule.ID, claim, nil); err != nil {
		t.Fatal(err)
	}
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != claim {
		t.Fatalf("pending_id=%q after non-pending finish", pending)
	}
	events, err = repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "failed" {
		t.Fatalf("events after duplicate finish=%+v err=%v", events, err)
	}
}

func TestSchedulerLogsRawActionErrorButNormalizesEvent(t *testing.T) {
	_, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	var logs synchronizedBuffer
	scheduler := NewScheduler(repo, nil, slog.New(slog.NewTextHandler(&logs, nil)))
	scheduler.SetTrigger(func(context.Context, Rule, string) error {
		return fmt.Errorf("%w: %v", ErrKnownFailure, sql.ErrNoRows)
	})
	scheduler.evaluate(ctx, target, HTTPObservation{ObservedAt: time.Now().UTC(), StatusCode: 503}, nil)
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		events, err := repo.RuleEvents(ctx, rule.ID)
		if err == nil && len(events) == 1 && events[0].Status == "failed" {
			if events[0].Error != knownFailureEventMessage {
				t.Fatalf("event error=%q", events[0].Error)
			}
			if !strings.Contains(logs.String(), "sql: no rows in result set") {
				t.Fatalf("raw action error missing from logs: %q", logs.String())
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("action result was not recorded")
}

func TestMarkPendingUnknownTransitionsAndBlocksUntilResolved(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	if err := repo.MarkPendingUnknown(ctx); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "unknown" || !strings.Contains(events[0].Error, "restarted") {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	var pending string
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != claim {
		t.Fatalf("pending_id=%q want %q", pending, claim)
	}
	if again, err := repo.ClaimRule(ctx, rule, target, true, time.Now().Add(time.Second), time.Minute); err != nil || again != "" {
		t.Fatalf("restart event was repeated: claim=%q err=%v", again, err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	events, err = repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "acknowledged_unknown" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != "" {
		t.Fatalf("pending_id=%q after acknowledgement", pending)
	}
	if err := db.WithTx(ctx, func(txCtx context.Context) error {
		return db.EnsureMonitoringEventEnabled(txCtx, claim, rule.ScheduleID)
	}); err == nil {
		t.Fatal("acknowledged unknown event still authorized execution")
	}
}
