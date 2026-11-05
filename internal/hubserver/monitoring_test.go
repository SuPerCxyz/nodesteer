package hubserver

import (
	"bytes"
	"context"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent"
	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestWaitMonitoringExecutionRequiresSuccess(t *testing.T) {
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	execution := &models.Execution{ID: "execution", TaskID: "task", NodeID: "node", Status: models.ExecStatusFailed}
	if err := st.CreateExecution(context.Background(), execution); err != nil {
		t.Fatal(err)
	}
	if err := waitMonitoringExecution(context.Background(), st, execution.ID); err == nil {
		t.Fatal("failure allowed progression")
	}
	execution.Status = models.ExecStatusSuccess
	if err := st.UpdateExecution(context.Background(), execution); err != nil {
		t.Fatal(err)
	}
	if err := waitMonitoringExecution(context.Background(), st, execution.ID); err != nil {
		t.Fatal(err)
	}
	execution.Status = models.ExecStatusPending
	if err := st.UpdateExecution(context.Background(), execution); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	if err := waitMonitoringExecution(ctx, st, execution.ID); err == nil {
		t.Fatal("unknown outcome allowed progression")
	}
}

func TestMonitoringPreflightFailureIsKnown(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	dir := t.TempDir()
	h, err := New(Config{GatewayAddr: ":8443", RegistrationToken: "test-registration", DataDir: dir, ArtifactDir: filepath.Join(dir, "artifacts"), BaseURL: "http://127.0.0.1:8080", HeartbeatTimeout: 10 * time.Second, SessionTTL: time.Hour, DevNoAuth: true}, logger)
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	ctx := context.Background()
	st := h.Store().(*store.SQLiteStore)
	repo := monitoring.NewRepository(st.DB())
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	tasks := hub.NewTaskManager(st, h.Revisions(), h.SyncMgr())
	for _, tc := range []struct {
		name            string
		scheduleEnabled bool
		taskEnabled     bool
	}{
		{"schedule disabled", false, true},
		{"task disabled", true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			task := models.Task{Name: tc.name, Type: models.TaskTypeCommand, Command: "true", Enabled: tc.taskEnabled}
			if err := st.CreateTask(ctx, &task); err != nil {
				t.Fatal(err)
			}
			schedule := models.Schedule{Name: tc.name, TaskID: task.ID, Type: models.ScheduleTypeManual, Enabled: tc.scheduleEnabled}
			if err := st.CreateSchedule(ctx, &schedule); err != nil {
				t.Fatal(err)
			}
			rule := monitoring.Rule{Name: tc.name, TargetID: target.ID, ScheduleID: schedule.ID, Operator: "status_eq", Expected: "503", Enabled: true}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			claim, err := repo.ClaimRule(ctx, rule, target, true, time.Now(), time.Minute)
			if err != nil || claim == "" {
				t.Fatalf("claim=%s err=%v", claim, err)
			}
			err = runMonitoringRule(ctx, rule, claim, st, h.Nodes(), tasks, h.Schedules(), h.ExecMgr())
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
			if !strings.Contains(events[0].Error, tc.name) || strings.Contains(events[0].Error, "sql:") {
				t.Fatalf("preflight error=%q, want %q without SQL details", events[0].Error, tc.name)
			}
		})
	}
}

func TestMonitoringWaitDurationIncludesRetries(t *testing.T) {
	duration, err := monitoringWaitDuration(hub.ExecutionStrategy{Timeout: 120, Retry: 2})
	if err != nil || duration != 390*time.Second {
		t.Fatalf("duration=%v error=%v", duration, err)
	}
	if _, err := monitoringWaitDuration(hub.ExecutionStrategy{Timeout: int(^uint(0) >> 1), Retry: 2}); err == nil {
		t.Fatal("overflowing duration accepted")
	}
}

func TestMonitoringAPITriggersRealAgentCommand(t *testing.T) {
	for _, location := range []string{"hub", "agent", "agent-untrusted"} {
		t.Run(location, func(t *testing.T) { testMonitoringAPITriggersRealAgentCommand(t, location) })
	}
}

