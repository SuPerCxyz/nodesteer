package api

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

func newAlertAPITestServer(t *testing.T) (*Server, *monitoring.AlertRepository) {
	t.Helper()
	s, st := newMeTestServer(t)
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	repo := monitoring.NewAlertRepository(st.DB())
	notifier := monitoring.NewAlertNotifier(repo, logger)
	s.SetAlerting(repo, notifier)
	seedMonitoringUsers(t, st)
	createMonitoringNode(t, st, "node-1")
	return s, repo
}

func decodeAlertRule(t *testing.T, rec *httptest.ResponseRecorder) monitoring.AlertRule {
	t.Helper()
	var rule monitoring.AlertRule
	if err := json.Unmarshal(rec.Body.Bytes(), &rule); err != nil {
		t.Fatalf("decode rule: %v (%s)", err, rec.Body.String())
	}
	return rule
}

func decodeAlertChannel(t *testing.T, rec *httptest.ResponseRecorder) monitoring.NotificationChannel {
	t.Helper()
	var channel monitoring.NotificationChannel
	if err := json.Unmarshal(rec.Body.Bytes(), &channel); err != nil {
		t.Fatalf("decode channel: %v (%s)", err, rec.Body.String())
	}
	return channel
}

func TestAlertRuleAndChannelAPIValidationAndPermissions(t *testing.T) {
	s, _ := newAlertAPITestServer(t)
	handler := s.Routes()
	viewer := loginAs(t, s, "viewer", "password")
	admin := loginAs(t, s, "admin", "password")

	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/rules", "", ""); rec.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated rules = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/rules", viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer rules = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels", viewer, `{}`); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer channel create = %d", rec.Code)
	}

	// 字面量 secret 字段必须被拒绝，只接受 *_env 引用。
	literalSecret := `{"name":"bad","type":"webhook","config":{"url":"http://example.test/?token=secret"},"enabled":true}`
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels", admin, literalSecret); rec.Code != http.StatusBadRequest {
		t.Fatalf("literal secret channel = %d %s", rec.Code, rec.Body.String())
	}

	t.Setenv("NODESTEER_ALERT_API_WEBHOOK", "http://127.0.0.1:1/secret-webhook-token")
	channelBody := `{"name":"ops webhook","type":"webhook","config":{"url_env":"NODESTEER_ALERT_API_WEBHOOK","timeout_sec":5},"enabled":true}`
	rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels", admin, channelBody)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create channel = %d %s", rec.Code, rec.Body.String())
	}
	channel := decodeAlertChannel(t, rec)
	if strings.Contains(rec.Body.String(), "secret-webhook-token") || strings.Contains(rec.Body.String(), "127.0.0.1") {
		t.Fatalf("channel response leaked secret: %s", rec.Body.String())
	}
	rec = doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/notification-channels/"+channel.ID, admin, "")
	if rec.Code != http.StatusOK || strings.Contains(rec.Body.String(), "secret-webhook-token") {
		t.Fatalf("get channel = %d %s", rec.Code, rec.Body.String())
	}
	duplicate := `{"name":"ops webhook","type":"webhook","config":{"url_env":"NODESTEER_ALERT_API_WEBHOOK"},"enabled":true}`
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels", admin, duplicate); rec.Code != http.StatusConflict {
		t.Fatalf("duplicate channel = %d %s", rec.Code, rec.Body.String())
	}

	ruleBody := `{"name":"High CPU","node_id":"node-1","metric":"cpu.usage","series":"cpu.usage|cpu=all","operator":"gt","threshold":90,"duration_sec":60,"recovery_sec":30,"cooldown_sec":300,"severity":"critical","channels":["` + channel.ID + `"],"enabled":true}`
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/rules", admin, ruleBody)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create rule = %d %s", rec.Code, rec.Body.String())
	}
	rule := decodeAlertRule(t, rec)
	if rule.ID == "" || rule.Name != "High CPU" || len(rule.Channels) != 1 {
		t.Fatalf("rule = %+v", rule)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/rules", admin,
		`{"name":"bad","metric":"cpu.usage","operator":"contains","threshold":1,"severity":"warning"}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid rule = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/rules", admin,
		`{"name":"missing node","node_id":"missing","metric":"cpu.usage","operator":"gt","threshold":1,"severity":"warning"}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("missing node rule = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/rules", admin,
		`{"name":"missing channel","node_id":"node-1","metric":"cpu.usage","operator":"gt","threshold":1,"severity":"warning","channels":["missing"]}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("missing channel rule = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/rules/"+rule.ID, admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("get rule = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/rules/missing", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("missing rule = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/rules/"+rule.ID+"/unknown", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("unknown subpath = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/notification-channels/"+channel.ID, admin, ""); rec.Code != http.StatusConflict {
		t.Fatalf("delete referenced channel = %d %s", rec.Code, rec.Body.String())
	}

	updated := `{"name":"High CPU updated","node_id":"node-1","metric":"cpu.usage","series":"cpu.usage|cpu=all","operator":"gt","threshold":91,"duration_sec":60,"recovery_sec":30,"cooldown_sec":300,"severity":"warning","channels":["` + channel.ID + `"],"enabled":true}`
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/alerts/rules/"+rule.ID, admin, updated); rec.Code != http.StatusOK {
		t.Fatalf("update rule = %d %s", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/rules/"+rule.ID+"/enable", admin, `{"enabled":false}`); rec.Code != http.StatusOK {
		t.Fatalf("disable rule = %d %s", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/rules/"+rule.ID+"/enable", admin, `{"enabled":true}`); rec.Code != http.StatusOK {
		t.Fatalf("enable rule = %d %s", rec.Code, rec.Body.String())
	}
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/rules/"+rule.ID+"/silence", admin, `{"duration_sec":60}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("silence rule = %d %s", rec.Code, rec.Body.String())
	}
	silenced := decodeAlertRule(t, rec)
	if silenced.SilenceUntil == nil {
		t.Fatal("rule was not silenced")
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/alerts/rules/"+rule.ID+"/silence", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("unsilence rule = %d %s", rec.Code, rec.Body.String())
	}

	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/alerts/rules/"+rule.ID, admin, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("delete rule = %d %s", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/notification-channels/"+channel.ID, admin, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("delete channel = %d %s", rec.Code, rec.Body.String())
	}
}

