package monitoring

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func newAlertTestRepository(t *testing.T) (*store.SQLiteStore, *AlertRepository) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	return st, NewAlertRepository(st.DB())
}

func createAlertTestChannel(t *testing.T, repo *AlertRepository) NotificationChannel {
	t.Helper()
	channel := NotificationChannel{
		Name:    "ops webhook",
		Type:    AlertChannelWebhook,
		Config:  ChannelConfig{URLEnv: "NODESTEER_ALERT_TEST_WEBHOOK"},
		Enabled: true,
	}
	if err := repo.CreateChannel(context.Background(), &channel); err != nil {
		t.Fatalf("create channel: %v", err)
	}
	return channel
}

func createAlertTestRule(t *testing.T, repo *AlertRepository, channelID string) AlertRule {
	t.Helper()
	rule := testAlertRule()
	rule.Channels = []string{channelID}
	if err := repo.CreateAlertRule(context.Background(), &rule); err != nil {
		t.Fatalf("create rule: %v", err)
	}
	return rule
}

func TestAlertRepositoryRuleCRUDAndAudit(t *testing.T) {
	st, repo := newAlertTestRepository(t)
	ctx := WithAuditActor(context.Background(), "admin-id", "admin")
	channel := createAlertTestChannel(t, repo)
	rule := testAlertRule()
	rule.Channels = []string{channel.ID}
	if err := repo.CreateAlertRule(ctx, &rule); err != nil {
		t.Fatalf("create rule: %v", err)
	}
	if rule.ID == "" || rule.CreatedAt.IsZero() || rule.UpdatedAt.IsZero() {
		t.Fatalf("created rule = %+v", rule)
	}

	loaded, err := repo.GetAlertRule(ctx, rule.ID)
	if err != nil {
		t.Fatalf("get rule: %v", err)
	}
	if loaded.Name != rule.Name || len(loaded.Channels) != 1 || loaded.Channels[0] != channel.ID {
		t.Fatalf("loaded rule = %+v", loaded)
	}
	rules, err := repo.ListAlertRules(ctx)
	if err != nil || len(rules) != 1 {
		t.Fatalf("list rules = %d, %v", len(rules), err)
	}
	enabled, err := repo.EnabledAlertRules(ctx)
	if err != nil || len(enabled) != 1 {
		t.Fatalf("enabled rules = %d, %v", len(enabled), err)
	}

	loaded.Name = "High CPU (updated)"
	if err := repo.UpdateAlertRule(ctx, loaded); err != nil {
		t.Fatalf("update rule: %v", err)
	}
	loaded, err = repo.GetAlertRule(ctx, rule.ID)
	if err != nil || loaded.Name != "High CPU (updated)" {
		t.Fatalf("updated rule = %+v, %v", loaded, err)
	}

	if err := repo.SetAlertRuleEnabled(ctx, rule.ID, false); err != nil {
		t.Fatalf("disable rule: %v", err)
	}
	enabled, err = repo.EnabledAlertRules(ctx)
	if err != nil || len(enabled) != 0 {
		t.Fatalf("enabled after disable = %d, %v", len(enabled), err)
	}
	if err := repo.SetAlertRuleEnabled(ctx, rule.ID, true); err != nil {
		t.Fatalf("enable rule: %v", err)
	}

	if err := repo.DeleteAlertRule(ctx, rule.ID); err != nil {
		t.Fatalf("delete rule: %v", err)
	}
	if _, err := repo.GetAlertRule(ctx, rule.ID); !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("get deleted rule = %v", err)
	}

	var auditCount int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM audit_logs WHERE resource = 'alert_rule'`).Scan(&auditCount); err != nil {
		t.Fatalf("audit count: %v", err)
	}
	if auditCount < 5 {
		t.Fatalf("audit count = %d, want >= 5", auditCount)
	}
}

func TestAlertRepositoryChannelValidationAndSecretStorage(t *testing.T) {
	st, repo := newAlertTestRepository(t)
	channel := createAlertTestChannel(t, repo)

	var raw string
	if err := st.DB().QueryRow(`SELECT config_json FROM notification_channels WHERE id = ?`, channel.ID).Scan(&raw); err != nil {
		t.Fatalf("config: %v", err)
	}
	if strings.Contains(raw, "http://") || strings.Contains(raw, "https://") {
		t.Fatalf("channel config contains a literal URL: %s", raw)
	}
	var config map[string]any
	if err := json.Unmarshal([]byte(raw), &config); err != nil {
		t.Fatalf("decode config: %v", err)
	}
	if config["url_env"] != "NODESTEER_ALERT_TEST_WEBHOOK" {
		t.Fatalf("config = %#v", config)
	}

	literalSecret := NotificationChannel{
		Name: "bad", Type: AlertChannelWebhook, Enabled: true,
		Config: ChannelConfig{URLEnv: "https://example.test/hook?token=secret"},
	}
	if err := literalSecret.Validate(); err == nil {
		t.Fatal("literal URL must be rejected as an invalid env reference")
	}
	mixedFields := NotificationChannel{
		Name: "bad", Type: AlertChannelWebhook, Enabled: true,
		Config: ChannelConfig{URLEnv: "NODESTEER_ALERT_TEST_WEBHOOK", TokenEnv: "SECRET_TOKEN"},
	}
	if err := mixedFields.Validate(); err == nil {
		t.Fatal("unsupported config fields must be rejected")
	}

	channel.Name = "ops webhook updated"
	if err := repo.UpdateChannel(context.Background(), channel); err != nil {
		t.Fatalf("update channel: %v", err)
	}
	if err := repo.CreateChannel(context.Background(), &NotificationChannel{
		Name: channel.Name, Type: AlertChannelWebhook, Enabled: true,
		Config: ChannelConfig{URLEnv: "NODESTEER_ALERT_TEST_WEBHOOK"},
	}); !errors.Is(err, ErrChannelNameExists) {
		t.Fatalf("duplicate channel = %v", err)
	}

	rule := createAlertTestRule(t, repo, channel.ID)
	if err := repo.DeleteChannel(context.Background(), channel.ID); !errors.Is(err, ErrChannelInUse) {
		t.Fatalf("delete referenced channel = %v", err)
	}
	if err := repo.DeleteAlertRule(context.Background(), rule.ID); err != nil {
		t.Fatalf("delete rule: %v", err)
	}
	if err := repo.DeleteChannel(context.Background(), channel.ID); err != nil {
		t.Fatalf("delete channel: %v", err)
	}
}

func TestAlertRepositoryDecisionPersistenceAndNotifications(t *testing.T) {
	st, repo := newAlertTestRepository(t)
	channel := createAlertTestChannel(t, repo)
	rule := createAlertTestRule(t, repo, channel.ID)
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

	decision := AlertDecision{
		State: AlertState{
			RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
			Status: AlertStatusPending, ConditionSince: now, LastEvaluatedAt: now, UpdatedAt: now,
		},
		Event: &AlertEvent{
			RuleID: rule.ID, RuleName: rule.Name, NodeID: "node-1", NodeHostname: "node-1",
			Metric: rule.Metric, Series: rule.Series, Operator: rule.Operator,
			Status: AlertStatusPending, Severity: rule.Severity, Value: float64Ptr(95),
			Threshold: rule.Threshold, StartedAt: now, CreatedAt: now, UpdatedAt: now,
		},
	}
	if err := repo.ApplyDecision(context.Background(), rule, decision); err != nil {
		t.Fatalf("apply pending: %v", err)
	}
	states, err := repo.ListAlertStates(context.Background(), rule.ID)
	if err != nil || len(states) != 1 {
		t.Fatalf("states = %d, %v", len(states), err)
	}
	if states[0].EventID == "" {
		t.Fatal("state did not receive event id")
	}
	event, err := repo.GetAlertEvent(context.Background(), states[0].EventID)
	if err != nil {
		t.Fatalf("get event: %v", err)
	}
	if event.Status != AlertStatusPending {
		t.Fatalf("event = %+v", event)
	}

	// 触发：写通知发件箱。
	decision = AlertDecision{
		State: AlertState{
			RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
			Status: AlertStatusFiring, ConditionSince: now, LastEvaluatedAt: now.Add(time.Minute),
			LastNotifiedAt: now.Add(time.Minute), EventID: event.ID, UpdatedAt: now.Add(time.Minute),
		},
		Event: func() *AlertEvent {
			copied := event
			copied.Status = AlertStatusFiring
			copied.FiredAt = now.Add(time.Minute)
			copied.Notified = true
			copied.UpdatedAt = now.Add(time.Minute)
			return &copied
		}(),
		Notify: true,
	}
	if err := repo.ApplyDecision(context.Background(), rule, decision); err != nil {
		t.Fatalf("apply firing: %v", err)
	}
	var notifications int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM alert_notifications WHERE event_id = ? AND status = 'pending'`, event.ID).Scan(&notifications); err != nil {
		t.Fatalf("notifications: %v", err)
	}
	if notifications != 1 {
		t.Fatalf("notifications = %d, want 1", notifications)
	}

	// 恢复：再写一条恢复通知。
	decision = AlertDecision{
		State: AlertState{
			RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
			Status: AlertStatusResolved, LastEvaluatedAt: now.Add(2 * time.Minute),
			UpdatedAt: now.Add(2 * time.Minute),
		},
		Event: func() *AlertEvent {
			copied := event
			copied.Status = AlertStatusResolved
			copied.ResolvedAt = now.Add(2 * time.Minute)
			copied.UpdatedAt = now.Add(2 * time.Minute)
			return &copied
		}(),
		Notify: true,
	}
	if err := repo.ApplyDecision(context.Background(), rule, decision); err != nil {
		t.Fatalf("apply resolved: %v", err)
	}
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM alert_notifications WHERE event_id = ?`, event.ID).Scan(&notifications); err != nil {
		t.Fatalf("notifications: %v", err)
	}
	if notifications != 2 {
		t.Fatalf("notifications after resolve = %d, want 2", notifications)
	}
	states, err = repo.ListAlertStates(context.Background(), rule.ID)
	if err != nil || len(states) != 1 || states[0].Status != AlertStatusResolved || states[0].EventID != "" {
		t.Fatalf("resolved state = %+v, %v", states, err)
	}
}

func TestAlertRepositoryUpdateDisablesActiveEvent(t *testing.T) {
	_, repo := newAlertTestRepository(t)
	channel := createAlertTestChannel(t, repo)
	rule := createAlertTestRule(t, repo, channel.ID)
	now := time.Now().UTC()
	decision := AlertDecision{
		State: AlertState{
			RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
			Status: AlertStatusFiring, ConditionSince: now, LastEvaluatedAt: now,
			LastNotifiedAt: now, UpdatedAt: now,
		},
		Event: &AlertEvent{
			RuleID: rule.ID, RuleName: rule.Name, NodeID: "node-1", NodeHostname: "node-1",
			Metric: rule.Metric, Series: rule.Series, Operator: rule.Operator,
			Status: AlertStatusFiring, Severity: rule.Severity, Value: float64Ptr(95),
			Threshold: rule.Threshold, StartedAt: now, FiredAt: now, Notified: true,
			CreatedAt: now, UpdatedAt: now,
		},
		Notify: true,
	}
	if err := repo.ApplyDecision(context.Background(), rule, decision); err != nil {
		t.Fatalf("apply firing: %v", err)
	}
	rule.Enabled = false
	if err := repo.UpdateAlertRule(context.Background(), rule); err != nil {
		t.Fatalf("disable via update: %v", err)
	}
	active, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "active"})
	if err != nil || total != 0 || len(active) != 0 {
		t.Fatalf("active after disable = %d/%d, %v", len(active), total, err)
	}
	history, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "history"})
	if err != nil || total != 1 || history[0].Status != AlertStatusResolved {
		t.Fatalf("history after disable = %+v, %v", history, err)
	}
	var pending int
	if err := repo.db.QueryRow(`SELECT COUNT(*) FROM alert_notifications WHERE status = 'pending'`).Scan(&pending); err != nil || pending != 0 {
		t.Fatalf("pending notifications after disable = %d, %v", pending, err)
	}
}

func TestAlertRepositoryEventsFilterAckAndSilence(t *testing.T) {
	st, repo := newAlertTestRepository(t)
	channel := createAlertTestChannel(t, repo)
	rule := createAlertTestRule(t, repo, channel.ID)
	now := time.Now().UTC().Truncate(time.Second)

	decision := AlertDecision{
		State: AlertState{
			RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
			Status: AlertStatusFiring, ConditionSince: now, LastEvaluatedAt: now, UpdatedAt: now,
		},
		Event: &AlertEvent{
			RuleID: rule.ID, RuleName: rule.Name, NodeID: "node-1", NodeHostname: "node-1",
			Metric: rule.Metric, Series: rule.Series, Operator: rule.Operator,
			Status: AlertStatusFiring, Severity: AlertSeverityCritical, Value: float64Ptr(95),
			Threshold: rule.Threshold, StartedAt: now, FiredAt: now, Notified: false,
			CreatedAt: now, UpdatedAt: now,
		},
	}
	if err := repo.ApplyDecision(context.Background(), rule, decision); err != nil {
		t.Fatalf("apply firing: %v", err)
	}
	active, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "active"})
	if err != nil || total != 1 || len(active) != 1 {
		t.Fatalf("active events = %d/%d, %v", len(active), total, err)
	}
	history, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{State: "history"})
	if err != nil || total != 0 || len(history) != 0 {
		t.Fatalf("history events = %d/%d, %v", len(history), total, err)
	}
	filtered, total, err := repo.ListAlertEvents(context.Background(), AlertEventFilter{
		State: "active", RuleID: rule.ID, NodeID: "node-1", Severity: AlertSeverityCritical,
	})
	if err != nil || total != 1 || len(filtered) != 1 {
		t.Fatalf("filtered events = %d/%d, %v", len(filtered), total, err)
	}

	eventID := active[0].ID
	if err := repo.AckAlertEvent(context.Background(), eventID, "admin-id", "admin", now.Add(time.Minute)); err != nil {
		t.Fatalf("ack: %v", err)
	}
	event, err := repo.GetAlertEvent(context.Background(), eventID)
	if err != nil || event.AcknowledgedBy != "admin" || event.AcknowledgedAt.IsZero() {
		t.Fatalf("acked event = %+v, %v", event, err)
	}

	until := now.Add(10 * time.Minute)
	if err := repo.SetAlertRuleSilence(context.Background(), rule.ID, until); err != nil {
		t.Fatalf("silence: %v", err)
	}
	event, err = repo.GetAlertEvent(context.Background(), eventID)
	if err != nil || event.Status != AlertStatusSilenced {
		t.Fatalf("silenced event = %+v, %v", event, err)
	}
	states, err := repo.ListAlertStates(context.Background(), rule.ID)
	if err != nil || len(states) != 1 || states[0].Status != AlertStatusSilenced {
		t.Fatalf("silenced state = %+v, %v", states, err)
	}
	if err := repo.ClearAlertRuleSilence(context.Background(), rule.ID); err != nil {
		t.Fatalf("unsilence: %v", err)
	}
	event, err = repo.GetAlertEvent(context.Background(), eventID)
	if err != nil || event.Status != AlertStatusFiring {
		t.Fatalf("unsilenced event = %+v, %v", event, err)
	}

	// 已解决事件不能再确认。
	if _, err := st.DB().Exec(`UPDATE alert_events SET status = 'resolved', resolved_at = ? WHERE id = ?`, formatAlertTime(now), eventID); err != nil {
		t.Fatalf("mark resolved: %v", err)
	}
	if err := repo.AckAlertEvent(context.Background(), eventID, "admin-id", "admin", now); !errors.Is(err, ErrAlertConflict) {
		t.Fatalf("ack resolved = %v", err)
	}
}