func testMonitoringAPITriggersRealAgentCommand(t *testing.T, location string) {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	probe := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`{"remaining":0}`)) }))
	defer probe.Close()
	dir := t.TempDir()
	h, err := New(Config{GatewayAddr: ":8443", RegistrationToken: "test-registration", DataDir: dir, ArtifactDir: filepath.Join(dir, "artifacts"), BaseURL: "http://127.0.0.1:8080", HeartbeatTimeout: 10 * time.Second, SessionTTL: time.Hour, DevNoAuth: true, MonitoringAllowedCIDRs: []string{"127.0.0.0/8"}}, logger)
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	server := &http.Server{Handler: h.GatewayHandler()}
	go server.Serve(listener)
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	agentURL, caFile := "ws://"+listener.Addr().String(), ""
	if location == "agent" {
		tlsServer := httptest.NewTLSServer(h.GatewayHandler())
		defer tlsServer.Close()
		agentURL = "wss://" + strings.TrimPrefix(tlsServer.URL, "https://")
		caFile = filepath.Join(t.TempDir(), "ca.pem")
		if err := os.WriteFile(caFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: tlsServer.Certificate().Raw}), 0600); err != nil {
			t.Fatal(err)
		}
	}
	a, err := agent.New(agent.Config{HubURL: agentURL, TLSCAFile: caFile, RegistrationToken: "test-registration", DeploymentMode: models.DeploymentModeNative, DataDir: t.TempDir(), AgentVersion: "test", MonitoringAllowedCIDRs: []string{"127.0.0.0/8"}}, logger)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	go a.Run(ctx)
	deadline := time.Now().Add(10 * time.Second)
	for h.Sessions().Count() == 0 && time.Now().Before(deadline) {
		time.Sleep(25 * time.Millisecond)
	}
	nodes, err := h.Nodes().ListNodes(ctx)
	if err != nil || len(nodes) != 1 {
		t.Fatalf("nodes=%d err=%v", len(nodes), err)
	}
	handler := h.ServeMux()
	post := func(path string, value any, output any) {
		t.Helper()
		data, marshalErr := json.Marshal(value)
		if marshalErr != nil {
			t.Fatal(marshalErr)
		}
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(data))
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK && rec.Code != http.StatusCreated {
			t.Fatalf("POST %s = %d %s", path, rec.Code, rec.Body.String())
		}
		if err := json.Unmarshal(rec.Body.Bytes(), output); err != nil {
			t.Fatal(err)
		}
	}
	marker := filepath.Join(t.TempDir(), "switched")
	var task models.Task
	post("/api/tasks", models.Task{Name: "switch", Type: models.TaskTypeCommand, Command: "if test -e " + marker + ".attempt; then touch " + marker + "; else touch " + marker + ".attempt; exit 1; fi", Enabled: true}, &task)
	var schedule models.Schedule
	post("/api/schedules", models.Schedule{TaskID: task.ID, Name: "switch", Type: models.ScheduleTypeManual, Target: models.Target{Type: "node", NodeIDs: []string{nodes[0].ID}}, Retry: 1, Enabled: true}, &schedule)
	var target monitoring.Target
	targetRequest := monitoring.Target{Name: "service", URL: probe.URL, JSONPath: []string{"remaining"}, IntervalSec: 5, TimeoutSec: 2, Enabled: true}
	if location != "hub" {
		targetRequest.SourceNodeID = nodes[0].ID
	}
	post("/api/monitoring/http-targets", targetRequest, &target)
	var rule monitoring.Rule
	post("/api/monitoring/rules", monitoring.Rule{Name: "expiry", TargetID: target.ID, ScheduleID: schedule.ID, Operator: "eq", Expected: "0", Enabled: true}, &rule)
	go h.monitoring.Run(ctx)
	if location == "agent-untrusted" {
		deadline = time.Now().Add(5 * time.Second)
		for time.Now().Before(deadline) {
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/monitoring/http-targets/"+target.ID+"/observations", nil))
			var observations []monitoring.StoredObservation
			if json.Unmarshal(rec.Body.Bytes(), &observations) == nil && len(observations) > 0 {
				if observations[0].Error == "" {
					t.Fatal("untrusted Hub probe succeeded")
				}
				if _, err := os.Stat(marker); err == nil {
					t.Fatal("untrusted probe triggered task")
				}
				return
			}
			time.Sleep(50 * time.Millisecond)
		}
		t.Fatal("untrusted Hub probe did not produce an observation")
	}
	deadline = time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(marker); err == nil {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if _, err := os.Stat(marker); err != nil {
		t.Fatalf("command did not execute: %v", err)
	}
	deadline = time.Now().Add(5 * time.Second)
	var events []monitoring.RuleEvent
	for time.Now().Before(deadline) {
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/monitoring/rules/"+rule.ID+"/events", nil))
		if err := json.Unmarshal(rec.Body.Bytes(), &events); err == nil && len(events) == 1 && events[0].Status == "completed" {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if len(events) != 1 || events[0].Status != "completed" || string(events[0].ExecutionIDs) == "[]" {
		t.Fatalf("events=%+v", events)
	}
}

func TestMonitoringBatchVerificationAndCompensationRealAgents(t *testing.T) {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	probe := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusServiceUnavailable) }))
	defer probe.Close()
	dir := t.TempDir()
	h, err := New(Config{GatewayAddr: ":8443", RegistrationToken: "test-registration", DataDir: dir, ArtifactDir: filepath.Join(dir, "artifacts"), BaseURL: "http://127.0.0.1:8080", HeartbeatTimeout: 10 * time.Second, SessionTTL: time.Hour, DevNoAuth: true, MonitoringAllowedCIDRs: []string{"127.0.0.0/8"}}, logger)
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	server := &http.Server{Handler: h.GatewayHandler()}
	go server.Serve(listener)
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	for i := 0; i < 2; i++ {
		a, err := agent.New(agent.Config{HubURL: "ws://" + listener.Addr().String(), RegistrationToken: "test-registration", DeploymentMode: models.DeploymentModeNative, DataDir: t.TempDir(), AgentVersion: "test"}, logger)
		if err != nil {
			t.Fatal(err)
		}
		defer a.Close()
		go a.Run(ctx)
	}
	deadline := time.Now().Add(10 * time.Second)
	for h.Sessions().Count() < 2 && time.Now().Before(deadline) {
		time.Sleep(25 * time.Millisecond)
	}
	nodes, err := h.Nodes().ListNodes(ctx)
	if err != nil || len(nodes) != 2 {
		t.Fatalf("nodes=%d err=%v", len(nodes), err)
	}
	ids := []string{nodes[0].ID, nodes[1].ID}
	handler := h.ServeMux()
	post := func(path string, value any, out any) {
		t.Helper()
		data, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, path, bytes.NewReader(data)))
		if rec.Code != http.StatusOK && rec.Code != http.StatusCreated {
			t.Fatalf("POST %s = %d %s", path, rec.Code, rec.Body.String())
		}
		if err := json.Unmarshal(rec.Body.Bytes(), out); err != nil {
			t.Fatal(err)
		}
	}
	marker := filepath.Join(t.TempDir(), "actions")
	createSchedule := func(name, command string) models.Schedule {
		t.Helper()
		var task models.Task
		post("/api/tasks", models.Task{Name: name, Type: models.TaskTypeCommand, Command: command, Enabled: true}, &task)
		var schedule models.Schedule
		post("/api/schedules", models.Schedule{Name: name, TaskID: task.ID, Type: models.ScheduleTypeManual, Target: models.Target{Type: "node", NodeIDs: ids}, Enabled: true}, &schedule)
		return schedule
	}
	action := createSchedule("action", "printf a >> "+marker)
	verify := createSchedule("verify", "exit 1")
	compensate := createSchedule("compensate", "printf c >> "+marker)
	var target monitoring.Target
	post("/api/monitoring/http-targets", monitoring.Target{Name: "service", URL: probe.URL, IntervalSec: 5, TimeoutSec: 2, Enabled: true}, &target)
	var rule monitoring.Rule
	post("/api/monitoring/rules", monitoring.Rule{Name: "batch", TargetID: target.ID, ScheduleID: action.ID, VerifyScheduleID: verify.ID, CompensateScheduleID: compensate.ID, BatchSize: 2, Operator: "status_eq", Expected: "503", Enabled: true}, &rule)
	go h.monitoring.Run(ctx)
	deadline = time.Now().Add(20 * time.Second)
	var events []monitoring.RuleEvent
	for time.Now().Before(deadline) {
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/monitoring/rules/"+rule.ID+"/events", nil))
		if json.Unmarshal(rec.Body.Bytes(), &events) == nil && len(events) > 0 && events[0].Status != "pending" {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if len(events) != 1 || events[0].Status != "compensated" || len(events[0].Steps) != 6 {
		t.Fatalf("events=%+v", events)
	}
	data, err := os.ReadFile(marker)
	if err != nil || strings.Count(string(data), "a") != 2 || strings.Count(string(data), "c") != 2 {
		t.Fatalf("actions=%q err=%v", data, err)
	}
}

func TestNodeMetricLongBatchRefreshAndRecovery(t *testing.T) {
	for _, recoverCondition := range []bool{false, true} {
		name := "remains-matched"
		if recoverCondition {
			name = "condition-recovers"
		}
		t.Run(name, func(t *testing.T) { testNodeMetricLongBatch(t, recoverCondition) })
	}
}

func testNodeMetricLongBatch(t *testing.T, recoverCondition bool) {
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	h, err := New(Config{GatewayAddr: ":8443", RegistrationToken: "test-registration", DataDir: dir, ArtifactDir: filepath.Join(dir, "artifacts"), BaseURL: "http://127.0.0.1:8080", HeartbeatTimeout: 10 * time.Second, SessionTTL: time.Hour, DevNoAuth: true}, logger)
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	server := &http.Server{Handler: h.GatewayHandler()}
	go server.Serve(listener)
	defer server.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	for i := 0; i < 2; i++ {
		a, err := agent.New(agent.Config{HubURL: "ws://" + listener.Addr().String(), RegistrationToken: "test-registration", DeploymentMode: models.DeploymentModeNative, DataDir: t.TempDir(), AgentVersion: "test"}, logger)
		if err != nil {
			t.Fatal(err)
		}
		defer a.Close()
		go a.Run(ctx)
	}
	deadline := time.Now().Add(10 * time.Second)
	for h.Sessions().Count() < 2 && time.Now().Before(deadline) {
		time.Sleep(25 * time.Millisecond)
	}
	nodes, err := h.Nodes().ListNodes(ctx)
	if err != nil || len(nodes) != 2 {
		t.Fatalf("nodes=%d err=%v", len(nodes), err)
	}
	st := h.Store().(*store.SQLiteStore)
	repo := monitoring.NewRepository(st.DB())
	var high atomic.Bool
	high.Store(true)
	h.monitoring.SetNodeMetricProbe(func(_ context.Context, _ monitoring.Target) (monitoring.HTTPObservation, error) {
		value := json.Number("95")
		if !high.Load() {
			value = json.Number("20")
		}
		return monitoring.HTTPObservation{ObservedAt: time.Now().UTC(), HasValue: true, Value: value}, nil
	})
	handler := h.ServeMux()
	post := func(path string, value any, out any) {
		t.Helper()
		data, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, path, bytes.NewReader(data)))
		if rec.Code != http.StatusOK && rec.Code != http.StatusCreated {
			t.Fatalf("POST %s = %d %s", path, rec.Code, rec.Body.String())
		}
		if err := json.Unmarshal(rec.Body.Bytes(), out); err != nil {
			t.Fatal(err)
		}
	}
	marker := filepath.Join(t.TempDir(), "actions")
	var task models.Task
	post("/api/tasks", models.Task{Name: "slow", Type: models.TaskTypeCommand, Command: "sleep 11; printf a >> " + marker, Enabled: true}, &task)
	var schedule models.Schedule
	post("/api/schedules", models.Schedule{Name: "slow", TaskID: task.ID, Type: models.ScheduleTypeManual, Target: models.Target{Type: "node", NodeIDs: []string{nodes[0].ID, nodes[1].ID}}, Timeout: 20, Enabled: true}, &schedule)
	var target monitoring.Target
	post("/api/monitoring/http-targets", monitoring.Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: nodes[0].ID, Metric: "cpu.usage", Series: "cpu.usage", IntervalSec: 5, TimeoutSec: 2, Enabled: true}, &target)
	var rule monitoring.Rule
	post("/api/monitoring/rules", monitoring.Rule{Name: "CPU", TargetID: target.ID, ScheduleID: schedule.ID, Operator: "gt", Expected: "90", BatchSize: 1, Enabled: true}, &rule)
	go h.monitoring.Run(ctx)
	deadline = time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		events, _ := repo.RuleEvents(ctx, rule.ID)
		if len(events) > 0 && len(events[0].Steps) > 0 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	initial, _ := repo.RuleEvents(ctx, rule.ID)
	if len(initial) == 0 || len(initial[0].Steps) == 0 {
		t.Fatal("first node not started")
	}
	if recoverCondition {
		high.Store(false)
	}
	deadline = time.Now().Add(30 * time.Second)
	var events []monitoring.RuleEvent
	for time.Now().Before(deadline) {
		events, _ = repo.RuleEvents(ctx, rule.ID)
		if len(events) > 0 && events[0].Status != "pending" {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if len(events) != 1 {
		t.Fatalf("events=%+v", events)
	}
	if recoverCondition {
		nodeInError := false
		for _, node := range nodes {
			if strings.Contains(events[0].Error, node.ID) {
				nodeInError = true
				break
			}
		}
		if events[0].Status != "failed" || len(events[0].Steps) != 1 ||
			!strings.Contains(events[0].Error, "/action: rule interrupted") || !nodeInError {
			t.Fatalf("recovered condition did not stop next batch as a known failure: %+v", events[0])
		}
		return
	}
	if events[0].Status != "completed" || len(events[0].Steps) != 2 {
		t.Fatalf("long action did not renew observation: %+v", events[0])
	}
}
