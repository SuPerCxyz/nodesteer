package monitoring

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func seedAlertNode(t *testing.T, st *store.SQLiteStore, id, status string) {
	t.Helper()
	if err := st.UpsertNode(context.Background(), &models.Node{
		ID: id, AgentID: "agent-" + id, Hostname: id, Status: status,
	}); err != nil {
		t.Fatalf("seed node %s: %v", id, err)
	}
}

func seedAlertSample(t *testing.T, st *store.SQLiteStore, nodeID, metric string, dimensions map[string]string, at time.Time, value float64) {
	t.Helper()
	if err := st.InsertNodeMetricSamples(context.Background(), nodeID, []store.NativeSample{{
		Timestamp: at, Metric: metric, Dimensions: dimensions,
		Value: float64Ptr(value), Quality: store.NativeQualityValid,
	}}); err != nil {
		t.Fatalf("seed sample: %v", err)
	}
}

func newAlertEngineTest(t *testing.T, now *time.Time) (*store.SQLiteStore, *AlertRepository, *AlertEvaluator) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	repo := NewAlertRepository(st.DB())
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	notifier := NewAlertNotifier(repo, logger)
	evaluator := NewAlertEvaluator(repo, st, notifier, logger)
	evaluator.SetClock(func() time.Time { return *now })
	return st, repo, evaluator
}

func TestAlertEvaluatorDurationFiringAndRecovery(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	st, repo, evaluator := newAlertEngineTest(t, &now)
	seedAlertNode(t, st, "node-1", models.NodeStatusOnline)
	seedAlertSample(t, st, "node-1", "cpu.usage", map[string]string{"cpu": "all"}, now, 95)

	channel := createAlertTestChannel(t, repo)
	rule := testAlertRule()
	rule.NodeID = "node-1"
	rule.DurationSec = 60
	rule.Channels = []string{channel.ID}
	if err := repo.CreateAlertRule(context.Background(), &rule); err != nil {
		t.Fatalf("create rule: %v", err)
	}

	if err := evaluator.EvaluateOnce(context.Background()); err != nil {
		t.Fatalf("evaluate pending: %v", err)
	}
	events, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "active"})
	if err != nil || total != 1 || events[0].Status != AlertStatusPending {
		t.Fatalf("pending events = %+v, %v", events, err)
	}

	now = now.Add(60 * time.Second)
	seedAlertSample(t, st, "node-1", "cpu.usage", map[string]string{"cpu": "all"}, now, 95)
	if err := evaluator.EvaluateOnce(context.Background()); err != nil {
		t.Fatalf("evaluate firing: %v", err)
	}
	events, total, err = repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "active"})
	if err != nil || total != 1 || events[0].Status != AlertStatusFiring || !events[0].Notified {
		t.Fatalf("firing events = %+v, %v", events, err)
	}
	var notifications int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM alert_notifications WHERE status = 'pending'`).Scan(&notifications); err != nil {
		t.Fatalf("notifications: %v", err)
	}
	if notifications != 1 {
		t.Fatalf("pending notifications = %d, want 1", notifications)
	}

	now = now.Add(60 * time.Second)
	seedAlertSample(t, st, "node-1", "cpu.usage", map[string]string{"cpu": "all"}, now, 10)
	if err := evaluator.EvaluateOnce(context.Background()); err != nil {
		t.Fatalf("evaluate resolved: %v", err)
	}
	events, total, err = repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "history"})
	if err != nil || total != 1 || events[0].Status != AlertStatusResolved {
		t.Fatalf("history events = %+v, %v", events, err)
	}
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM alert_notifications`).Scan(&notifications); err != nil {
		t.Fatalf("notifications: %v", err)
	}
	if notifications != 2 {
		t.Fatalf("notifications = %d, want 2", notifications)
	}
}

func TestAlertEvaluatorFailClosed(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	st, repo, evaluator := newAlertEngineTest(t, &now)
	seedAlertNode(t, st, "node-empty", models.NodeStatusOnline)
	seedAlertNode(t, st, "node-offline", models.NodeStatusOffline)
	seedAlertSample(t, st, "node-offline", "cpu.usage", map[string]string{"cpu": "all"}, now, 99)
	seedAlertNode(t, st, "node-stale", models.NodeStatusOnline)
	seedAlertSample(t, st, "node-stale", "cpu.usage", map[string]string{"cpu": "all"}, now.Add(-10*time.Minute), 99)

	channel := createAlertTestChannel(t, repo)
	for _, nodeID := range []string{"node-empty", "node-offline", "node-stale"} {
		rule := testAlertRule()
		rule.Name = "rule " + nodeID
		rule.NodeID = nodeID
		rule.DurationSec = 0
		rule.Channels = []string{channel.ID}
		if err := repo.CreateAlertRule(context.Background(), &rule); err != nil {
			t.Fatalf("create rule %s: %v", nodeID, err)
		}
	}
	if err := evaluator.EvaluateOnce(context.Background()); err != nil {
		t.Fatalf("evaluate: %v", err)
	}
	_, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "all"})
	if err != nil || total != 0 {
		t.Fatalf("fail-closed events = %d, %v", total, err)
	}
	states, err := repo.ListAlertStates(context.Background(), "")
	if err != nil || len(states) != 3 {
		t.Fatalf("states = %+v, %v", states, err)
	}
	for _, state := range states {
		if state.Status != AlertStatusUnknown {
			t.Fatalf("state = %+v, want unknown", state)
		}
	}
}

