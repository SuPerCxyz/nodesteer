package api

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// runCaptureConn 记录 Hub 下发的 RUN_EXECUTION 载荷。
type runCaptureConn struct {
	nodeID string
	sent   chan protocol.Envelope
}

func (c *runCaptureConn) NodeID() string { return c.nodeID }
func (c *runCaptureConn) Send(msg protocol.Envelope) error {
	select {
	case c.sent <- msg:
	default:
	}
	return nil
}
func (c *runCaptureConn) Close() error { return nil }

func (c *runCaptureConn) lastRunPayload(t *testing.T) protocol.RunExecutionPayload {
	t.Helper()
	deadline := time.After(time.Second)
	for {
		select {
		case msg := <-c.sent:
			// 变更通知与执行下发可能共用同一连接，跳过非执行消息。
			if msg.Type != protocol.MsgRunExecution {
				continue
			}
			var p protocol.RunExecutionPayload
			if err := json.Unmarshal(msg.Payload, &p); err != nil {
				t.Fatalf("decode run payload: %v", err)
			}
			return p
		case <-deadline:
			t.Fatal("no RUN_EXECUTION dispatched")
			return protocol.RunExecutionPayload{}
		}
	}
}

// TestTaskAPIExcludesLegacyTargetAndStrategy Task 响应不得再出现目标/执行策略字段。
func TestTaskAPIExcludesLegacyTargetAndStrategy(t *testing.T) {
	c := newContractServer(t)
	rec := c.do(t, http.MethodPost, "/api/tasks", `{
		"name":"legacy-task","type":"command","command":"true","enabled":true,
		"timeout":30,"retry":2,"run_user":"ops","offline_policy":"allow_offline",
		"target":{"type":"label","label_key":"env"},
		"schedule":{"type":"interval","interval_sec":60}
	}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create task = %d %s", rec.Code, rec.Body.String())
	}
	task := decodeResource[models.Task](t, rec)

	rec = c.do(t, http.MethodGet, "/api/tasks/"+task.ID, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("get task = %d %s", rec.Code, rec.Body.String())
	}
	var body map[string]json.RawMessage
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"target", "timeout", "retry", "run_user", "offline_policy", "schedule"} {
		if _, ok := body[key]; ok {
			t.Fatalf("task response must not contain %q: %s", key, rec.Body.String())
		}
	}
}

// TestScheduleAPITargetAndStrategyContract 调度创建必须带目标，manual 可创建，
// 响应包含 name/target/timeout/retry/run_user，策略负数返回 400。
func TestScheduleAPITargetAndStrategyContract(t *testing.T) {
	c := newContractServer(t)
	rec := c.do(t, http.MethodPost, "/api/tasks", `{"name":"sched-task","type":"command","command":"true","enabled":true}`)
	task := decodeResource[models.Task](t, rec)
	if rec.Code != http.StatusOK {
		t.Fatalf("create task = %d %s", rec.Code, rec.Body.String())
	}

	cases := []struct {
		name string
		body string
		want int
	}{
		{"missing target", `{"task_id":"` + task.ID + `","type":"interval","interval_sec":60,"execution_owner":"hub"}`, http.StatusBadRequest},
		{"invalid target", `{"task_id":"` + task.ID + `","type":"interval","interval_sec":60,"target":{"type":"bogus"},"execution_owner":"hub"}`, http.StatusBadRequest},
		{"negative timeout", `{"task_id":"` + task.ID + `","type":"manual","target":{"type":"label","label_key":"env"},"timeout":-1}`, http.StatusBadRequest},
		{"negative retry", `{"task_id":"` + task.ID + `","type":"manual","target":{"type":"label","label_key":"env"},"retry":-1}`, http.StatusBadRequest},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if rec := c.do(t, http.MethodPost, "/api/schedules", tc.body); rec.Code != tc.want {
				t.Fatalf("POST schedule = %d %s, want %d", rec.Code, rec.Body.String(), tc.want)
			}
		})
	}

	rec = c.do(t, http.MethodPost, "/api/schedules",
		`{"task_id":"`+task.ID+`","type":"manual","target":{"type":"label","label_key":"env"},"run_user":"ops","enabled":true}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create manual schedule = %d %s", rec.Code, rec.Body.String())
	}
	sc := decodeResource[models.Schedule](t, rec)
	if sc.Type != models.ScheduleTypeManual || sc.Target.Type != "label" || sc.Timeout != 300 || sc.Retry != 0 || sc.RunUser != "ops" {
		t.Fatalf("unexpected schedule: %+v", sc)
	}
	if sc.Name == "" {
		t.Fatalf("schedule name should be generated: %+v", sc)
	}

	// 部分更新：显式 retry=0 必须保留，未提供的 timeout/name/target 保持不变。
	rec = c.do(t, http.MethodPut, "/api/schedules/"+sc.ID, `{"retry":0}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("partial update = %d %s", rec.Code, rec.Body.String())
	}
	got, err := c.server.schedules.Get(t.Context(), sc.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.Retry != 0 || got.Timeout != 300 || got.Name != sc.Name || got.Target.Type != "label" || got.RunUser != "ops" {
		t.Fatalf("partial update lost fields: %+v", got)
	}
	// 显式空串必须能清空 run_user；未提供时保留旧值。
	if rec := c.do(t, http.MethodPut, "/api/schedules/"+sc.ID, `{"run_user":""}`); rec.Code != http.StatusOK {
		t.Fatalf("clear run_user = %d %s", rec.Code, rec.Body.String())
	}
	got, err = c.server.schedules.Get(t.Context(), sc.ID)
	if err != nil || got.RunUser != "" {
		t.Fatalf("run_user not cleared: %+v err=%v", got, err)
	}
	// timeout 显式 0 归一为默认 300；显式负数拒绝。
	if rec := c.do(t, http.MethodPut, "/api/schedules/"+sc.ID, `{"timeout":0}`); rec.Code != http.StatusOK {
		t.Fatalf("timeout 0 update = %d %s", rec.Code, rec.Body.String())
	}
	if rec := c.do(t, http.MethodPut, "/api/schedules/"+sc.ID, `{"timeout":-1}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("negative timeout update = %d %s, want 400", rec.Code, rec.Body.String())
	}
}

