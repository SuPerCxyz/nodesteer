package api

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

func TestMonitoringRuleCRUDValidationAndPermissions(t *testing.T) {
	s, st := newMonitoringAPI(t, true)
	seedMonitoringUsers(t, st)
	createMonitoringSchedule(t, st, "schedule-1")
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	viewer := loginAs(t, s, "viewer", "password")

	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := s.monitoring.Create(t.Context(), &target); err != nil {
		t.Fatal(err)
	}

	validBody := `{"name":"rule-1","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"200","duration_sec":0,"cooldown_sec":0,"enabled":true}`
	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules", viewer, validBody); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer create = %d %s, want 403", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/rules", viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer list = %d %s, want 403", rec.Code, rec.Body.String())
	}

	invalidBodies := map[string]string{
		"empty body":           "",
		"malformed body":       `{"name":`,
		"missing schedule":     `{"name":"rule","target_id":"` + target.ID + `","operator":"status_eq","expected":"200","enabled":true}`,
		"missing target":       `{"name":"rule","target_id":"missing","schedule_id":"schedule-1","operator":"status_eq","expected":"200","enabled":true}`,
		"missing schedule ref": `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"missing","operator":"status_eq","expected":"200","enabled":true}`,
		"client supplied id":   `{"id":"client","name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"200","enabled":true}`,
		"invalid operator":     `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"regex","expected":"200","enabled":true}`,
		"status too low":       `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"99","enabled":true}`,
		"status too high":      `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"600","enabled":true}`,
		"status not numeric":   `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"ok","enabled":true}`,
		"invalid threshold":    `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"lt","expected":"NaN","enabled":true}`,
		"invalid expiry":       `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"expires_within","expected":"-1s","enabled":true}`,
		"negative duration":    `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"200","duration_sec":-1,"enabled":true}`,
		"duration too large":   `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"200","duration_sec":86401,"enabled":true}`,
		"negative cooldown":    `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"200","cooldown_sec":-1,"enabled":true}`,
		"cooldown too large":   `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"200","cooldown_sec":604801,"enabled":true}`,
		"batch too large":      `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"200","batch_size":21,"enabled":true}`,
	}
	for name, body := range invalidBodies {
		t.Run(name, func(t *testing.T) {
			if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules", admin, body); rec.Code != http.StatusBadRequest {
				t.Fatalf("POST %s = %d %s, want 400", name, rec.Code, rec.Body.String())
			}
		})
	}

	rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules", admin, validBody)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create = %d %s", rec.Code, rec.Body.String())
	}
	rule := decodeResource[monitoring.Rule](t, rec)
	if rule.ID == "" || rule.Name != "rule-1" || !rule.Enabled {
		t.Fatalf("created rule = %+v", rule)
	}

	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/rules", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("list = %d %s", rec.Code, rec.Body.String())
	} else if got := decodeResource[[]monitoring.Rule](t, rec); len(got) != 1 || got[0].ID != rule.ID {
		t.Fatalf("rules = %+v", got)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/rules/"+rule.ID, admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("get = %d %s", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/rules/"+rule.ID, viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer get = %d %s, want 403", rec.Code, rec.Body.String())
	}

	updateBody := `{"name":"rule-1-updated","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"201","duration_sec":5,"cooldown_sec":10,"enabled":true}`
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/rules/"+rule.ID, admin, updateBody); rec.Code != http.StatusOK {
		t.Fatalf("update = %d %s", rec.Code, rec.Body.String())
	}
	rec = doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/rules/"+rule.ID, admin, "")
	updated := decodeResource[monitoring.Rule](t, rec)
	if updated.Name != "rule-1-updated" || updated.Expected != "201" || updated.DurationSec != 5 || updated.CooldownSec != 10 {
		t.Fatalf("updated rule = %+v", updated)
	}

	disableBody := `{"name":"rule-1-updated","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"status_eq","expected":"201","duration_sec":5,"cooldown_sec":10,"enabled":false}`
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/rules/"+rule.ID, admin, disableBody); rec.Code != http.StatusOK {
		t.Fatalf("disable = %d %s", rec.Code, rec.Body.String())
	}
	rec = doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/rules/"+rule.ID, admin, "")
	if got := decodeResource[monitoring.Rule](t, rec); got.Enabled {
		t.Fatalf("rule not disabled: %+v", got)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/rules/"+rule.ID, admin, updateBody); rec.Code != http.StatusOK {
		t.Fatalf("enable = %d %s", rec.Code, rec.Body.String())
	}
	rec = doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/rules/"+rule.ID, admin, "")
	if got := decodeResource[monitoring.Rule](t, rec); !got.Enabled {
		t.Fatalf("rule not re-enabled: %+v", got)
	}

	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/rules/"+rule.ID, viewer, updateBody); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer update = %d %s, want 403", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/rules/"+rule.ID, admin, `{"name":"rule","target_id":"`+target.ID+`","schedule_id":"schedule-1","operator":"regex","expected":"200","enabled":true}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid update = %d %s, want 400", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/rules/"+rule.ID, admin, `{"name":"rule","target_id":"`+target.ID+`","schedule_id":"missing","operator":"status_eq","expected":"200","enabled":true}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("unknown schedule update = %d %s, want 400", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/rules/missing", admin, updateBody); rec.Code != http.StatusNotFound {
		t.Fatalf("update missing = %d %s, want 404", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/rules/missing", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("delete missing = %d %s, want 404", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/rules/"+rule.ID, viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer delete = %d %s, want 403", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/rules/"+rule.ID, admin, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("delete = %d %s, want 204", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/rules/"+rule.ID, admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("get deleted = %d %s, want 404", rec.Code, rec.Body.String())
	}

	var count int
	for action, want := range map[string]int{"create": 1, "update": 3, "delete": 1} {
		if err := st.DB().QueryRow(`SELECT COUNT(*) FROM audit_logs WHERE resource = 'monitoring_rule' AND action = ? AND username = 'admin' AND resource_id = ?`, action, rule.ID).Scan(&count); err != nil || count != want {
			t.Fatalf("audit %s count = %d, err = %v, want %d", action, count, err, want)
		}
	}
}

func TestMonitoringResolveUnknownFailedAndUnknown(t *testing.T) {
	s, st := newMonitoringAPI(t, true)
	seedMonitoringUsers(t, st)
	createMonitoringSchedule(t, st, "schedule-1")
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	viewer := loginAs(t, s, "viewer", "password")

	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := s.monitoring.Create(t.Context(), &target); err != nil {
		t.Fatal(err)
	}

	for _, status := range []string{"unknown", "failed"} {
		t.Run(status, func(t *testing.T) {
			rule := monitoring.Rule{Name: "rule-" + status, TargetID: target.ID, ScheduleID: "schedule-1", Operator: "status_eq", Expected: "200", Enabled: true}
			if err := s.monitoring.CreateRule(t.Context(), &rule); err != nil {
				t.Fatal(err)
			}
			eventID := "event-" + status
			if _, err := st.DB().Exec(`UPDATE monitoring_rules SET pending_id = ? WHERE id = ?`, eventID, rule.ID); err != nil {
				t.Fatal(err)
			}
			if _, err := st.DB().Exec(`INSERT INTO monitoring_rule_events (id, rule_id, target_id, observed_at, status) VALUES (?, ?, ?, ?, ?)`, eventID, rule.ID, target.ID, time.Now().UTC().Format(time.RFC3339Nano), status); err != nil {
				t.Fatal(err)
			}

			if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/"+rule.ID+"/resolve-unknown", viewer, ""); rec.Code != http.StatusForbidden {
				t.Fatalf("viewer resolve = %d %s, want 403", rec.Code, rec.Body.String())
			}
			if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/"+rule.ID+"/resolve-unknown", admin, ""); rec.Code != http.StatusNoContent {
				t.Fatalf("resolve %s = %d %s, want 204", status, rec.Code, rec.Body.String())
			}
			var gotStatus, pending string
			if err := st.DB().QueryRow(`SELECT status FROM monitoring_rule_events WHERE id = ?`, eventID).Scan(&gotStatus); err != nil {
				t.Fatal(err)
			}
			if err := st.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id = ?`, rule.ID).Scan(&pending); err != nil {
				t.Fatal(err)
			}
			wantStatus := "acknowledged_" + status
			if gotStatus != wantStatus || pending != "" {
				t.Fatalf("after resolve: status=%q pending=%q, want %q and empty", gotStatus, pending, wantStatus)
			}
			var detail string
			if err := st.DB().QueryRow(`SELECT detail FROM audit_logs WHERE resource = 'monitoring_rule' AND action = 'resolve_unknown' AND username = 'admin' AND resource_id = ?`, rule.ID).Scan(&detail); err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(detail, "event_id="+eventID) || !strings.Contains(detail, "previous_status="+status) {
				t.Fatalf("resolve audit detail=%q", detail)
			}
		})
	}

	t.Run("no unresolved execution", func(t *testing.T) {
		rule := monitoring.Rule{Name: "rule-clear", TargetID: target.ID, ScheduleID: "schedule-1", Operator: "status_eq", Expected: "200", Enabled: true}
		if err := s.monitoring.CreateRule(t.Context(), &rule); err != nil {
			t.Fatal(err)
		}
		rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/"+rule.ID+"/resolve-unknown", admin, "")
		if rec.Code != http.StatusConflict || strings.Contains(rec.Body.String(), "sql:") {
			t.Fatalf("resolve without pending = %d %s, want 409 without SQL details", rec.Code, rec.Body.String())
		}
	})

	t.Run("pending event remains unresolved", func(t *testing.T) {
		rule := monitoring.Rule{Name: "rule-pending", TargetID: target.ID, ScheduleID: "schedule-1", Operator: "status_eq", Expected: "200", Enabled: true}
		if err := s.monitoring.CreateRule(t.Context(), &rule); err != nil {
			t.Fatal(err)
		}
		eventID := "event-pending"
		if _, err := st.DB().Exec(`UPDATE monitoring_rules SET pending_id = ? WHERE id = ?`, eventID, rule.ID); err != nil {
			t.Fatal(err)
		}
		if _, err := st.DB().Exec(`INSERT INTO monitoring_rule_events (id, rule_id, target_id, observed_at, status) VALUES (?, ?, ?, ?, 'pending')`, eventID, rule.ID, target.ID, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
			t.Fatal(err)
		}
		rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/"+rule.ID+"/resolve-unknown", admin, "")
		if rec.Code != http.StatusConflict {
			t.Fatalf("resolve pending = %d %s, want 409", rec.Code, rec.Body.String())
		}
	})

	t.Run("unknown rule", func(t *testing.T) {
		rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/missing/resolve-unknown", admin, "")
		if rec.Code != http.StatusNotFound || strings.Contains(rec.Body.String(), "sql:") {
			t.Fatalf("resolve unknown rule = %d %s, want 404 without SQL details", rec.Code, rec.Body.String())
		}
	})
}

func TestMonitoringRuleDeleteRejectsPendingExecution(t *testing.T) {
	s, st := newMonitoringAPI(t, true)
	seedMonitoringUsers(t, st)
	createMonitoringSchedule(t, st, "schedule-1")
	admin := loginAs(t, s, "admin", "password")

	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := s.monitoring.Create(t.Context(), &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "pending-rule", TargetID: target.ID, ScheduleID: "schedule-1", Operator: "status_eq", Expected: "200", Enabled: true}
	if err := s.monitoring.CreateRule(t.Context(), &rule); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`UPDATE monitoring_rules SET pending_id = 'event-pending' WHERE id = ?`, rule.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`INSERT INTO monitoring_rule_events (id, rule_id, target_id, observed_at, status) VALUES ('event-pending', ?, ?, ?, 'pending')`, rule.ID, target.ID, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		t.Fatal(err)
	}

	rec := doMonitoringRequest(t, s.Routes(), http.MethodDelete, "/api/monitoring/rules/"+rule.ID, admin, "")
	if rec.Code != http.StatusConflict {
		t.Fatalf("delete rule with pending execution = %d %s, want 409", rec.Code, rec.Body.String())
	}
	if _, err := s.monitoring.GetRule(t.Context(), rule.ID); err != nil {
		t.Fatalf("pending rule was deleted: %v", err)
	}
}

func TestMonitoringRuleEventsUnknownRule(t *testing.T) {
	s, st := newMonitoringAPI(t, true)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")
	if rec := doMonitoringRequest(t, s.Routes(), http.MethodGet, "/api/monitoring/rules/missing/events", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("unknown rule events = %d %s, want 404", rec.Code, rec.Body.String())
	}
}

func TestMonitoringNodeMetricRuleRequiresExistingNode(t *testing.T) {
	s, st := newMonitoringAPI(t, true)
	seedMonitoringUsers(t, st)
	createMonitoringSchedule(t, st, "schedule-1")
	s.nodes = hub.NewNodeManager(st, hub.NewRevisionManager(st))
	target := monitoring.Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node-1", Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 10, TimeoutSec: 2, Enabled: true}
	if err := s.monitoring.Create(t.Context(), &target); err != nil {
		t.Fatal(err)
	}
	admin := loginAs(t, s, "admin", "password")
	body := `{"name":"high","target_id":"` + target.ID + `","schedule_id":"schedule-1","operator":"gt","expected":"90","enabled":true}`
	rec := doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/rules", admin, body)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "node metric node not found") {
		t.Fatalf("missing node rule = %d %s, want 400", rec.Code, rec.Body.String())
	}
	createMonitoringNode(t, st, "node-1")
	rec = doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/rules", admin, body)
	if rec.Code != http.StatusCreated {
		t.Fatalf("existing node rule = %d %s, want 201", rec.Code, rec.Body.String())
	}
}
