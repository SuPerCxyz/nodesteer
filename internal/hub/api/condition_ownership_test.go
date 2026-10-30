package api

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// TestConditionOwnershipAndMerge 覆盖：
// - Task 只接受本地条件，Schedule 只接受远程条件；
// - 调度页立即执行合并 Task 本地 + Schedule 远程条件；
// - 任务页试运行只带 Task 本地条件。
func TestConditionOwnershipAndMerge(t *testing.T) {
	c := newContractServer(t)
	ctx := t.Context()

	for _, n := range []*models.Node{
		{ID: "cond-node-1", AgentID: "cond-agent-1", Hostname: "cond-node-1",
			Status: models.NodeStatusOnline, Capabilities: map[string]bool{models.CapScript: true}},
		{ID: "cond-node-2", AgentID: "cond-agent-2", Hostname: "cond-node-2",
			Status: models.NodeStatusOnline},
	} {
		if err := c.store.UpsertNode(ctx, n); err != nil {
			t.Fatal(err)
		}
	}
	conn := &runCaptureConn{nodeID: "cond-node-1", sent: make(chan protocol.Envelope, 16)}
	c.sessions.Register(conn)

	local := `{"type":"local","local":{"metric":"cpu_usage","operator":"<","value":"80"}}`
	remote := `{"type":"remote","remote":{"node_id":"cond-node-2","property":"online","operator":"==","value":"online"}}`

	// Task 写入远程条件 → 400。
	if rec := c.do(t, http.MethodPost, "/api/tasks",
		`{"name":"bad-remote-task","type":"command","command":"true","enabled":true,"condition":`+remote+`}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("task remote condition = %d %s, want 400", rec.Code, rec.Body.String())
	}

	rec := c.do(t, http.MethodPost, "/api/tasks",
		`{"name":"cond-task","type":"command","command":"true","enabled":true,"condition":`+local+`}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create task = %d %s", rec.Code, rec.Body.String())
	}
	task := decodeResource[models.Task](t, rec)

	// Schedule 写入本地条件 → 400。
	if rec := c.do(t, http.MethodPost, "/api/schedules",
		`{"task_id":"`+task.ID+`","type":"manual","target":{"type":"node","node_ids":["cond-node-1"]},"execution_owner":"hub","condition":`+local+`}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("schedule local condition = %d %s, want 400", rec.Code, rec.Body.String())
	}

	rec = c.do(t, http.MethodPost, "/api/schedules",
		`{"task_id":"`+task.ID+`","name":"cond-schedule","type":"manual",`+
			`"target":{"type":"node","node_ids":["cond-node-1"]},`+
			`"execution_owner":"hub","offline_policy":"allow_offline","condition":`+remote+`}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create schedule = %d %s", rec.Code, rec.Body.String())
	}
	schedule := decodeResource[models.Schedule](t, rec)

	// 调度页立即执行：payload.Condition 应为 local AND remote。
	rec = c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run", `{"schedule_id":"`+schedule.ID+`"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("run from schedule = %d %s", rec.Code, rec.Body.String())
	}
	payload := conn.lastRunPayload(t)
	var merged models.Condition
	if err := json.Unmarshal(payload.Condition, &merged); err != nil {
		t.Fatalf("decode merged condition: %v", err)
	}
	if merged.Type != "and" || len(merged.And) != 2 || merged.And[0].Type != "local" || merged.And[1].Type != "remote" {
		t.Fatalf("merged condition = %+v", merged)
	}
	if payload.ScheduleID != schedule.ID || payload.ScheduleRevision != schedule.Revision {
		t.Fatalf("schedule source = %q rev %d", payload.ScheduleID, payload.ScheduleRevision)
	}

	// 任务页试运行：只带本地条件。
	rec = c.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run",
		`{"target":{"type":"node","node_ids":["cond-node-1"]}}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("run from task = %d %s", rec.Code, rec.Body.String())
	}
	payload = conn.lastRunPayload(t)
	var localOnly models.Condition
	if err := json.Unmarshal(payload.Condition, &localOnly); err != nil {
		t.Fatalf("decode local condition: %v", err)
	}
	if localOnly.Type != "local" {
		t.Fatalf("task-run condition = %+v", localOnly)
	}
	if payload.ScheduleID != "" || payload.ScheduleRevision != 0 {
		t.Fatalf("task-run must not carry schedule source: %+v", payload.ScheduleID)
	}
}
