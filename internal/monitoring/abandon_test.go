package monitoring

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestAbandonUnknownBlocksLinkedExecutionsAndAudits(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	ctx = WithAuditActor(ctx, "user-1", "alice")
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	executions := []*models.Execution{
		{ID: "exec-running", TaskID: "task", NodeID: "node", Status: models.ExecStatusRunning},
		{ID: "exec-terminal", TaskID: "task", NodeID: "node", Status: models.ExecStatusSuccess},
	}
	for _, execution := range executions {
		if err := db.WithTx(ctx, func(txCtx context.Context) error {
			if err := db.CreateExecution(txCtx, execution); err != nil {
				return err
			}
			return db.LinkMonitoringExecution(store.WithMonitoringStep(txCtx, "action", 0), claim, execution.ID)
		}); err != nil {
			t.Fatal(err)
		}
	}
	unlinked := &models.Execution{ID: "exec-unlinked", TaskID: "task", NodeID: "node", Status: models.ExecStatusRunning}
	if err := db.CreateExecution(ctx, unlinked); err != nil {
		t.Fatal(err)
	}
	if err := repo.MarkPendingUnknown(ctx); err != nil {
		t.Fatal(err)
	}
	if err := repo.AbandonUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "abandoned_unknown" ||
		!strings.Contains(events[0].Error, "hub restarted before submission outcome was recorded") ||
		!strings.Contains(events[0].Error, AbandonReason) {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	var pending string
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != "" {
		t.Fatalf("pending_id=%q after abandon", pending)
	}
	status, blockReason, endTime := "", "", ""
	if err := db.DB().QueryRow(`SELECT status, block_reason, end_time FROM executions WHERE id=?`, "exec-running").Scan(&status, &blockReason, &endTime); err != nil {
		t.Fatal(err)
	}
	if status != models.ExecStatusBlocked || blockReason != AbandonReason || endTime == "" {
		t.Fatalf("abandoned execution status=%q reason=%q end=%q", status, blockReason, endTime)
	}
	if err := db.DB().QueryRow(`SELECT status FROM executions WHERE id=?`, "exec-terminal").Scan(&status); err != nil || status != models.ExecStatusSuccess {
		t.Fatalf("terminal execution status=%q err=%v", status, err)
	}
	if err := db.DB().QueryRow(`SELECT status FROM executions WHERE id=?`, "exec-unlinked").Scan(&status); err != nil || status != models.ExecStatusRunning {
		t.Fatalf("unlinked execution status=%q err=%v", status, err)
	}
	var auditDetail string
	if err := db.DB().QueryRow(`SELECT detail FROM audit_logs WHERE resource='monitoring_rule' AND action='abandon_unknown' AND resource_id=? AND username='alice'`, rule.ID).Scan(&auditDetail); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(auditDetail, "event_id="+claim) || !strings.Contains(auditDetail, "blocked_executions=1") ||
		!strings.Contains(auditDetail, "hub restarted before submission outcome was recorded") {
		t.Fatalf("abandon audit detail=%q", auditDetail)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err == nil {
		t.Fatal("abandoned event still resolved through ResolveUnknown")
	}
}

func TestLegacyExecutionIDsResolveAndAbandon(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	ctx = WithAuditActor(ctx, "user-1", "alice")
	eventID := "legacy-event"
	if _, err := db.DB().Exec(`INSERT INTO monitoring_rule_events(id,rule_id,target_id,observed_at,status,error,execution_ids) VALUES(?,?,?,?, 'unknown', 'legacy unknown', '["legacy-exec"]')`, eventID, rule.ID, target.ID, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		t.Fatal(err)
	}
	if _, err := db.DB().Exec(`UPDATE monitoring_rules SET pending_id=? WHERE id=?`, eventID, rule.ID); err != nil {
		t.Fatal(err)
	}
	if err := db.CreateExecution(ctx, &models.Execution{ID: "legacy-exec", TaskID: "task", NodeID: "node", Status: models.ExecStatusRunning}); err != nil {
		t.Fatal(err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err == nil {
		t.Fatal("legacy running execution was resolved without abandon")
	}
	if err := repo.AbandonUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	var eventStatus, eventError, pending, executionStatus string
	if err := db.DB().QueryRow(`SELECT status, error FROM monitoring_rule_events WHERE id=?`, eventID).Scan(&eventStatus, &eventError); err != nil {
		t.Fatal(err)
	}
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if err := db.DB().QueryRow(`SELECT status FROM executions WHERE id='legacy-exec'`).Scan(&executionStatus); err != nil {
		t.Fatal(err)
	}
	if eventStatus != "abandoned_unknown" || !strings.Contains(eventError, "legacy unknown") || !strings.Contains(eventError, AbandonReason) {
		t.Fatalf("legacy event status=%q error=%q", eventStatus, eventError)
	}
	if pending != "" || executionStatus != models.ExecStatusBlocked {
		t.Fatalf("pending=%q execution=%q", pending, executionStatus)
	}
}

func TestAbandonUnknownAuditRedactsStorageError(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	ctx = WithAuditActor(ctx, "user-1", "alice")
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	if err := db.WithTx(ctx, func(txCtx context.Context) error {
		if err := db.CreateExecution(txCtx, &models.Execution{ID: "exec-running", TaskID: "task", NodeID: "node", Status: models.ExecStatusRunning}); err != nil {
			return err
		}
		return db.LinkMonitoringExecution(store.WithMonitoringStep(txCtx, "action", 0), claim, "exec-running")
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := db.DB().Exec(`UPDATE monitoring_rule_events SET status='unknown', error='sql: no rows in result set' WHERE id=?`, claim); err != nil {
		t.Fatal(err)
	}
	if err := repo.AbandonUnknown(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	var detail string
	if err := db.DB().QueryRow(`SELECT detail FROM audit_logs WHERE resource='monitoring_rule' AND action='abandon_unknown' AND resource_id=?`, rule.ID).Scan(&detail); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(detail, "event_id="+claim) || !strings.Contains(detail, "storage error") || strings.Contains(detail, "sql:") {
		t.Fatalf("audit detail=%q", detail)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || !strings.Contains(events[0].Error, "sql: no rows in result set") || !strings.Contains(events[0].Error, AbandonReason) {
		t.Fatalf("events=%+v err=%v", events, err)
	}
}

func TestAbandonUnknownRejectsAllTerminalExecutions(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	if err := db.WithTx(ctx, func(txCtx context.Context) error {
		if err := db.CreateExecution(txCtx, &models.Execution{ID: "exec-terminal", TaskID: "task", NodeID: "node", Status: models.ExecStatusSuccess}); err != nil {
			return err
		}
		return db.LinkMonitoringExecution(store.WithMonitoringStep(txCtx, "action", 0), claim, "exec-terminal")
	}); err != nil {
		t.Fatal(err)
	}
	if err := repo.MarkPendingUnknown(ctx); err != nil {
		t.Fatal(err)
	}
	if err := repo.AbandonUnknown(ctx, rule.ID); err == nil || !strings.Contains(err.Error(), "already terminal") {
		t.Fatalf("abandon all-terminal err=%v", err)
	}
	if err := repo.ResolveUnknown(ctx, rule.ID); err != nil {
		t.Fatalf("ResolveUnknown after terminal execution: %v", err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "acknowledged_unknown" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
}

func TestFinishRuleSanitizesEventMessage(t *testing.T) {
	_, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	actionErr := &CompensationFailure{Details: "node-a/action: " + strings.Repeat("x", 600) + "\n\x00"}
	if err := repo.FinishRule(ctx, rule.ID, claim, actionErr); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if strings.ContainsAny(events[0].Error, "\n\x00") {
		t.Fatalf("event message contains control characters: %q", events[0].Error)
	}
	if len([]rune(events[0].Error)) > 515 {
		t.Fatalf("event message too long: %d runes", len([]rune(events[0].Error)))
	}
}

func TestFinishRuleUsesSafeCompensationSummary(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	actionErr := &CompensationFailure{Details: "node-b: execution failed", Cause: errors.New("sql: no rows in result set")}
	if err := repo.FinishRule(ctx, rule.ID, claim, actionErr); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "unknown" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if events[0].Error != "monitoring compensation failed: node-b: execution failed" {
		t.Fatalf("event error=%q", events[0].Error)
	}
	if strings.Contains(events[0].Error, "sql:") {
		t.Fatalf("event error leaked raw cause: %q", events[0].Error)
	}
	var pending string
	if err := db.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != claim {
		t.Fatalf("pending_id=%q want %q", pending, claim)
	}
}

func TestAbandonUnknownRejectsMissingNoPendingAndNonUnknown(t *testing.T) {
	db, repo, ctx, target, rule := newHTTPRuleFixture(t, nil)
	if err := repo.AbandonUnknown(ctx, "missing"); !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("missing rule err=%v", err)
	}
	if err := repo.AbandonUnknown(ctx, rule.ID); err == nil {
		t.Fatal("rule without pending execution abandoned")
	}
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	if err := repo.AbandonUnknown(ctx, rule.ID); err == nil {
		t.Fatal("pending event abandoned")
	}
	if _, err := db.DB().Exec(`UPDATE monitoring_rule_events SET status='failed' WHERE id=?`, claim); err != nil {
		t.Fatal(err)
	}
	if err := repo.AbandonUnknown(ctx, rule.ID); err == nil {
		t.Fatal("failed event abandoned")
	}
}
