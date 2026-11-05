package store

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

type monitoringEventKey struct{}
type monitoringStepKey struct{}
type monitoringStep struct {
	Phase string
	Batch int
}

var (
	ErrMonitoringEventNotPending = errors.New("monitoring event is not pending")
	ErrMonitoringRuleDisabled    = errors.New("monitoring rule disabled")
	ErrMonitoringRuleInterrupted = errors.New("monitoring rule interrupted")
	ErrMonitoringRuleChanged     = errors.New("monitoring rule changed")
)

// WithMonitoringEvent associates newly created executions with a claimed rule event.
func WithMonitoringEvent(ctx context.Context, eventID string) context.Context {
	return context.WithValue(ctx, monitoringEventKey{}, eventID)
}

func MonitoringEventFromContext(ctx context.Context) string {
	value, _ := ctx.Value(monitoringEventKey{}).(string)
	return value
}

func WithMonitoringStep(ctx context.Context, phase string, batch int) context.Context {
	return context.WithValue(ctx, monitoringStepKey{}, monitoringStep{Phase: phase, Batch: batch})
}

func (s *SQLiteStore) EnsureMonitoringEventEnabled(ctx context.Context, eventID, scheduleID string) error {
	if s.txFrom(ctx) == nil {
		return errors.New("monitoring authorization requires transaction")
	}
	step, _ := ctx.Value(monitoringStepKey{}).(monitoringStep)
	if step.Phase == "" {
		step.Phase = "action"
	}
	field := "schedule_id"
	switch step.Phase {
	case "action":
	case "verify":
		field = "verify_schedule_id"
	case "compensate":
		field = "compensate_schedule_id"
	default:
		return errors.New("invalid monitoring step phase")
	}
	var eventStatus, pendingID, configuredSchedule string
	var interrupted, enabled int
	err := s.queryer(ctx).QueryRowContext(ctx, `SELECT e.status, r.pending_id, r.interrupted, json_extract(r.config_json, '$.enabled'), COALESCE(json_extract(r.config_json, '$.' || ?), '')
		FROM monitoring_rule_events e JOIN monitoring_rules r ON r.id = e.rule_id WHERE e.id = ?`, field, eventID).Scan(&eventStatus, &pendingID, &interrupted, &enabled, &configuredSchedule)
	if errors.Is(err, sql.ErrNoRows) {
		return ErrMonitoringEventNotPending
	}
	if err != nil {
		return err
	}
	if eventStatus != "pending" || pendingID != eventID {
		return ErrMonitoringEventNotPending
	}
	if enabled != 1 {
		return ErrMonitoringRuleDisabled
	}
	if interrupted != 0 {
		return ErrMonitoringRuleInterrupted
	}
	if configuredSchedule != scheduleID {
		return ErrMonitoringRuleChanged
	}
	if step.Phase == "action" {
		var kind, nodeID, observed string
		var interval, maxAge int
		err := s.queryer(ctx).QueryRowContext(ctx, `SELECT COALESCE(json_extract(t.config_json,'$.kind'),''), COALESCE(json_extract(t.config_json,'$.monitored_node_id'),''), COALESCE(json_extract(t.config_json,'$.interval_sec'),0), COALESCE(json_extract(t.config_json,'$.max_age_sec'),0), r.last_confirmed_at FROM monitoring_rule_events e JOIN monitoring_rules r ON r.id=e.rule_id JOIN monitoring_http_targets t ON t.id=r.target_id WHERE e.id=?`, eventID).Scan(&kind, &nodeID, &interval, &maxAge, &observed)
		if err != nil {
			return err
		}
		if kind == "node_metric" {
			var status string
			if err := s.queryer(ctx).QueryRowContext(ctx, `SELECT status FROM nodes WHERE id=?`, nodeID).Scan(&status); err != nil {
				return err
			}
			at, err := time.Parse(time.RFC3339Nano, observed)
			age := time.Duration(interval*2) * time.Second
			if maxAge > 0 {
				age = time.Duration(maxAge) * time.Second
			}
			if age > 5*time.Minute {
				age = 5 * time.Minute
			}
			if status != models.NodeStatusOnline || interval <= 0 || err != nil || at.After(time.Now()) || time.Since(at) > age {
				return errors.New("node metric is stale or node offline")
			}
		}
	}
	return nil
}

