package monitoring

import (
	"errors"
	"testing"
	"time"
)

func testAlertRule() AlertRule {
	return AlertRule{
		Name:        "High CPU",
		NodeID:      "node-1",
		Metric:      "cpu.usage",
		Series:      "cpu.usage|cpu=all",
		Operator:    AlertOperatorGT,
		Threshold:   90,
		DurationSec: 60,
		RecoverySec: 0,
		CooldownSec: 0,
		Severity:    AlertSeverityCritical,
		Channels:    []string{"channel-1"},
		Enabled:     true,
	}
}

func testAlertEvent(id string) *AlertEvent {
	return &AlertEvent{
		ID:        id,
		RuleID:    "rule-1",
		RuleName:  "High CPU",
		NodeID:    "node-1",
		Metric:    "cpu.usage",
		Series:    "cpu.usage|cpu=all",
		Operator:  AlertOperatorGT,
		Status:    AlertStatusFiring,
		Severity:  AlertSeverityCritical,
		Threshold: 90,
		StartedAt: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		FiredAt:   time.Date(2026, 1, 1, 0, 1, 0, 0, time.UTC),
		Notified:  true,
		CreatedAt: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC),
		UpdatedAt: time.Date(2026, 1, 1, 0, 1, 0, 0, time.UTC),
	}
}

func TestAlertRuleValidate(t *testing.T) {
	valid := testAlertRule()
	if err := valid.Validate(); err != nil {
		t.Fatalf("valid rule: %v", err)
	}
	cases := []struct {
		name   string
		mutate func(*AlertRule)
	}{
		{"empty name", func(rule *AlertRule) { rule.Name = "" }},
		{"invalid metric", func(rule *AlertRule) { rule.Metric = "cpu usage" }},
		{"series metric mismatch", func(rule *AlertRule) { rule.Series = "memory.usage" }},
		{"invalid operator", func(rule *AlertRule) { rule.Operator = "contains" }},
		{"invalid threshold", func(rule *AlertRule) { rule.Threshold = 1e20 }},
		{"negative duration", func(rule *AlertRule) { rule.DurationSec = -1 }},
		{"negative recovery", func(rule *AlertRule) { rule.RecoverySec = -1 }},
		{"negative cooldown", func(rule *AlertRule) { rule.CooldownSec = -1 }},
		{"invalid severity", func(rule *AlertRule) { rule.Severity = "page" }},
		{"duplicate channel", func(rule *AlertRule) { rule.Channels = []string{"a", "a"} }},
	}
	for _, testCase := range cases {
		t.Run(testCase.name, func(t *testing.T) {
			rule := valid
			rule.Channels = append([]string{}, valid.Channels...)
			testCase.mutate(&rule)
			if err := rule.Validate(); !errors.Is(err, ErrInvalidAlertRule) {
				t.Fatalf("Validate() = %v, want ErrInvalidAlertRule", err)
			}
		})
	}
}

func TestCompareAlertValue(t *testing.T) {
	cases := []struct {
		operator string
		value    float64
		expected bool
	}{
		{AlertOperatorGT, 91, true},
		{AlertOperatorGT, 90, false},
		{AlertOperatorGTE, 90, true},
		{AlertOperatorLT, 89, true},
		{AlertOperatorLTE, 90, true},
		{AlertOperatorEQ, 90, true},
		{AlertOperatorNE, 90, false},
		{AlertOperatorNE, 89, true},
	}
	for _, testCase := range cases {
		if got := CompareAlertValue(testCase.operator, testCase.value, 90); got != testCase.expected {
			t.Fatalf("%s %v = %v, want %v", testCase.operator, testCase.value, got, testCase.expected)
		}
	}
}

func TestEvaluateAlertDecisionDurationFiringAndCooldown(t *testing.T) {
	rule := testAlertRule()
	rule.CooldownSec = 300
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

	decision := EvaluateAlertDecision(rule, AlertState{}, nil, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base,
	}, base)
	if decision.State.Status != AlertStatusPending || decision.Event == nil || decision.Event.Status != AlertStatusPending {
		t.Fatalf("first decision = %+v", decision)
	}
	if decision.Notify {
		t.Fatal("pending must not notify")
	}
	pending := decision.Event
	pending.ID = "event-1"

	decision = EvaluateAlertDecision(rule, decision.State, pending, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base.Add(59 * time.Second),
	}, base.Add(59*time.Second))
	if decision.State.Status != AlertStatusPending || decision.Notify {
		t.Fatalf("before duration = %+v", decision)
	}

	decision = EvaluateAlertDecision(rule, decision.State, pending, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base.Add(60 * time.Second),
	}, base.Add(60*time.Second))
	if decision.State.Status != AlertStatusFiring || decision.Event.Status != AlertStatusFiring {
		t.Fatalf("at duration = %+v", decision)
	}
	if !decision.Notify {
		t.Fatal("firing must notify")
	}
	if !decision.State.LastNotifiedAt.Equal(base.Add(60 * time.Second)) {
		t.Fatalf("last notified = %v", decision.State.LastNotifiedAt)
	}
	firing := decision.Event
	firing.ID = "event-1"

	decision = EvaluateAlertDecision(rule, decision.State, firing, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 10, ObservedAt: base.Add(90 * time.Second),
	}, base.Add(90*time.Second))
	if decision.State.Status != AlertStatusResolved || decision.Event.Status != AlertStatusResolved || !decision.Notify {
		t.Fatalf("resolve = %+v", decision)
	}
	resolved := decision.Event
	resolved.ID = "event-1"

	decision = EvaluateAlertDecision(rule, decision.State, nil, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base.Add(120 * time.Second),
	}, base.Add(120*time.Second))
	if decision.State.Status != AlertStatusPending || decision.Event == nil {
		t.Fatalf("refire pending = %+v", decision)
	}
	pending = decision.Event
	pending.ID = "event-2"
	decision = EvaluateAlertDecision(rule, decision.State, pending, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base.Add(180 * time.Second),
	}, base.Add(180*time.Second))
	if decision.State.Status != AlertStatusFiring {
		t.Fatalf("refire = %+v", decision)
	}
	if decision.Notify {
		t.Fatal("cooldown must suppress the second notification")
	}
}