func TestAlertChannelTestEndpointAndSecretSafety(t *testing.T) {
	s, _ := newAlertAPITestServer(t)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")

	var captured map[string]any
	success := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(raw, &captured)
		w.WriteHeader(http.StatusOK)
	}))
	defer success.Close()
	t.Setenv("NODESTEER_ALERT_API_TEST_OK", success.URL)
	rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels", admin,
		`{"name":"test ok","type":"webhook","config":{"url_env":"NODESTEER_ALERT_API_TEST_OK"},"enabled":true}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create channel = %d %s", rec.Code, rec.Body.String())
	}
	channel := decodeAlertChannel(t, rec)
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels/"+channel.ID+"/test", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("test channel = %d %s", rec.Code, rec.Body.String())
	}
	if captured["event"] != "firing" {
		t.Fatalf("test payload = %#v", captured)
	}

	failure := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":"api-super-secret-token"}`))
	}))
	defer failure.Close()
	t.Setenv("NODESTEER_ALERT_API_TEST_FAIL", failure.URL+"/private-path")
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels", admin,
		`{"name":"test fail","type":"webhook","config":{"url_env":"NODESTEER_ALERT_API_TEST_FAIL"},"enabled":true}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create failing channel = %d %s", rec.Code, rec.Body.String())
	}
	failing := decodeAlertChannel(t, rec)
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels/"+failing.ID+"/test", admin, "")
	if rec.Code != http.StatusBadGateway {
		t.Fatalf("failing test = %d %s", rec.Code, rec.Body.String())
	}
	for _, secret := range []string{"api-super-secret-token", failure.URL, "private-path"} {
		if strings.Contains(rec.Body.String(), secret) {
			t.Fatalf("test error leaked %q: %s", secret, rec.Body.String())
		}
	}

	// 环境变量未配置属于配置错误，返回 400；不得回显变量值或渠道配置。
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels", admin,
		`{"name":"test missing env","type":"webhook","config":{"url_env":"NODESTEER_ALERT_API_MISSING_ENV"},"enabled":true}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create missing-env channel = %d %s", rec.Code, rec.Body.String())
	}
	missing := decodeAlertChannel(t, rec)
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/notification-channels/"+missing.ID+"/test", admin, "")
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("missing-env test = %d %s", rec.Code, rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), "NODESTEER_ALERT_API_MISSING_ENV") {
		t.Fatalf("test error leaked the environment variable name: %s", rec.Body.String())
	}
}