// TestManualRunTargetAndStrategyContract 覆盖 Manual Run 目标解析、默认策略与 400。
func TestManualRunTargetAndStrategyContract(t *testing.T) {
	c := newContractServer(t)
	ctx := t.Context()
	rec := c.do(t, http.MethodPost, "/api/tasks", `{"name":"run-task","type":"command","command":"true","enabled":true}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create task = %d %s", rec.Code, rec.Body.String())
	}
	task := decodeResource[models.Task](t, rec)

	n1 := &models.Node{ID: "run-node-1", AgentID: "run-agent-1", Hostname: "run-node-1",
		Status: models.NodeStatusOnline, Capabilities: map[string]bool{models.CapScript: true}}
	n2 := &models.Node{ID: "run-node-2", AgentID: "run-agent-2", Hostname: "run-node-2",
		Status: models.NodeStatusOnline, Capabilities: map[string]bool{models.CapScript: true}}
	for _, n := range []*models.Node{n1, n2} {
		if err := c.store.UpsertNode(ctx, n); err != nil {
			t.Fatal(err)
		}
	}
	conn1 := &runCaptureConn{nodeID: n1.ID, sent: make(chan protocol.Envelope, 8)}
	conn2 := &runCaptureConn{nodeID: n2.ID, sent: make(chan protocol.Envelope, 8)}
	c.sessions.Register(conn1)
	c.sessions.Register(conn2)

	count := func() int64 {
		n, err := c.store.CountExecutions(ctx, store.ExecutionFilter{})
		if err != nil {
			t.Fatal(err)
		}
		return n
	}

	// 缺少目标 → 400，不创建执行。
	before := count()
	if rec := c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run", `{}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("run without target = %d %s, want 400", rec.Code, rec.Body.String())
	}
	if after := count(); after != before {
		t.Fatalf("run without target created executions: %d -> %d", before, after)
	}
	// 非法目标 → 400。
	if rec := c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run", `{"target":{"type":"bogus"}}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid target = %d %s, want 400", rec.Code, rec.Body.String())
	}
	// 负数策略 → 400。
	if rec := c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run",
		`{"target":{"type":"node","node_ids":["run-node-1"]},"timeout":-1}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("negative timeout = %d %s, want 400", rec.Code, rec.Body.String())
	}
	if rec := c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run",
		`{"target":{"type":"node","node_ids":["run-node-1"]},"retry":-1}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("negative retry = %d %s, want 400", rec.Code, rec.Body.String())
	}

	// target 优先于 node_ids；默认 timeout=300、retry=0、当前用户。
	rec = c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run",
		`{"target":{"type":"node","node_ids":["run-node-1"]},"node_ids":["run-node-2"]}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("run with target = %d %s", rec.Code, rec.Body.String())
	}
	p := conn1.lastRunPayload(t)
	if p.Timeout != 300 || p.Retry != 0 || p.RunUser != "" || p.ScheduleID != "" || p.CreatedAt == "" {
		t.Fatalf("unexpected default payload: %+v", p)
	}
	if len(conn2.sent) != 0 {
		t.Fatal("node_ids must not override explicit target")
	}

	// 兼容 node_ids 数组，并传递显式策略。
	rec = c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run",
		`{"node_ids":["run-node-2"],"timeout":45,"retry":2,"run_user":"ops","offline_policy":"allow_offline"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("run with node_ids = %d %s", rec.Code, rec.Body.String())
	}
	p = conn2.lastRunPayload(t)
	if p.Timeout != 45 || p.Retry != 2 || p.RunUser != "ops" {
		t.Fatalf("explicit strategy not applied: %+v", p)
	}
	if p.ScheduleID != "" {
		t.Fatalf("manual run must not carry schedule_id: %+v", p)
	}

	// 调度计划页「立即执行」：目标与策略以 Schedule 为准，并记录来源调度。
	rec = c.do(t, http.MethodPost, "/api/schedules", `{
		"task_id":"`+task.ID+`","name":"run-from-schedule","type":"manual",
		"target":{"type":"node","node_ids":["run-node-2"]},
		"timeout":77,"retry":3,"run_user":"sched","execution_owner":"hub","enabled":true}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create schedule = %d %s", rec.Code, rec.Body.String())
	}
	sc := decodeResource[models.Schedule](t, rec)
	rec = c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run", `{"schedule_id":"`+sc.ID+`"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("run from schedule = %d %s", rec.Code, rec.Body.String())
	}
	p = conn2.lastRunPayload(t)
	if p.ScheduleID != sc.ID || p.TriggerType != models.TriggerManual ||
		p.Timeout != 77 || p.Retry != 3 || p.RunUser != "sched" {
		t.Fatalf("schedule-run payload mismatch: %+v", p)
	}
	execs, err := c.store.ListExecutions(ctx, store.ExecutionFilter{TaskID: task.ID})
	if err != nil || len(execs) == 0 {
		t.Fatalf("list executions: %+v err=%v", execs, err)
	}
	foundSource := false
	for _, ex := range execs {
		if ex.ScheduleID == sc.ID && ex.TriggerType == models.TriggerManual {
			foundSource = true
			break
		}
	}
	if !foundSource {
		t.Fatalf("schedule-run execution did not record source schedule: %+v", execs)
	}

	// 任务定义与调度未被 Manual Run 修改。
	after, err := c.server.tasks.Get(ctx, task.ID)
	if err != nil || after.Revision != task.Revision {
		t.Fatalf("manual run mutated task: %+v err=%v", after, err)
	}
}

// TestExecutionAPIExposesScheduleAndCreatedAt Execution 响应包含可空 schedule_id 与 created_at。
func TestExecutionAPIExposesScheduleAndCreatedAt(t *testing.T) {
	c := newContractServer(t)
	created := time.Now().UTC().Truncate(time.Second)
	ex := &models.Execution{
		ID: "exec-schedule-source", TaskID: "task-1", NodeID: "node-1",
		ScheduleID: "schedule-1", TriggerType: models.TriggerSchedule,
		Status: models.ExecStatusSuccess, CreatedAt: created,
	}
	if err := c.store.CreateExecution(t.Context(), ex); err != nil {
		t.Fatal(err)
	}
	rec := c.do(t, http.MethodGet, "/api/executions/"+ex.ID, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("get execution = %d %s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["schedule_id"] != "schedule-1" {
		t.Fatalf("schedule_id = %v", body["schedule_id"])
	}
	if body["created_at"] == nil || body["created_at"] == "" {
		t.Fatalf("created_at missing: %v", body)
	}
}
