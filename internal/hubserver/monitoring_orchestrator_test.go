package hubserver

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type monitoringScriptedAgent struct {
	nodeID           string
	store            *store.SQLiteStore
	updateMu         *sync.Mutex
	mu               sync.Mutex
	statusBySchedule map[string]string
	sent             []protocol.RunExecutionPayload
	onSend           func()
}

func (a *monitoringScriptedAgent) NodeID() string { return a.nodeID }
func (a *monitoringScriptedAgent) Close() error   { return nil }

func (a *monitoringScriptedAgent) Send(msg protocol.Envelope) error {
	if msg.Type != protocol.MsgRunExecution {
		return nil
	}
	var payload protocol.RunExecutionPayload
	if err := json.Unmarshal(msg.Payload, &payload); err != nil {
		return err
	}
	a.mu.Lock()
	a.sent = append(a.sent, payload)
	status := a.statusBySchedule[payload.ScheduleID]
	a.mu.Unlock()
	if a.onSend != nil {
		a.onSend()
	}
	if status == "" {
		return nil
	}
	if a.updateMu != nil {
		a.updateMu.Lock()
		defer a.updateMu.Unlock()
	}
	execution, err := a.store.GetExecution(context.Background(), payload.ExecutionID)
	if err != nil {
		return err
	}
	execution.Status = status
	execution.EndTime = time.Now()
	return a.store.UpdateExecution(context.Background(), execution)
}

func newMonitoringOrchestratorHub(t *testing.T) (*Hub, *store.SQLiteStore, *monitoring.Repository, context.Context) {
	t.Helper()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	dir := t.TempDir()
	h, err := New(Config{GatewayAddr: ":8443", RegistrationToken: "test-registration", DataDir: dir, ArtifactDir: filepath.Join(dir, "artifacts"), BaseURL: "http://127.0.0.1:8080", HeartbeatTimeout: 10 * time.Second, SessionTTL: time.Hour, DevNoAuth: true}, logger)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { h.Close() })
	st := h.Store().(*store.SQLiteStore)
	return h, st, monitoring.NewRepository(st.DB()), context.Background()
}

func createMonitoringNodeWithCapabilities(t *testing.T, ctx context.Context, st *store.SQLiteStore, id string, capabilities map[string]bool) {
	t.Helper()
	if err := st.UpsertNode(ctx, &models.Node{ID: id, AgentID: "agent-" + id, Status: models.NodeStatusOnline, Capabilities: capabilities}); err != nil {
		t.Fatal(err)
	}
}

func createMonitoringNode(t *testing.T, ctx context.Context, st *store.SQLiteStore, id string) {
	t.Helper()
	createMonitoringNodeWithCapabilities(t, ctx, st, id, map[string]bool{models.CapScript: true})
}

func createMonitoringSchedule(t *testing.T, ctx context.Context, st *store.SQLiteStore, name string, nodes []string) *models.Schedule {
	t.Helper()
	task := &models.Task{Name: name, Type: models.TaskTypeCommand, Command: "true", Enabled: true}
	if err := st.CreateTask(ctx, task); err != nil {
		t.Fatal(err)
	}
	schedule := &models.Schedule{Name: name, TaskID: task.ID, Type: models.ScheduleTypeManual, Target: models.Target{Type: "node", NodeIDs: nodes}, Enabled: true}
	if err := st.CreateSchedule(ctx, schedule); err != nil {
		t.Fatal(err)
	}
	return schedule
}

func claimMonitoringRule(t *testing.T, ctx context.Context, repo *monitoring.Repository, target monitoring.Target, rule monitoring.Rule) string {
	t.Helper()
	claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
	if err != nil || claim == "" {
		t.Fatalf("claim=%q err=%v", claim, err)
	}
	return claim
}

func runMonitoringOrchestration(t *testing.T, ctx context.Context, h *Hub, st *store.SQLiteStore, rule monitoring.Rule, claim string) error {
	t.Helper()
	tasks := hub.NewTaskManager(st, h.Revisions(), h.SyncMgr())
	return runMonitoringRule(ctx, rule, claim, st, h.Nodes(), tasks, h.Schedules(), h.ExecMgr())
}