func TestAlertEvaluatorAllNodesMatchesByMetricAndSeries(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	st, repo, evaluator := newAlertEngineTest(t, &now)
	seedAlertNode(t, st, "node-1", models.NodeStatusOnline)
	seedAlertNode(t, st, "node-2", models.NodeStatusOnline)
	seedAlertSample(t, st, "node-1", "cpu.usage", map[string]string{"cpu": "all"}, now, 95)
	seedAlertSample(t, st, "node-2", "cpu.usage", map[string]string{"cpu": "all"}, now, 10)

	channel := createAlertTestChannel(t, repo)
	rule := testAlertRule()
	rule.NodeID = ""
	rule.Series = ""
	rule.DurationSec = 0
	rule.Channels = []string{channel.ID}
	if err := repo.CreateAlertRule(context.Background(), &rule); err != nil {
		t.Fatalf("create rule: %v", err)
	}
	if err := evaluator.EvaluateOnce(context.Background()); err != nil {
		t.Fatalf("evaluate: %v", err)
	}
	events, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "active"})
	if err != nil || total != 1 || len(events) != 1 {
		t.Fatalf("events = %+v, %v", events, err)
	}
	if events[0].NodeID != "node-1" || events[0].Status != AlertStatusFiring {
		t.Fatalf("event = %+v", events[0])
	}
}

func TestAlertEvaluatorDeduplicatesActiveEvent(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	st, repo, evaluator := newAlertEngineTest(t, &now)
	seedAlertNode(t, st, "node-1", models.NodeStatusOnline)
	seedAlertSample(t, st, "node-1", "cpu.usage", map[string]string{"cpu": "all"}, now, 95)
	channel := createAlertTestChannel(t, repo)
	rule := testAlertRule()
	rule.NodeID = "node-1"
	rule.DurationSec = 0
	rule.Channels = []string{channel.ID}
	if err := repo.CreateAlertRule(context.Background(), &rule); err != nil {
		t.Fatalf("create rule: %v", err)
	}
	if err := evaluator.EvaluateOnce(context.Background()); err != nil {
		t.Fatalf("first evaluate: %v", err)
	}
	if err := evaluator.EvaluateOnce(context.Background()); err != nil {
		t.Fatalf("second evaluate: %v", err)
	}
	_, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "active"})
	if err != nil || total != 1 {
		t.Fatalf("active events = %d, %v", total, err)
	}
	var notifications int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM alert_notifications`).Scan(&notifications); err != nil {
		t.Fatalf("notifications: %v", err)
	}
	if notifications != 1 {
		t.Fatalf("notifications = %d, want 1", notifications)
	}
}

func TestAlertEvaluatorSilencedRuleDoesNotNotify(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	st, repo, evaluator := newAlertEngineTest(t, &now)
	seedAlertNode(t, st, "node-1", models.NodeStatusOnline)
	seedAlertSample(t, st, "node-1", "cpu.usage", map[string]string{"cpu": "all"}, now, 95)
	channel := createAlertTestChannel(t, repo)
	rule := testAlertRule()
	rule.NodeID = "node-1"
	rule.DurationSec = 0
	rule.Channels = []string{channel.ID}
	if err := repo.CreateAlertRule(context.Background(), &rule); err != nil {
		t.Fatalf("create rule: %v", err)
	}
	if err := repo.SetAlertRuleSilence(context.Background(), rule.ID, now.Add(time.Hour)); err != nil {
		t.Fatalf("silence: %v", err)
	}
	if err := evaluator.EvaluateOnce(context.Background()); err != nil {
		t.Fatalf("evaluate: %v", err)
	}
	events, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "active"})
	if err != nil || total != 1 || events[0].Status != AlertStatusSilenced || events[0].Notified {
		t.Fatalf("silenced event = %+v, %v", events, err)
	}
	var notifications int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM alert_notifications`).Scan(&notifications); err != nil {
		t.Fatalf("notifications: %v", err)
	}
	if notifications != 0 {
		t.Fatalf("silenced notifications = %d, want 0", notifications)
	}
}
