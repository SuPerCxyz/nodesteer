package api

import (
	"context"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestMonitoringAbandonUnknownAPI(t *testing.T) {
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
	createRule := func(name string) monitoring.Rule {
		t.Helper()
		rule := monitoring.Rule{Name: name, TargetID: target.ID, ScheduleID: "schedule-1", Operator: "status_eq", Expected: "200", Enabled: true}
		if err := s.monitoring.CreateRule(t.Context(), &rule); err != nil {
			t.Fatal(err)
		}
		return rule
	}
	createUnknownEvent := func(rule monitoring.Rule, eventID, status string, executions ...*models.Execution) {
		t.Helper()
		if _, err := st.DB().Exec(`INSERT INTO monitoring_rule_events (id,rule_id,target_id,observed_at,status) VALUES(?,?,?,?, 'pending')`, eventID, rule.ID, target.ID, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
			t.Fatal(err)
		}
		if _, err := st.DB().Exec(`UPDATE monitoring_rules SET pending_id=? WHERE id=?`, eventID, rule.ID); err != nil {
			t.Fatal(err)
		}
		for _, execution := range executions {
			execution := execution
			if err := st.WithTx(t.Context(), func(txCtx context.Context) error {
				if err := st.CreateExecution(txCtx, execution); err != nil {
					return err
				}
				return st.LinkMonitoringExecution(store.WithMonitoringStep(txCtx, "action", 0), eventID, execution.ID)
			}); err != nil {
				t.Fatal(err)
			}
		}
		if _, err := st.DB().Exec(`UPDATE monitoring_rule_events SET status=? WHERE id=?`, status, eventID); err != nil {
			t.Fatal(err)
		}
	}

	t.Run("abandon running execution", func(t *testing.T) {
		rule := createRule("abandon-running")
		eventID := "event-abandon-running"
		createUnknownEvent(rule, eventID, "unknown", &models.Execution{ID: "exec-abandon-running", TaskID: "task", NodeID: "node", Status: models.ExecStatusRunning})
		rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/"+rule.ID+"/abandon-unknown", admin, "")
		if rec.Code != http.StatusNoContent {
			t.Fatalf("abandon = %d %s, want 204", rec.Code, rec.Body.String())
		}
		var eventStatus, eventError, pending, execStatus, reason, endTime string
		if err := st.DB().QueryRow(`SELECT status, error FROM monitoring_rule_events WHERE id=?`, eventID).Scan(&eventStatus, &eventError); err != nil {
			t.Fatal(err)
		}
		if err := st.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
			t.Fatal(err)
		}
		if err := st.DB().QueryRow(`SELECT status, block_reason, end_time FROM executions WHERE id=?`, "exec-abandon-running").Scan(&execStatus, &reason, &endTime); err != nil {
			t.Fatal(err)
		}
		if eventStatus != "abandoned_unknown" || eventError != monitoring.AbandonReason || pending != "" {
			t.Fatalf("event status=%q error=%q pending=%q", eventStatus, eventError, pending)
		}
		if execStatus != models.ExecStatusBlocked || reason != monitoring.AbandonReason || endTime == "" {
			t.Fatalf("execution status=%q reason=%q end=%q", execStatus, reason, endTime)
		}
		var audit int
		if err := st.DB().QueryRow(`SELECT COUNT(*) FROM audit_logs WHERE resource='monitoring_rule' AND action='abandon_unknown' AND resource_id=? AND username='admin'`, rule.ID).Scan(&audit); err != nil || audit != 1 {
			t.Fatalf("abandon audit=%d err=%v", audit, err)
		}
	})

	t.Run("viewer forbidden", func(t *testing.T) {
		rule := createRule("abandon-viewer")
		createUnknownEvent(rule, "event-abandon-viewer", "unknown")
		rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/"+rule.ID+"/abandon-unknown", viewer, "")
		if rec.Code != http.StatusForbidden {
			t.Fatalf("viewer abandon = %d %s, want 403", rec.Code, rec.Body.String())
		}
	})

	t.Run("no pending execution", func(t *testing.T) {
		rule := createRule("abandon-clear")
		rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/"+rule.ID+"/abandon-unknown", admin, "")
		if rec.Code != http.StatusConflict || strings.Contains(rec.Body.String(), "sql:") {
			t.Fatalf("abandon without pending = %d %s, want 409 without SQL details", rec.Code, rec.Body.String())
		}
	})

	t.Run("non unknown event", func(t *testing.T) {
		rule := createRule("abandon-failed")
		createUnknownEvent(rule, "event-abandon-failed", "failed")
		rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/"+rule.ID+"/abandon-unknown", admin, "")
		if rec.Code != http.StatusConflict || strings.Contains(rec.Body.String(), "sql:") {
			t.Fatalf("abandon failed event = %d %s, want 409 without SQL details", rec.Code, rec.Body.String())
		}
	})

	t.Run("unknown rule", func(t *testing.T) {
		rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/rules/missing/abandon-unknown", admin, "")
		if rec.Code != http.StatusNotFound || strings.Contains(rec.Body.String(), "sql:") {
			t.Fatalf("abandon unknown rule = %d %s, want 404 without SQL details", rec.Code, rec.Body.String())
		}
	})
}