func TestEvaluateAlertDecisionRecoveryWindow(t *testing.T) {
	rule := testAlertRule()
	rule.RecoverySec = 30
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	state := AlertState{
		RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
		Status: AlertStatusFiring, ConditionSince: base.Add(-time.Hour),
		LastNotifiedAt: base.Add(-time.Minute), EventID: "event-1",
	}
	event := testAlertEvent("event-1")

	decision := EvaluateAlertDecision(rule, state, event, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 10, ObservedAt: base,
	}, base)
	if decision.State.Status != AlertStatusFiring || decision.Event != nil || decision.Notify {
		t.Fatalf("first recovery evaluation = %+v", decision)
	}
	if !decision.State.RecoverySince.Equal(base) {
		t.Fatalf("recovery since = %v", decision.State.RecoverySince)
	}

	decision = EvaluateAlertDecision(rule, decision.State, event, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 10, ObservedAt: base.Add(30 * time.Second),
	}, base.Add(30*time.Second))
	if decision.State.Status != AlertStatusResolved || decision.Event == nil || !decision.Notify {
		t.Fatalf("recovery resolution = %+v", decision)
	}
	if decision.State.EventID != "" {
		t.Fatalf("resolved state still references event %q", decision.State.EventID)
	}
}

func TestEvaluateAlertDecisionFailClosed(t *testing.T) {
	rule := testAlertRule()
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

	pendingEvent := testAlertEvent("pending-1")
	pendingEvent.Status = AlertStatusPending
	state := AlertState{
		RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
		Status: AlertStatusPending, ConditionSince: base, EventID: "pending-1",
	}
	decision := EvaluateAlertDecision(rule, state, pendingEvent, AlertEvaluation{
		Available: false, Reason: "no_data", NodeID: "node-1", Series: rule.Series,
	}, base)
	if decision.State.Status != AlertStatusUnknown || decision.State.EventID != "" {
		t.Fatalf("pending unavailable = %+v", decision)
	}
	if decision.Event == nil || decision.Event.Status != AlertStatusResolved || decision.Notify {
		t.Fatalf("pending unavailable event = %+v", decision.Event)
	}

	firingEvent := testAlertEvent("event-1")
	state = AlertState{
		RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
		Status: AlertStatusFiring, ConditionSince: base.Add(-time.Hour), EventID: "event-1",
	}
	decision = EvaluateAlertDecision(rule, state, firingEvent, AlertEvaluation{
		Available: false, Reason: "stale", NodeID: "node-1", Series: rule.Series,
	}, base)
	if decision.State.Status != AlertStatusFiring || decision.Event != nil || decision.Notify {
		t.Fatalf("firing unavailable must stay firing: %+v", decision)
	}
	if decision.State.Message != "data unavailable: stale" {
		t.Fatalf("message = %q", decision.State.Message)
	}
}

func TestEvaluateAlertDecisionSilence(t *testing.T) {
	rule := testAlertRule()
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	until := base.Add(10 * time.Minute)
	rule.SilenceUntil = &until

	decision := EvaluateAlertDecision(rule, AlertState{}, nil, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base,
	}, base)
	pending := decision.Event
	pending.ID = "event-1"
	decision = EvaluateAlertDecision(rule, decision.State, pending, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base.Add(60 * time.Second),
	}, base.Add(60*time.Second))
	if decision.State.Status != AlertStatusSilenced || decision.Event.Status != AlertStatusSilenced {
		t.Fatalf("silenced firing = %+v", decision)
	}
	if decision.Notify {
		t.Fatal("silenced alert must not notify")
	}
	silenced := decision.Event
	silenced.ID = "event-1"

	decision = EvaluateAlertDecision(rule, decision.State, silenced, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base.Add(11 * time.Minute),
	}, base.Add(11*time.Minute))
	if decision.State.Status != AlertStatusFiring || decision.Event.Status != AlertStatusFiring {
		t.Fatalf("silence expiry = %+v", decision)
	}
	if decision.Notify {
		t.Fatal("silence expiry must not retroactively notify")
	}
}

func TestEvaluateAlertDecisionWithoutChannelsDoesNotNotify(t *testing.T) {
	rule := testAlertRule()
	rule.Channels = nil
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	decision := EvaluateAlertDecision(rule, AlertState{
		RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
		Status: AlertStatusPending, ConditionSince: base.Add(-time.Minute),
	}, nil, AlertEvaluation{
		Available: true, NodeID: "node-1", Series: rule.Series, Value: 95, ObservedAt: base,
	}, base)
	if decision.State.Status != AlertStatusFiring || decision.Notify {
		t.Fatalf("no-channel firing = %+v", decision)
	}
}
