package hubserver

import (
	"context"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type monitoringPlan struct {
	schedule *models.Schedule
	task     *models.Task
	nodes    map[string]bool
}

type monitoringRunResult struct {
	attempted bool
	known     bool
	status    string
	err       error
}

func monitoringKnownFailure(details string, cause error) error {
	return &monitoring.KnownFailure{Details: details, Cause: cause}
}

func monitoringTransientError(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) ||
		store.IsBusyError(err) || store.IsDriverError(err)
}

func monitoringStepStatus(err error) string {
	var failure *monitoringStepFailure
	if errors.As(err, &failure) {
		return failure.status
	}
	if errors.Is(err, errMonitoringStepFailed) {
		return models.ExecStatusFailed
	}
	return ""
}

func monitoringKnownFailureReason(status string, err error) string {
	switch {
	case errors.Is(err, store.ErrMonitoringEventNotPending):
		return "event not pending"
	case errors.Is(err, store.ErrMonitoringRuleDisabled):
		return "rule disabled"
	case errors.Is(err, store.ErrMonitoringRuleInterrupted):
		return "rule interrupted"
	case errors.Is(err, store.ErrMonitoringRuleChanged):
		return "rule changed"
	case errors.Is(err, hub.ErrAgentOffline):
		return "agent offline"
	case errors.Is(err, context.DeadlineExceeded):
		return "timed out"
	case errors.Is(err, context.Canceled):
		return "canceled"
	case store.IsBusyError(err) || store.IsDriverError(err):
		return "storage unavailable"
	}
	switch status {
	case models.ExecStatusFailed:
		return "execution failed"
	case models.ExecStatusSkipped:
		return "execution skipped"
	case models.ExecStatusBlocked:
		return "execution blocked"
	case models.ExecStatusCanceled:
		return "execution canceled"
	case models.ExecStatusTimedOut:
		return "execution timed out"
	}
	if err == nil {
		return "known failure"
	}
	message := strings.ToLower(err.Error())
	switch {
	case strings.Contains(message, "schedule disabled") || strings.Contains(message, "schedule unavailable"):
		return "schedule disabled"
	case strings.Contains(message, "task disabled") || strings.Contains(message, "task unavailable"):
		return "task disabled"
	case strings.Contains(message, "script") && strings.Contains(message, "disabled"):
		return "script disabled"
	case strings.Contains(message, "node") && (strings.Contains(message, "unavailable") || strings.Contains(message, "not online") || strings.Contains(message, "disabled")):
		return "node unavailable"
	case strings.Contains(message, "capability"):
		return "capability missing"
	case strings.Contains(message, "target"):
		return "target mismatch"
	case strings.Contains(message, "rule interrupted"):
		return "rule interrupted"
	case strings.Contains(message, "rule disabled"):
		return "rule disabled"
	default:
		return "preflight rejected"
	}
}

func monitoringCompensableActionStatus(status string) bool {
	// FAILED, TIMED_OUT and CANCELED may have started a process and produced
	// partial side effects, so compensate them. SKIPPED/BLOCKED have no
	// running process to clean up.
	switch status {
	case models.ExecStatusSuccess, models.ExecStatusFailed, models.ExecStatusTimedOut, models.ExecStatusCanceled:
		return true
	default:
		return false
	}
}

func monitoringWaitDuration(strategy hub.ExecutionStrategy) (time.Duration, error) {
	strategy, err := strategy.Normalize()
	if err != nil {
		return 0, err
	}
	if strategy.Retry >= math.MaxInt64 || int64(strategy.Timeout) > (math.MaxInt64/int64(time.Second)-30)/int64(strategy.Retry+1) {
		return 0, errors.New("monitoring execution timeout exceeds supported duration")
	}
	return time.Duration(int64(strategy.Timeout)*int64(strategy.Retry+1)+30) * time.Second, nil
}

