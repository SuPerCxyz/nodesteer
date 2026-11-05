package store

import (
	"context"
	"errors"
	"testing"
)

func TestEnsureMonitoringEventEnabledChecksPhaseScheduleAndRuleState(t *testing.T) {
	st, err := OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	st.DB().SetMaxOpenConns(1)
	ctx := context.Background()
	if _, err := st.DB().Exec(`INSERT INTO monitoring_http_targets(id,config_json,updated_at) VALUES('target','{}','')`); err != nil {
		t.Fatal(err)
	}
	config := `{"id":"rule","name":"rule","target_id":"target","schedule_id":"action","verify_schedule_id":"verify","compensate_schedule_id":"compensate","operator":"status_eq","expected":"503","enabled":true}`
	if _, err := st.DB().Exec(`INSERT INTO monitoring_rules(id,target_id,config_json,pending_id) VALUES('rule','target',?,'event')`, config); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`INSERT INTO monitoring_rule_events(id,rule_id,target_id,observed_at,status) VALUES('event','rule','target','2026-01-01T00:00:00Z','pending')`); err != nil {
		t.Fatal(err)
	}
	phases := map[string]string{"action": "action", "verify": "verify", "compensate": "compensate"}
	for phase, scheduleID := range phases {
		phase, scheduleID := phase, scheduleID
		t.Run(phase, func(t *testing.T) {
			if err := st.WithTx(ctx, func(txCtx context.Context) error {
				return st.EnsureMonitoringEventEnabled(WithMonitoringStep(txCtx, phase, 0), "event", scheduleID)
			}); err != nil {
				t.Fatalf("matching %s schedule denied: %v", phase, err)
			}
			if err := st.WithTx(ctx, func(txCtx context.Context) error {
				return st.EnsureMonitoringEventEnabled(WithMonitoringStep(txCtx, phase, 0), "event", "other")
			}); !errors.Is(err, ErrMonitoringRuleChanged) {
				t.Fatalf("mismatched %s schedule err=%v, want rule changed", phase, err)
			}
		})
	}
	if _, err := st.DB().Exec(`UPDATE monitoring_rules SET config_json=json_set(config_json,'$.enabled',0) WHERE id='rule'`); err != nil {
		t.Fatal(err)
	}
	for phase, scheduleID := range phases {
		if err := st.WithTx(ctx, func(txCtx context.Context) error {
			return st.EnsureMonitoringEventEnabled(WithMonitoringStep(txCtx, phase, 0), "event", scheduleID)
		}); !errors.Is(err, ErrMonitoringRuleDisabled) {
			t.Fatalf("disabled rule %s err=%v, want rule disabled", phase, err)
		}
	}
	if _, err := st.DB().Exec(`UPDATE monitoring_rules SET config_json=json_set(config_json,'$.enabled',1), interrupted=1 WHERE id='rule'`); err != nil {
		t.Fatal(err)
	}
	for phase, scheduleID := range phases {
		if err := st.WithTx(ctx, func(txCtx context.Context) error {
			return st.EnsureMonitoringEventEnabled(WithMonitoringStep(txCtx, phase, 0), "event", scheduleID)
		}); !errors.Is(err, ErrMonitoringRuleInterrupted) {
			t.Fatalf("interrupted event %s err=%v, want rule interrupted", phase, err)
		}
	}
	if _, err := st.DB().Exec(`UPDATE monitoring_rules SET interrupted=0 WHERE id='rule'`); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`UPDATE monitoring_rule_events SET status='completed' WHERE id='event'`); err != nil {
		t.Fatal(err)
	}
	for phase, scheduleID := range phases {
		if err := st.WithTx(ctx, func(txCtx context.Context) error {
			return st.EnsureMonitoringEventEnabled(WithMonitoringStep(txCtx, phase, 0), "event", scheduleID)
		}); !errors.Is(err, ErrMonitoringEventNotPending) {
			t.Fatalf("completed event %s err=%v, want event not pending", phase, err)
		}
	}
}