func TestMonitoringOrchestratorKnownFailureWithoutCompensation(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	createMonitoringNode(t, ctx, st, "node-a")
	createMonitoringNode(t, ctx, st, "node-b")
	schedule := createMonitoringSchedule(t, ctx, st, "action", []string{"node-a", "node-b"})
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: schedule.ID, Operator: "status_eq", Expected: "503", BatchSize: 2, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if !errors.Is(err, monitoring.ErrKnownFailure) {
		t.Fatalf("err=%v, want known failure", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "failed" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	for _, nodeID := range []string{"node-a", "node-b"} {
		if !strings.Contains(events[0].Error, nodeID+"/action: agent offline") {
			t.Fatalf("event error=%q missing %s reason", events[0].Error, nodeID)
		}
	}
	if strings.Contains(events[0].Error, "sql:") {
		t.Fatalf("event error leaked SQL: %q", events[0].Error)
	}
	var failed int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM executions WHERE task_id=(SELECT task_id FROM schedules WHERE id=?) AND status='FAILED'`, schedule.ID).Scan(&failed); err != nil {
		t.Fatal(err)
	}
	if failed != 2 {
		t.Fatalf("failed executions=%d, want 2", failed)
	}
}

func TestMonitoringOrchestratorUnknownOutcomeIsNotKnownFailure(t *testing.T) {
	h, st, repo, _ := newMonitoringOrchestratorHub(t)
	createMonitoringNode(t, context.Background(), st, "node-a")
	schedule := createMonitoringSchedule(t, context.Background(), st, "action", []string{"node-a"})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	agent := &monitoringScriptedAgent{nodeID: "node-a", store: st, updateMu: &sync.Mutex{}, onSend: cancel}
	h.Sessions().Register(agent)
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(context.Background(), &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: schedule.ID, Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(context.Background(), &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, context.Background(), repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if err == nil || errors.Is(err, monitoring.ErrKnownFailure) || errors.Is(err, monitoring.ErrCompensated) {
		t.Fatalf("err=%v, want unknown outcome", err)
	}
	if err := repo.FinishRule(context.Background(), rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(context.Background(), rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "unknown" || !strings.Contains(events[0].Error, "partial execution") {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	var pending string
	if err := st.DB().QueryRow(`SELECT pending_id FROM monitoring_rules WHERE id=?`, rule.ID).Scan(&pending); err != nil {
		t.Fatal(err)
	}
	if pending != claim {
		t.Fatalf("pending_id=%q want %q", pending, claim)
	}
}

func TestMonitoringOrchestratorMixedBatchCompensatesOnlyDispatchedNode(t *testing.T) {
	for _, withCompensation := range []bool{false, true} {
		name := "known-failure-without-compensation"
		if withCompensation {
			name = "compensates-online-node-only"
		}
		t.Run(name, func(t *testing.T) {
			h, st, repo, ctx := newMonitoringOrchestratorHub(t)
			createMonitoringNode(t, ctx, st, "node-a")
			createMonitoringNode(t, ctx, st, "node-b")
			action := createMonitoringSchedule(t, ctx, st, "action", []string{"node-a", "node-b"})
			var compensate *models.Schedule
			statuses := map[string]string{action.ID: models.ExecStatusSuccess}
			if withCompensation {
				compensate = createMonitoringSchedule(t, ctx, st, "compensate", []string{"node-a", "node-b"})
				statuses[compensate.ID] = models.ExecStatusSuccess
			}
			// Only node-a has an agent; node-b fails dispatch after its execution
			// row is created and must not enter the compensation set.
			h.Sessions().Register(&monitoringScriptedAgent{nodeID: "node-a", store: st, updateMu: &sync.Mutex{}, statusBySchedule: statuses})
			target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
			if err := repo.Create(ctx, &target); err != nil {
				t.Fatal(err)
			}
			rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, Operator: "status_eq", Expected: "503", BatchSize: 2, Enabled: true}
			if compensate != nil {
				rule.CompensateScheduleID = compensate.ID
			}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			claim := claimMonitoringRule(t, ctx, repo, target, rule)
			err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
			wantStatus := "failed"
			if withCompensation {
				if !errors.Is(err, monitoring.ErrCompensated) {
					t.Fatalf("err=%v, want compensated", err)
				}
				wantStatus = "compensated"
			} else if !errors.Is(err, monitoring.ErrKnownFailure) {
				t.Fatalf("err=%v, want known failure", err)
			}
			if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
				t.Fatal(err)
			}
			events, err := repo.RuleEvents(ctx, rule.ID)
			if err != nil || len(events) != 1 || events[0].Status != wantStatus {
				t.Fatalf("events=%+v err=%v", events, err)
			}
			stepsByPhase := map[string][]string{}
			for _, step := range events[0].Steps {
				stepsByPhase[step.Phase] = append(stepsByPhase[step.Phase], step.NodeID)
			}
			if len(stepsByPhase["action"]) != 2 {
				t.Fatalf("action steps=%v", stepsByPhase["action"])
			}
			if withCompensation {
				if len(stepsByPhase["compensate"]) != 1 || stepsByPhase["compensate"][0] != "node-a" {
					t.Fatalf("compensation steps=%v, want only node-a", stepsByPhase["compensate"])
				}
			} else if len(stepsByPhase["compensate"]) != 0 {
				t.Fatalf("unexpected compensation steps=%v", stepsByPhase["compensate"])
			}
		})
	}
}

func TestMonitoringOrchestratorPreflightFailuresAreKnown(t *testing.T) {
	tests := []struct {
		name       string
		wantReason string
		setup      func(t *testing.T, ctx context.Context, st *store.SQLiteStore) (*models.Task, *models.Schedule)
	}{
		{
			name:       "offline node",
			wantReason: "node unavailable",
			setup: func(t *testing.T, ctx context.Context, st *store.SQLiteStore) (*models.Task, *models.Schedule) {
				createMonitoringNode(t, ctx, st, "node-a")
				if err := st.UpdateNodeStatus(ctx, "node-a", models.NodeStatusOffline); err != nil {
					t.Fatal(err)
				}
				task := &models.Task{Name: "action", Type: models.TaskTypeCommand, Command: "true", Enabled: true}
				if err := st.CreateTask(ctx, task); err != nil {
					t.Fatal(err)
				}
				schedule := &models.Schedule{Name: "action", TaskID: task.ID, Type: models.ScheduleTypeManual, Target: models.Target{Type: "node", NodeIDs: []string{"node-a"}}, Enabled: true}
				if err := st.CreateSchedule(ctx, schedule); err != nil {
					t.Fatal(err)
				}
				return task, schedule
			},
		},
		{
			name:       "missing capability",
			wantReason: "capability missing",
			setup: func(t *testing.T, ctx context.Context, st *store.SQLiteStore) (*models.Task, *models.Schedule) {
				createMonitoringNodeWithCapabilities(t, ctx, st, "node-a", map[string]bool{})
				task := &models.Task{Name: "action", Type: models.TaskTypeCommand, Command: "true", Enabled: true}
				if err := st.CreateTask(ctx, task); err != nil {
					t.Fatal(err)
				}
				schedule := &models.Schedule{Name: "action", TaskID: task.ID, Type: models.ScheduleTypeManual, Target: models.Target{Type: "node", NodeIDs: []string{"node-a"}}, Enabled: true}
				if err := st.CreateSchedule(ctx, schedule); err != nil {
					t.Fatal(err)
				}
				return task, schedule
			},
		},
		{
			name:       "disabled script",
			wantReason: "script disabled",
			setup: func(t *testing.T, ctx context.Context, st *store.SQLiteStore) (*models.Task, *models.Schedule) {
				createMonitoringNode(t, ctx, st, "node-a")
				script := &models.Script{Name: "disabled", Interpreter: "shell", Content: "true", Enabled: false}
				if err := st.CreateScript(ctx, script); err != nil {
					t.Fatal(err)
				}
				task := &models.Task{Name: "action", Type: models.TaskTypeScript, ScriptID: script.ID, Enabled: true}
				if err := st.CreateTask(ctx, task); err != nil {
					t.Fatal(err)
				}
				schedule := &models.Schedule{Name: "action", TaskID: task.ID, Type: models.ScheduleTypeManual, Target: models.Target{Type: "node", NodeIDs: []string{"node-a"}}, Enabled: true}
				if err := st.CreateSchedule(ctx, schedule); err != nil {
					t.Fatal(err)
				}
				return task, schedule
			},
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			h, st, repo, ctx := newMonitoringOrchestratorHub(t)
			task, schedule := tc.setup(t, ctx, st)
			target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
			if err := repo.Create(ctx, &target); err != nil {
				t.Fatal(err)
			}
			rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: schedule.ID, Operator: "status_eq", Expected: "503", Enabled: true}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			claim := claimMonitoringRule(t, ctx, repo, target, rule)
			err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
			if !errors.Is(err, monitoring.ErrKnownFailure) {
				t.Fatalf("err=%v, want known failure", err)
			}
			if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
				t.Fatal(err)
			}
			events, err := repo.RuleEvents(ctx, rule.ID)
			if err != nil || len(events) != 1 || events[0].Status != "failed" || len(events[0].Steps) != 0 {
				t.Fatalf("events=%+v err=%v", events, err)
			}
			if !strings.Contains(events[0].Error, "node-a/action: "+tc.wantReason) || strings.Contains(events[0].Error, "sql:") {
				t.Fatalf("preflight event error=%q, want node/reason without SQL details", events[0].Error)
			}
			var executions int
			if err := st.DB().QueryRow(`SELECT COUNT(*) FROM executions WHERE task_id=?`, task.ID).Scan(&executions); err != nil {
				t.Fatal(err)
			}
			if executions != 0 {
				t.Fatalf("preflight failure created %d executions", executions)
			}
		})
	}
}

func TestMonitoringOrchestratorTransientCreateFailureIsUnknown(t *testing.T) {
	base, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer base.Close()
	base.DB().SetMaxOpenConns(1)
	ctx := context.Background()
	createMonitoringNode(t, ctx, base, "node-a")
	action := createMonitoringSchedule(t, ctx, base, "action", []string{"node-a"})
	compensate := createMonitoringSchedule(t, ctx, base, "compensate", []string{"node-a"})
	st := &monitoringBusyCreateStore{SQLiteStore: base, failTaskID: action.TaskID}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	revisions := hub.NewRevisionManager(st)
	sessions := hub.NewSessionManager()
	nodes := hub.NewNodeManager(st, revisions)
	syncMgr := hub.NewSyncManager(st, revisions, sessions, nodes)
	execs := hub.NewExecutionManager(st, sessions, nodes, logger)
	tasks := hub.NewTaskManager(st, revisions, syncMgr)
	schedules := hub.NewScheduleManager(st, revisions, syncMgr, nodes, execs)
	repo := monitoring.NewRepository(base.DB())
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, CompensateScheduleID: compensate.ID, Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err = runMonitoringRule(ctx, rule, claim, st, nodes, tasks, schedules, execs)
	if err == nil || errors.Is(err, monitoring.ErrKnownFailure) || errors.Is(err, monitoring.ErrCompensated) {
		t.Fatalf("err=%v, want unknown outcome", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "unknown" || len(events[0].Steps) != 0 {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	var executions int
	if err := base.DB().QueryRow(`SELECT COUNT(*) FROM executions WHERE task_id=?`, action.TaskID).Scan(&executions); err != nil {
		t.Fatal(err)
	}
	if executions != 0 {
		t.Fatalf("transient create failure created %d executions", executions)
	}
	var compensationExecutions int
	if err := base.DB().QueryRow(`SELECT COUNT(*) FROM executions WHERE task_id=?`, compensate.TaskID).Scan(&compensationExecutions); err != nil {
		t.Fatal(err)
	}
	if compensationExecutions != 0 {
		t.Fatalf("transient create failure triggered %d compensation executions", compensationExecutions)
	}
}

func TestMonitoringOrchestratorCompensationExcludesSkippedBlocked(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	nodes := []string{"node-a", "node-b"}
	statuses := []string{models.ExecStatusSkipped, models.ExecStatusBlocked}
	for _, id := range nodes {
		createMonitoringNode(t, ctx, st, id)
	}
	action := createMonitoringSchedule(t, ctx, st, "action", nodes)
	compensate := createMonitoringSchedule(t, ctx, st, "compensate", nodes)
	updateMu := &sync.Mutex{}
	for i, id := range nodes {
		h.Sessions().Register(&monitoringScriptedAgent{nodeID: id, store: st, updateMu: updateMu, statusBySchedule: map[string]string{
			action.ID: statuses[i],
		}})
	}
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, CompensateScheduleID: compensate.ID, Operator: "status_eq", Expected: "503", BatchSize: 2, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if !errors.Is(err, monitoring.ErrKnownFailure) {
		t.Fatalf("err=%v, want known failure", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "failed" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	for _, reason := range []string{"execution skipped", "execution blocked"} {
		if !strings.Contains(events[0].Error, reason) {
			t.Fatalf("event error=%q missing %q", events[0].Error, reason)
		}
	}
	if strings.Contains(events[0].Error, "sql:") {
		t.Fatalf("event error leaked SQL: %q", events[0].Error)
	}
	for _, step := range events[0].Steps {
		if step.Phase == "compensate" {
			t.Fatalf("non-compensable action produced compensation step: %+v", step)
		}
	}
}

func TestMonitoringOrchestratorCanceledActionIsCompensated(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	createMonitoringNode(t, ctx, st, "node-a")
	action := createMonitoringSchedule(t, ctx, st, "action", []string{"node-a"})
	compensate := createMonitoringSchedule(t, ctx, st, "compensate", []string{"node-a"})
	h.Sessions().Register(&monitoringScriptedAgent{nodeID: "node-a", store: st, updateMu: &sync.Mutex{}, statusBySchedule: map[string]string{
		action.ID:     models.ExecStatusCanceled,
		compensate.ID: models.ExecStatusSuccess,
	}})
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, CompensateScheduleID: compensate.ID, Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if !errors.Is(err, monitoring.ErrCompensated) {
		t.Fatalf("err=%v, want compensated", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "compensated" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	phases := map[string]string{}
	for _, step := range events[0].Steps {
		phases[step.Phase] = step.Status
	}
	if phases["action"] != models.ExecStatusCanceled || phases["compensate"] != models.ExecStatusSuccess {
		t.Fatalf("steps=%+v", events[0].Steps)
	}
	if !strings.Contains(events[0].Error, "node-a/action: execution canceled") {
		t.Fatalf("compensated event error=%q", events[0].Error)
	}
}

func TestMonitoringOrchestratorFailedActionIsCompensated(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	createMonitoringNode(t, ctx, st, "node-a")
	action := createMonitoringSchedule(t, ctx, st, "action", []string{"node-a"})
	compensate := createMonitoringSchedule(t, ctx, st, "compensate", []string{"node-a"})
	h.Sessions().Register(&monitoringScriptedAgent{nodeID: "node-a", store: st, updateMu: &sync.Mutex{}, statusBySchedule: map[string]string{
		action.ID:     models.ExecStatusFailed,
		compensate.ID: models.ExecStatusSuccess,
	}})
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, CompensateScheduleID: compensate.ID, Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if !errors.Is(err, monitoring.ErrCompensated) {
		t.Fatalf("err=%v, want compensated", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "compensated" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if !strings.Contains(events[0].Error, "node-a/action: execution failed") {
		t.Fatalf("compensated event error=%q", events[0].Error)
	}
	phases := map[string]string{}
	for _, step := range events[0].Steps {
		phases[step.Phase] = step.Status
	}
	if phases["action"] != models.ExecStatusFailed || phases["compensate"] != models.ExecStatusSuccess {
		t.Fatalf("steps=%+v", events[0].Steps)
	}
}

func TestMonitoringOrchestratorRuleStateReasons(t *testing.T) {
	for _, tc := range []struct {
		name   string
		mutate func(t *testing.T, ctx context.Context, st *store.SQLiteStore, repo *monitoring.Repository, rule monitoring.Rule)
		reason string
	}{
		{
			name: "disabled",
			mutate: func(t *testing.T, ctx context.Context, st *store.SQLiteStore, repo *monitoring.Repository, rule monitoring.Rule) {
				rule.Enabled = false
				if err := repo.UpdateRule(ctx, rule); err != nil {
					t.Fatal(err)
				}
			},
			reason: "rule disabled",
		},
		{
			name: "interrupted",
			mutate: func(t *testing.T, ctx context.Context, st *store.SQLiteStore, repo *monitoring.Repository, rule monitoring.Rule) {
				if _, err := st.DB().Exec(`UPDATE monitoring_rules SET interrupted=1 WHERE id=?`, rule.ID); err != nil {
					t.Fatal(err)
				}
			},
			reason: "rule interrupted",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h, st, repo, ctx := newMonitoringOrchestratorHub(t)
			createMonitoringNode(t, ctx, st, "node-a")
			schedule := createMonitoringSchedule(t, ctx, st, "action", []string{"node-a"})
			target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
			if err := repo.Create(ctx, &target); err != nil {
				t.Fatal(err)
			}
			rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: schedule.ID, Operator: "status_eq", Expected: "503", Enabled: true}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			claim := claimMonitoringRule(t, ctx, repo, target, rule)
			tc.mutate(t, ctx, st, repo, rule)
			err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
			if !errors.Is(err, monitoring.ErrKnownFailure) {
				t.Fatalf("err=%v, want known failure", err)
			}
			if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
				t.Fatal(err)
			}
			events, err := repo.RuleEvents(ctx, rule.ID)
			if err != nil || len(events) != 1 || events[0].Status != "failed" {
				t.Fatalf("events=%+v err=%v", events, err)
			}
			if !strings.Contains(events[0].Error, "node-a/action: "+tc.reason) || strings.Contains(events[0].Error, "sql:") {
				t.Fatalf("event error=%q, want %q", events[0].Error, tc.reason)
			}
		})
	}
}

func TestMonitoringKnownFailureReasonSeparatesNodeAndCapability(t *testing.T) {
	for _, tc := range []struct {
		err    error
		reason string
	}{
		{errors.New("monitoring node unavailable"), "node unavailable"},
		{errors.New("monitoring node capability missing"), "capability missing"},
		{errors.New("node node-a is not online (status=offline)"), "node unavailable"},
	} {
		if got := monitoringKnownFailureReason("", tc.err); got != tc.reason {
			t.Fatalf("reason(%q)=%q, want %q", tc.err, got, tc.reason)
		}
	}
}

func TestMonitoringOrchestratorKnownFailureIncludesVerifyPhase(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	createMonitoringNode(t, ctx, st, "node-a")
	action := createMonitoringSchedule(t, ctx, st, "action", []string{"node-a"})
	verify := createMonitoringSchedule(t, ctx, st, "verify", []string{"node-a"})
	h.Sessions().Register(&monitoringScriptedAgent{nodeID: "node-a", store: st, updateMu: &sync.Mutex{}, statusBySchedule: map[string]string{
		action.ID: models.ExecStatusSuccess,
		verify.ID: models.ExecStatusFailed,
	}})
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, VerifyScheduleID: verify.ID, Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if !errors.Is(err, monitoring.ErrKnownFailure) {
		t.Fatalf("err=%v, want known failure", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "failed" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if !strings.Contains(events[0].Error, "node-a/verify: execution failed") || strings.Contains(events[0].Error, "sql:") {
		t.Fatalf("verify failure error=%q", events[0].Error)
	}
}

func TestMonitoringOrchestratorKnownFailureStatusReasons(t *testing.T) {
	tests := []struct {
		status string
		reason string
	}{
		{models.ExecStatusSkipped, "execution skipped"},
		{models.ExecStatusBlocked, "execution blocked"},
		{models.ExecStatusCanceled, "execution canceled"},
		{models.ExecStatusTimedOut, "execution timed out"},
	}
	for _, tc := range tests {
		t.Run(tc.status, func(t *testing.T) {
			h, st, repo, ctx := newMonitoringOrchestratorHub(t)
			createMonitoringNode(t, ctx, st, "node-a")
			action := createMonitoringSchedule(t, ctx, st, "action", []string{"node-a"})
			h.Sessions().Register(&monitoringScriptedAgent{nodeID: "node-a", store: st, updateMu: &sync.Mutex{}, statusBySchedule: map[string]string{action.ID: tc.status}})
			target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
			if err := repo.Create(ctx, &target); err != nil {
				t.Fatal(err)
			}
			rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, Operator: "status_eq", Expected: "503", Enabled: true}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			claim := claimMonitoringRule(t, ctx, repo, target, rule)
			err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
			if !errors.Is(err, monitoring.ErrKnownFailure) {
				t.Fatalf("err=%v, want known failure", err)
			}
			if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
				t.Fatal(err)
			}
			events, err := repo.RuleEvents(ctx, rule.ID)
			if err != nil || len(events) != 1 || events[0].Status != "failed" {
				t.Fatalf("events=%+v err=%v", events, err)
			}
			if !strings.Contains(events[0].Error, "node-a/action: "+tc.reason) || strings.Contains(events[0].Error, "sql:") {
				t.Fatalf("event error=%q, want %q without SQL", events[0].Error, tc.reason)
			}
		})
	}
}

func TestMonitoringOrchestratorCompensationUsesOriginalBatchAcrossBatches(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	nodes := []string{"node-a", "node-b", "node-c", "node-d"}
	for _, id := range nodes {
		createMonitoringNode(t, ctx, st, id)
	}
	action := createMonitoringSchedule(t, ctx, st, "action", nodes)
	compensate := createMonitoringSchedule(t, ctx, st, "compensate", nodes)
	updateMu := &sync.Mutex{}
	for _, id := range []string{"node-a", "node-b", "node-c"} {
		h.Sessions().Register(&monitoringScriptedAgent{nodeID: id, store: st, updateMu: updateMu, statusBySchedule: map[string]string{
			action.ID:     models.ExecStatusSuccess,
			compensate.ID: models.ExecStatusSuccess,
		}})
	}
	// node-d has no agent: second batch fails after node-c succeeds.
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, CompensateScheduleID: compensate.ID, Operator: "status_eq", Expected: "503", BatchSize: 2, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if !errors.Is(err, monitoring.ErrCompensated) {
		t.Fatalf("err=%v, want compensated", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "compensated" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	batches := map[string]int{}
	order := []string{}
	for _, step := range events[0].Steps {
		if step.Phase == "compensate" {
			batches[step.NodeID] = step.Batch
			order = append(order, step.NodeID)
		}
	}
	if strings.Join(order, ",") != "node-c,node-b,node-a" {
		t.Fatalf("compensation order=%v", order)
	}
	if batches["node-c"] != 1 || batches["node-b"] != 0 || batches["node-a"] != 0 {
		t.Fatalf("compensation batches=%v", batches)
	}
}

func TestMonitoringOrchestratorUnpersistedDispatchFailureIsUnknown(t *testing.T) {
	base, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer base.Close()
	base.DB().SetMaxOpenConns(1)
	ctx := context.Background()
	createMonitoringNode(t, ctx, base, "node-a")
	schedule := createMonitoringSchedule(t, ctx, base, "action", []string{"node-a"})
	st := &monitoringFailingUpdateStore{SQLiteStore: base}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	revisions := hub.NewRevisionManager(st)
	sessions := hub.NewSessionManager()
	nodes := hub.NewNodeManager(st, revisions)
	syncMgr := hub.NewSyncManager(st, revisions, sessions, nodes)
	execs := hub.NewExecutionManager(st, sessions, nodes, logger)
	tasks := hub.NewTaskManager(st, revisions, syncMgr)
	schedules := hub.NewScheduleManager(st, revisions, syncMgr, nodes, execs)
	repo := monitoring.NewRepository(base.DB())
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: schedule.ID, Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err = runMonitoringRule(ctx, rule, claim, st, nodes, tasks, schedules, execs)
	if err == nil || errors.Is(err, monitoring.ErrKnownFailure) || errors.Is(err, monitoring.ErrCompensated) {
		t.Fatalf("err=%v, want unknown outcome", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "unknown" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	var executionStatus string
	if err := base.DB().QueryRow(`SELECT status FROM executions WHERE task_id=(SELECT task_id FROM schedules WHERE id=?)`, schedule.ID).Scan(&executionStatus); err != nil {
		t.Fatal(err)
	}
	if executionStatus != models.ExecStatusPending {
		t.Fatalf("execution status=%s, want PENDING after persistence failure", executionStatus)
	}
}

type monitoringFailingUpdateStore struct {
	*store.SQLiteStore
}

func (s *monitoringFailingUpdateStore) UpdateExecution(context.Context, *models.Execution) error {
	return errors.New("injected update failure")
}

type monitoringBusyCreateStore struct {
	*store.SQLiteStore
	failTaskID string
}

func (s *monitoringBusyCreateStore) CreateExecution(ctx context.Context, execution *models.Execution) error {
	if execution.TaskID == s.failTaskID {
		return errors.New("database is locked (5) (SQLITE_BUSY)")
	}
	return s.SQLiteStore.CreateExecution(ctx, execution)
}

func TestMonitoringOrchestratorCompensationAggregatesFailuresAndUsesActionBatch(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	nodes := []string{"node-a", "node-b", "node-c"}
	for _, id := range nodes {
		createMonitoringNode(t, ctx, st, id)
	}
	action := createMonitoringSchedule(t, ctx, st, "action", nodes)
	compensate := createMonitoringSchedule(t, ctx, st, "compensate", nodes)
	updateMu := &sync.Mutex{}
	h.Sessions().Register(&monitoringScriptedAgent{nodeID: "node-a", store: st, updateMu: updateMu, statusBySchedule: map[string]string{
		action.ID:     models.ExecStatusSuccess,
		compensate.ID: models.ExecStatusSuccess,
	}})
	h.Sessions().Register(&monitoringScriptedAgent{nodeID: "node-b", store: st, updateMu: updateMu, statusBySchedule: map[string]string{
		action.ID:     models.ExecStatusSuccess,
		compensate.ID: models.ExecStatusFailed,
	}})
	// node-c has no agent: its action dispatch fails in the second batch.
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, CompensateScheduleID: compensate.ID, Operator: "status_eq", Expected: "503", BatchSize: 2, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if err == nil || errors.Is(err, monitoring.ErrCompensated) || errors.Is(err, monitoring.ErrKnownFailure) {
		t.Fatalf("err=%v, want aggregated compensation failure", err)
	}
	var compensation *monitoring.CompensationFailure
	if !errors.As(err, &compensation) {
		t.Fatalf("err type=%T, want CompensationFailure", err)
	}
	if !strings.Contains(compensation.SafeMessage(), "node-b") || !strings.Contains(compensation.SafeMessage(), "execution failed") {
		t.Fatalf("compensation summary=%q", compensation.SafeMessage())
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "unknown" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if !strings.Contains(events[0].Error, "node-b") || !strings.Contains(events[0].Error, "compensation failed") {
		t.Fatalf("event error=%q", events[0].Error)
	}
	compensated := []monitoring.RuleStep{}
	actionBatch := map[string]int{}
	for _, step := range events[0].Steps {
		if step.Phase == "action" {
			actionBatch[step.NodeID] = step.Batch
		}
		if step.Phase == "compensate" {
			compensated = append(compensated, step)
		}
	}
	if actionBatch["node-c"] != 1 {
		t.Fatalf("node-c action batch=%d, want second batch", actionBatch["node-c"])
	}
	if len(compensated) != 2 {
		t.Fatalf("compensation steps=%+v, want both completed nodes", compensated)
	}
	if compensated[0].NodeID != "node-b" || compensated[0].Batch != 0 || compensated[0].Status != models.ExecStatusFailed {
		t.Fatalf("first compensation step=%+v", compensated[0])
	}
	if compensated[1].NodeID != "node-a" || compensated[1].Batch != 0 || compensated[1].Status != models.ExecStatusSuccess {
		t.Fatalf("second compensation step=%+v", compensated[1])
	}
}

func TestMonitoringOrchestratorCompensationFailureIsNotCompensated(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	createMonitoringNode(t, ctx, st, "node-a")
	action := createMonitoringSchedule(t, ctx, st, "action", []string{"node-a"})
	verify := createMonitoringSchedule(t, ctx, st, "verify", []string{"node-a"})
	compensate := createMonitoringSchedule(t, ctx, st, "compensate", []string{"node-a"})
	agent := &monitoringScriptedAgent{nodeID: "node-a", store: st, updateMu: &sync.Mutex{}, statusBySchedule: map[string]string{
		action.ID:     models.ExecStatusSuccess,
		verify.ID:     models.ExecStatusFailed,
		compensate.ID: models.ExecStatusFailed,
	}}
	h.Sessions().Register(agent)
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, VerifyScheduleID: verify.ID, CompensateScheduleID: compensate.ID, Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if err == nil || errors.Is(err, monitoring.ErrCompensated) || !strings.Contains(err.Error(), "compensation failed") {
		t.Fatalf("err=%v, want compensation failure", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "unknown" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if len(events[0].Steps) != 3 {
		t.Fatalf("steps=%+v", events[0].Steps)
	}
	if events[0].Steps[0].Phase != "action" || events[0].Steps[0].Status != models.ExecStatusSuccess ||
		events[0].Steps[1].Phase != "verify" || events[0].Steps[1].Status != models.ExecStatusFailed ||
		events[0].Steps[2].Phase != "compensate" || events[0].Steps[2].Status != models.ExecStatusFailed {
		t.Fatalf("steps=%+v", events[0].Steps)
	}
}

func TestMonitoringOrchestratorCompensatesCompletedNodesInReverseOrder(t *testing.T) {
	h, st, repo, ctx := newMonitoringOrchestratorHub(t)
	nodes := []string{"node-a", "node-b"}
	for _, id := range nodes {
		createMonitoringNode(t, ctx, st, id)
	}
	action := createMonitoringSchedule(t, ctx, st, "action", nodes)
	verify := createMonitoringSchedule(t, ctx, st, "verify", nodes)
	compensate := createMonitoringSchedule(t, ctx, st, "compensate", nodes)
	updateMu := &sync.Mutex{}
	for _, id := range nodes {
		h.Sessions().Register(&monitoringScriptedAgent{nodeID: id, store: st, updateMu: updateMu, statusBySchedule: map[string]string{
			action.ID:     models.ExecStatusSuccess,
			verify.ID:     models.ExecStatusFailed,
			compensate.ID: models.ExecStatusSuccess,
		}})
	}
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: action.ID, VerifyScheduleID: verify.ID, CompensateScheduleID: compensate.ID, Operator: "status_eq", Expected: "503", BatchSize: 2, Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	claim := claimMonitoringRule(t, ctx, repo, target, rule)
	err := runMonitoringOrchestration(t, ctx, h, st, rule, claim)
	if !errors.Is(err, monitoring.ErrCompensated) {
		t.Fatalf("err=%v, want compensated", err)
	}
	if err := repo.FinishRule(ctx, rule.ID, claim, err); err != nil {
		t.Fatal(err)
	}
	events, err := repo.RuleEvents(ctx, rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "compensated" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	if !strings.Contains(events[0].Error, "action or verification failed; configured compensation succeeded") ||
		!strings.Contains(events[0].Error, "/verify: execution failed") {
		t.Fatalf("compensated event error=%q", events[0].Error)
	}
	if len(events[0].Steps) != 6 {
		t.Fatalf("steps=%+v", events[0].Steps)
	}
	phases := map[string]int{}
	compensated := []string{}
	for _, step := range events[0].Steps {
		phases[step.Phase]++
		if step.Phase == "compensate" {
			compensated = append(compensated, step.NodeID)
		}
		if step.Phase == "compensate" && step.Status != models.ExecStatusSuccess {
			t.Fatalf("compensation status=%s", step.Status)
		}
	}
	if phases["action"] != 2 || phases["verify"] != 2 || phases["compensate"] != 2 {
		t.Fatalf("phases=%v", phases)
	}
	want := []string{"node-b", "node-a"}
	if strings.Join(compensated, ",") != strings.Join(want, ",") {
		t.Fatalf("compensation order=%v want %v", compensated, want)
	}
}