// LinkMonitoringExecution must run in the same transaction as CreateExecution.
// CountMonitoringRulesForSchedule returns rules referencing the schedule in
// the action, verify or compensation phase. Schedule definitions live inside
// monitoring_rules.config_json, so there is no SQL foreign key to rely on.
func (s *SQLiteStore) CountMonitoringRulesForSchedule(ctx context.Context, scheduleID string) (int, error) {
	if scheduleID == "" {
		return 0, nil
	}
	var count int
	err := s.queryer(ctx).QueryRowContext(ctx, `SELECT COUNT(*) FROM monitoring_rules
		WHERE json_extract(config_json, '$.schedule_id') = ?
		   OR json_extract(config_json, '$.verify_schedule_id') = ?
		   OR json_extract(config_json, '$.compensate_schedule_id') = ?`, scheduleID, scheduleID, scheduleID).Scan(&count)
	return count, err
}

// CountNonTerminalMonitoringExecutionsTx counts active executions for an
// event. Legacy events may only have execution_ids and no monitoring_rule_steps,
// so fall back to the JSON list when no step rows exist.
func CountNonTerminalMonitoringExecutionsTx(ctx context.Context, tx *sql.Tx, eventID string) (int, error) {
	var steps int
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM monitoring_rule_steps WHERE event_id = ?`, eventID).Scan(&steps); err != nil {
		return 0, err
	}
	var count int
	if steps > 0 {
		err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM monitoring_rule_steps s JOIN executions e ON e.id = s.execution_id WHERE s.event_id = ? AND e.status NOT IN ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED')`, eventID).Scan(&count)
		return count, err
	}
	var executionIDs string
	err := tx.QueryRowContext(ctx, `SELECT execution_ids FROM monitoring_rule_events WHERE id = ?`, eventID).Scan(&executionIDs)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	err = tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM executions WHERE id IN (SELECT value FROM json_each(?)) AND status NOT IN ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED')`, executionIDs).Scan(&count)
	return count, err
}

// AbandonMonitoringExecutionsTx marks non-terminal executions linked to an
// event as BLOCKED. It does not cancel anything on the node; it only records
// that the administrator no longer waits for the result. Legacy events without
// step rows are resolved through their execution_ids JSON list.
func AbandonMonitoringExecutionsTx(ctx context.Context, tx *sql.Tx, eventID, reason string) (int, error) {
	if eventID == "" {
		return 0, errors.New("monitoring event id required")
	}
	var steps int
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM monitoring_rule_steps WHERE event_id = ?`, eventID).Scan(&steps); err != nil {
		return 0, err
	}
	var result sql.Result
	var err error
	if steps > 0 {
		result, err = tx.ExecContext(ctx, `UPDATE executions
			SET status = 'BLOCKED', block_reason = ?, end_time = ?
			WHERE id IN (SELECT execution_id FROM monitoring_rule_steps WHERE event_id = ?)
			  AND status NOT IN ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED')`,
			reason, time.Now().UTC().Format(time.RFC3339Nano), eventID)
	} else {
		var executionIDs string
		if err := tx.QueryRowContext(ctx, `SELECT execution_ids FROM monitoring_rule_events WHERE id = ?`, eventID).Scan(&executionIDs); err != nil {
			return 0, err
		}
		result, err = tx.ExecContext(ctx, `UPDATE executions
			SET status = 'BLOCKED', block_reason = ?, end_time = ?
			WHERE id IN (SELECT value FROM json_each(?))
			  AND status NOT IN ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED')`,
			reason, time.Now().UTC().Format(time.RFC3339Nano), executionIDs)
	}
	if err != nil {
		return 0, err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return 0, err
	}
	return int(count), nil
}

func (s *SQLiteStore) LinkMonitoringExecution(ctx context.Context, eventID, executionID string) error {
	if s.txFrom(ctx) == nil {
		return errors.New("monitoring execution link requires transaction")
	}
	result, err := s.execer(ctx).ExecContext(ctx, `UPDATE monitoring_rule_events SET execution_ids = json_insert(execution_ids, '$[#]', ?) WHERE id = ? AND status = 'pending'`, executionID, eventID)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if count != 1 {
		return errors.New("monitoring event is not pending")
	}
	step, _ := ctx.Value(monitoringStepKey{}).(monitoringStep)
	if step.Phase == "" {
		step.Phase = "action"
	}
	if step.Phase != "action" && step.Phase != "verify" && step.Phase != "compensate" {
		return errors.New("invalid monitoring step phase")
	}
	result, err = s.execer(ctx).ExecContext(ctx, `INSERT INTO monitoring_rule_steps (event_id, execution_id, node_id, phase, batch_index) SELECT ?, id, node_id, ?, ? FROM executions WHERE id = ?`, eventID, step.Phase, step.Batch, executionID)
	if err != nil {
		return err
	}
	inserted, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if inserted != 1 {
		return errors.New("monitoring execution not found")
	}
	return nil
}