func TestAlertEventsAPIListFilterAck(t *testing.T) {
	s, repo := newAlertAPITestServer(t)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	channel := monitoring.NotificationChannel{
		Name: "events webhook", Type: monitoring.AlertChannelWebhook, Enabled: true,
		Config: monitoring.ChannelConfig{URLEnv: "NODESTEER_ALERT_API_EVENTS"},
	}
	if err := repo.CreateChannel(context.Background(), &channel); err != nil {
		t.Fatalf("create channel: %v", err)
	}
	rule := monitoring.AlertRule{
		Name: "events rule", NodeID: "node-1", Metric: "cpu.usage", Series: "cpu.usage|cpu=all",
		Operator: monitoring.AlertOperatorGT, Threshold: 90, Severity: monitoring.AlertSeverityCritical,
		Channels: []string{channel.ID}, Enabled: true,
	}
	if err := repo.CreateAlertRule(context.Background(), &rule); err != nil {
		t.Fatalf("create rule: %v", err)
	}
	now := time.Now().UTC()
	value := 95.0
	decision := monitoring.AlertDecision{
		State: monitoring.AlertState{
			RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
			Status: monitoring.AlertStatusFiring, ConditionSince: now, LastEvaluatedAt: now, UpdatedAt: now,
		},
		Event: &monitoring.AlertEvent{
			RuleID: rule.ID, RuleName: rule.Name, NodeID: "node-1", NodeHostname: "node-1",
			Metric: rule.Metric, Series: rule.Series, Operator: rule.Operator,
			Status: monitoring.AlertStatusFiring, Severity: monitoring.AlertSeverityCritical,
			Value: &value, Threshold: rule.Threshold, StartedAt: now, FiredAt: now,
			CreatedAt: now, UpdatedAt: now,
		},
	}
	if err := repo.ApplyDecision(context.Background(), rule, decision); err != nil {
		t.Fatalf("apply event: %v", err)
	}

	rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/events?state=active", admin, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("active events = %d %s", rec.Code, rec.Body.String())
	}
	var list struct {
		Events []monitoring.AlertEvent `json:"events"`
		Total  int                     `json:"total"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &list); err != nil || list.Total != 1 || len(list.Events) != 1 {
		t.Fatalf("active events = %+v, %v", list, err)
	}
	eventID := list.Events[0].ID
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/events?state=history", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("history events = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/events?state=bogus", admin, ""); rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid state = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/events?limit=500", admin, ""); rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid limit = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/events?rule_id="+rule.ID+"&node_id=node-1&severity=critical", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("filtered events = %d", rec.Code)
	}
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/events/"+eventID+"/ack", admin, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("ack = %d %s", rec.Code, rec.Body.String())
	}
	var acked monitoring.AlertEvent
	if err := json.Unmarshal(rec.Body.Bytes(), &acked); err != nil || acked.AcknowledgedBy != "admin" || acked.AcknowledgedAt.IsZero() {
		t.Fatalf("acked event = %+v, %v", acked, err)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/events/missing/ack", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("missing ack = %d", rec.Code)
	}
}

func TestAlertNotificationsAPIListFilterPermissionsAndRedaction(t *testing.T) {
	s, repo := newAlertAPITestServer(t)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	viewer := loginAs(t, s, "viewer", "password")
	ctx := context.Background()

	channel := monitoring.NotificationChannel{
		Name: "notification history", Type: monitoring.AlertChannelWebhook, Enabled: true,
		Config: monitoring.ChannelConfig{URLEnv: "NODESTEER_ALERT_API_NOTIFICATION_HISTORY"},
	}
	if err := repo.CreateChannel(ctx, &channel); err != nil {
		t.Fatalf("create channel: %v", err)
	}
	rule := monitoring.AlertRule{
		Name: "notification rule", NodeID: "node-1", Metric: "cpu.usage", Series: "cpu.usage|cpu=all",
		Operator: monitoring.AlertOperatorGT, Threshold: 90, Severity: monitoring.AlertSeverityCritical,
		Channels: []string{channel.ID}, Enabled: true,
	}
	if err := repo.CreateAlertRule(ctx, &rule); err != nil {
		t.Fatalf("create rule: %v", err)
	}
	now := time.Now().UTC()
	value := 95.0
	if err := repo.ApplyDecision(ctx, rule, monitoring.AlertDecision{
		State: monitoring.AlertState{
			RuleID: rule.ID, NodeID: "node-1", Metric: rule.Metric, Series: rule.Series,
			Status: monitoring.AlertStatusFiring, ConditionSince: now, LastEvaluatedAt: now,
			LastNotifiedAt: now, UpdatedAt: now,
		},
		Event: &monitoring.AlertEvent{
			RuleID: rule.ID, RuleName: rule.Name, NodeID: "node-1", NodeHostname: "node-1",
			Metric: rule.Metric, Series: rule.Series, Operator: rule.Operator,
			Status: monitoring.AlertStatusFiring, Severity: monitoring.AlertSeverityCritical,
			Value: &value, Threshold: rule.Threshold, StartedAt: now, FiredAt: now,
			CreatedAt: now, UpdatedAt: now,
		},
		Notify: true,
	}); err != nil {
		t.Fatalf("apply event: %v", err)
	}
	events, total, err := repo.ListAlertEvents(ctx, monitoring.AlertEventFilter{State: "active", RuleID: rule.ID})
	if err != nil || total != 1 || len(events) != 1 {
		t.Fatalf("events = %d/%d, %v", total, len(events), err)
	}
	eventID := events[0].ID
	records, _, err := repo.ListNotifications(ctx, monitoring.AlertNotificationFilter{EventID: eventID})
	if err != nil || len(records) != 1 {
		t.Fatalf("notifications = %d, %v", len(records), err)
	}
	notificationID := records[0].ID
	if err := repo.RecordNotificationAttempt(ctx, notificationID, 1, "failed",
		"https://secret.example/token=abc", true, time.Time{}, now); err != nil {
		t.Fatalf("record attempt: %v", err)
	}

	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/notifications", "", ""); rec.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/notifications", viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/alerts/notifications", admin, ""); rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("post = %d", rec.Code)
	}

	path := "/api/monitoring/alerts/notifications?event_id=" + eventID + "&channel_id=" + channel.ID + "&status=failed&limit=10&offset=0"
	rec := doMonitoringRequest(t, handler, http.MethodGet, path, admin, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("list = %d %s", rec.Code, rec.Body.String())
	}
	var list struct {
		Notifications []monitoring.AlertNotificationRecord `json:"notifications"`
		Total         int                                  `json:"total"`
		Limit         int                                  `json:"limit"`
		Offset        int                                  `json:"offset"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &list); err != nil {
		t.Fatalf("decode: %v (%s)", err, rec.Body.String())
	}
	if list.Total != 1 || len(list.Notifications) != 1 || list.Limit != 10 || list.Offset != 0 {
		t.Fatalf("list = %+v", list)
	}
	record := list.Notifications[0]
	if record.ID != notificationID || record.EventID != eventID || record.ChannelID != channel.ID ||
		record.Status != "failed" || record.Attempts != 1 {
		t.Fatalf("record = %+v", record)
	}
	if len(record.AttemptDetails) != 1 || record.AttemptDetails[0].Attempt != 1 ||
		record.AttemptDetails[0].Status != "failed" || record.AttemptDetails[0].AttemptedAt.IsZero() {
		t.Fatalf("attempt details = %+v", record.AttemptDetails)
	}
	for _, secret := range []string{"secret.example", "token=abc", "http://", "https://"} {
		if strings.Contains(rec.Body.String(), secret) {
			t.Fatalf("response leaked %q: %s", secret, rec.Body.String())
		}
	}
	if record.LastError != "notification failed" || record.AttemptDetails[0].Error != "notification failed" {
		t.Fatalf("errors were not normalized: %+v / %+v", record.LastError, record.AttemptDetails[0])
	}

	rec = doMonitoringRequest(t, handler, http.MethodGet,
		"/api/monitoring/alerts/notifications?event_id="+eventID+"&status=pending", admin, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("empty filter = %d %s", rec.Code, rec.Body.String())
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &list); err != nil || list.Total != 0 || len(list.Notifications) != 0 {
		t.Fatalf("empty filter = %+v, %v", list, err)
	}
	if !strings.Contains(rec.Body.String(), `"notifications":[]`) {
		t.Fatalf("empty notifications should be an array: %s", rec.Body.String())
	}

	rec = doMonitoringRequest(t, handler, http.MethodGet,
		"/api/monitoring/alerts/notifications?limit=1&offset=1", admin, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("page 2 = %d %s", rec.Code, rec.Body.String())
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &list); err != nil || list.Total != 1 || len(list.Notifications) != 0 {
		t.Fatalf("page 2 = %+v, %v", list, err)
	}

	for _, invalid := range []string{
		"?status=bogus",
		"?limit=0",
		"?limit=500",
		"?offset=-1",
		"?unknown=1",
		"?event_id=" + strings.Repeat("x", 129),
	} {
		if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/notifications"+invalid, admin, ""); rec.Code != http.StatusBadRequest {
			t.Fatalf("invalid %s = %d %s", invalid, rec.Code, rec.Body.String())
		}
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/notifications?event_id=missing", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("missing event = %d", rec.Code)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/alerts/notifications?channel_id=missing", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("missing channel = %d", rec.Code)
	}
}