func runMonitoringRule(ctx context.Context, rule monitoring.Rule, claim string, st store.Store, nodes *hub.NodeManager, tasks *hub.TaskManager, schedules *hub.ScheduleManager, execs *hub.ExecutionManager) error {
	load := func(phase, id string) (*monitoringPlan, error) {
		if id == "" {
			return nil, nil
		}
		schedule, err := schedules.Get(ctx, id)
		if err != nil {
			if monitoringTransientError(err) {
				return nil, err
			}
			return nil, monitoringKnownFailure(phase+": schedule unavailable", err)
		}
		if !schedule.Enabled {
			return nil, monitoringKnownFailure(phase+": schedule disabled", nil)
		}
		task, err := tasks.Get(ctx, schedule.TaskID)
		if err != nil {
			if monitoringTransientError(err) {
				return nil, err
			}
			return nil, monitoringKnownFailure(phase+": task unavailable", err)
		}
		if !task.Enabled {
			return nil, monitoringKnownFailure(phase+": task disabled", nil)
		}
		if err := hub.ValidateTarget(ctx, st, schedule.Target); err != nil {
			return nil, monitoringKnownFailure(phase+": target mismatch", err)
		}
		ids, err := nodes.ResolveTarget(ctx, schedule.Target)
		if err != nil {
			if monitoringTransientError(err) {
				return nil, err
			}
			return nil, monitoringKnownFailure(phase+": target mismatch", err)
		}
		members := make(map[string]bool, len(ids))
		for _, id := range ids {
			members[id] = true
		}
		return &monitoringPlan{schedule: schedule, task: task, nodes: members}, nil
	}
	action, err := load("action", rule.ScheduleID)
	if err != nil {
		return err
	}
	verify, err := load("verify", rule.VerifyScheduleID)
	if err != nil {
		return err
	}
	compensate, err := load("compensate", rule.CompensateScheduleID)
	if err != nil {
		return err
	}
	if action == nil || len(action.nodes) == 0 {
		return monitoringKnownFailure("action: no targets", nil)
	}
	ids := make([]string, 0, len(action.nodes))
	for id := range action.nodes {
		if verify != nil && !verify.nodes[id] {
			return monitoringKnownFailure(id+"/verify: target mismatch", nil)
		}
		if compensate != nil && !compensate.nodes[id] {
			return monitoringKnownFailure(id+"/compensate: target mismatch", nil)
		}
		ids = append(ids, id)
	}
	sort.Strings(ids)
	baseCtx := store.WithMonitoringEvent(ctx, claim)
	run := func(plan *monitoringPlan, node, phase string, batch int) monitoringRunResult {
		waitDuration, err := monitoringWaitDuration(hub.StrategyFromSchedule(plan.schedule))
		if err != nil {
			return monitoringRunResult{known: true, err: err}
		}
		stepCtx := store.WithMonitoringStep(baseCtx, phase, batch)
		executions, err := execs.RunManual(stepCtx, plan.task, []string{node}, nil, hub.StrategyFromSchedule(plan.schedule), plan.schedule)
		if err != nil {
			if len(executions) == 0 {
				// Transient storage/context failures do not prove that nothing
				// was dispatched; fail closed. Business preflight rejections do.
				if monitoringTransientError(err) {
					return monitoringRunResult{known: false, err: err}
				}
				return monitoringRunResult{known: true, err: err}
			}
			for _, execution := range executions {
				if execution.Status != models.ExecStatusFailed {
					// The execution exists but its terminal state was not
					// persisted (or is otherwise unresolved): fail closed.
					return monitoringRunResult{attempted: true, known: false, status: execution.Status, err: err}
				}
			}
			// The execution manager records a dispatch failure as FAILED before
			// returning it. That proves the task was never dispatched.
			return monitoringRunResult{known: true, status: models.ExecStatusFailed, err: err}
		}
		if len(executions) == 0 {
			return monitoringRunResult{known: true, err: errors.New("monitoring execution was not created")}
		}
		if len(executions) != 1 {
			return monitoringRunResult{attempted: true, known: false, err: errors.New("monitoring returned multiple executions")}
		}
		waitCtx, cancel := context.WithTimeout(ctx, waitDuration)
		defer cancel()
		err = waitMonitoringExecution(waitCtx, st, executions[0].ID)
		if err == nil {
			return monitoringRunResult{attempted: true, known: true, status: models.ExecStatusSuccess}
		}
		status := monitoringStepStatus(err)
		if errors.Is(err, errMonitoringStepFailed) {
			return monitoringRunResult{attempted: true, known: true, status: status, err: err}
		}
		return monitoringRunResult{attempted: true, known: false, status: status, err: err}
	}
	type outcome struct {
		node        string
		phase       string
		attempted   bool
		known       bool
		compensable bool
		status      string
		err         error
	}
	type completedNode struct {
		node  string
		batch int
	}
	completed := []completedNode{}
	batchSize := rule.BatchSize
	if batchSize < 1 {
		batchSize = 1
	}
	for start := 0; start < len(ids); start += batchSize {
		end := start + batchSize
		if end > len(ids) {
			end = len(ids)
		}
		results := make([]outcome, end-start)
		var group sync.WaitGroup
		for i, nodeID := range ids[start:end] {
			group.Add(1)
			go func(i int, node string) {
				defer group.Done()
				actionResult := run(action, node, "action", start/batchSize)
				result := outcome{node: node, phase: "action", attempted: actionResult.attempted, known: actionResult.known, status: actionResult.status, err: actionResult.err}
				if actionResult.err == nil && verify != nil {
					verifyResult := run(verify, node, "verify", start/batchSize)
					result.phase = "verify"
					result.attempted = result.attempted || verifyResult.attempted
					result.known, result.status, result.err = verifyResult.known, verifyResult.status, verifyResult.err
				}
				result.compensable = result.attempted && monitoringCompensableActionStatus(actionResult.status)
				results[i] = result
			}(i, nodeID)
		}
		group.Wait()
		failed, uncertain := false, false
		knownDetails := []string{}
		causes := []error{}
		for _, result := range results {
			if result.compensable {
				completed = append(completed, completedNode{node: result.node, batch: start / batchSize})
			}
			if result.err != nil {
				failed = true
				causes = append(causes, fmt.Errorf("%s/%s: %w", result.node, result.phase, result.err))
				if result.known {
					reason := monitoringKnownFailureReason(result.status, result.err)
					knownDetails = append(knownDetails, fmt.Sprintf("%s/%s: %s", result.node, result.phase, reason))
				} else {
					uncertain = true
				}
			}
		}
		if !failed {
			continue
		}
		if uncertain {
			// Keep raw node/phase causes in the returned error for logs; the
			// event persists the generic unknown message.
			return fmt.Errorf("monitoring batch failed or outcome is unknown: %w", errors.Join(causes...))
		}
		if compensate == nil || len(completed) == 0 {
			// Nothing was dispatched, so there is no successful node to
			// compensate; report the known failure instead of "compensated".
			return &monitoring.KnownFailure{
				Details: strings.Join(knownDetails, "; "),
				Cause:   errors.Join(causes...),
			}
		}
		details := []string{}
		compensationCauses := []error{}
		for i := len(completed) - 1; i >= 0; i-- {
			item := completed[i]
			compensation := run(compensate, item.node, "compensate", item.batch)
			if compensation.err == nil {
				continue
			}
			details = append(details, fmt.Sprintf("%s: %s", item.node, monitoringCompensationReason(compensation.status, compensation.known, compensation.err)))
			compensationCauses = append(compensationCauses, fmt.Errorf("%s: %w", item.node, compensation.err))
		}
		if len(details) > 0 {
			return &monitoring.CompensationFailure{
				Details: strings.Join(details, "; "),
				Cause:   errors.Join(compensationCauses...),
			}
		}
		return &monitoring.Compensated{Details: strings.Join(knownDetails, "; ")}
	}
	return nil
}

func monitoringCompensationReason(status string, known bool, err error) string {
	reason := monitoringKnownFailureReason(status, err)
	if reason != "preflight rejected" {
		return reason
	}
	if known {
		return "known failure"
	}
	return "outcome unknown"
}
