package api

// 未知子路径契约（观察项 QA-20261018-OBS-UNKNOWN-SUBPATH-LENIENT）。
//
// 修复前运行实测（探针矩阵，2026-10-18，全量 Manager + 文件库）：
//   nodes        GET 200（返回节点本体）/ POST 200（改写 status）/ PUT 200 / DELETE 执行删除
//   tasks        GET 200 / PUT 200（改名落库）/ DELETE 执行删除
//   scripts      GET 200 / PUT 200（改名落库）/ DELETE 200（脚本被删除）
//   applications GET 200 / PUT 200（改名落库）/ DELETE 200（应用被删除）
//   artifacts    GET 200 / DELETE 200（artifact 被删除）
//   executions   GET 200（返回执行记录本体）
//   schedules/groups/users/transfers 已返回 404/405，未误执行
//
// 修复后契约：/api/<resource>/{id}/<未知子路径> 一律 404（任意方法），
// 不得落入基础资源方法分支被读取/改写/删除；已知子路径与错误方法仍按 405 + Allow。

import (
	"net/http"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/google/uuid"
)

// TestUnknownSubpathIs404ForAllResources 逐资源族 × 方法矩阵：
// 未知子路径必须 404 归一化文案，且不产生任何副作用。
func TestUnknownSubpathIs404ForAllResources(t *testing.T) {
	s := newStressServer(t)

	nodeID := "subpath-node"
	if err := s.store.UpsertNode(t.Context(), &models.Node{
		ID: nodeID, AgentID: "subpath-agent", Hostname: "subpath-node",
		Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative,
	}); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	rec := s.do(t, http.MethodPost, "/api/scripts", `{"name":"subpath-script","interpreter":"bash","content":"true"}`)
	script := decodeResource[models.Script](t, rec)
	rec = s.do(t, http.MethodPost, "/api/tasks", `{"name":"subpath-task","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`)
	task := decodeResource[models.Task](t, rec)
	rec = s.do(t, http.MethodPost, "/api/schedules", `{"task_id":"`+task.ID+`","type":"interval","interval_sec":60,"execution_owner":"hub"}`)
	schedule := decodeResource[models.Schedule](t, rec)
	rec = s.do(t, http.MethodPost, "/api/groups", `{"name":"subpath-group","type":"static"}`)
	group := decodeResource[models.Group](t, rec)
	rec = s.do(t, http.MethodPost, "/api/applications", `{"name":"subpath-app","unit_name":"subpath-app.service"}`)
	app := decodeResource[models.Application](t, rec)
	rec = s.do(t, http.MethodPost, "/api/users", `{"username":"subpath-user","password":"secret","role":"viewer"}`)
	user := decodeResource[models.User](t, rec)
	artifact := &models.Artifact{ID: uuid.NewString(), Name: "subpath-art", Version: "1", Architecture: "amd64",
		Filename: "subpath.bin", Size: 1, SHA256: "x", StoragePath: "/nonexistent"}
	if err := s.store.CreateArtifact(t.Context(), artifact); err != nil {
		t.Fatalf("create artifact: %v", err)
	}
	execution := &models.Execution{ID: uuid.NewString(), TaskID: task.ID, NodeID: nodeID,
		TriggerType: "manual", Status: models.ExecStatusSuccess}
	if err := s.store.CreateExecution(t.Context(), execution); err != nil {
		t.Fatalf("create execution: %v", err)
	}
	transfer := &models.FileTransfer{ID: uuid.NewString(), SourceNodeID: nodeID, SourcePath: "/var/tmp/src",
		Targets: []*models.FileTransferTarget{{NodeID: "subpath-other", DestinationPath: "/var/tmp/dst"}}}
	if err := s.store.CreateFileTransfer(t.Context(), transfer); err != nil {
		t.Fatalf("create transfer: %v", err)
	}

	// 变更体：若未知子路径被误当作基础资源 PUT/POST，会留下可观测的落库变更。
	families := []struct {
		name    string
		path    string
		want    string
		putBody string
	}{
		{"nodes", "/api/nodes/" + nodeID, "node not found", `{"status":"offline"}`},
		{"tasks", "/api/tasks/" + task.ID, "task not found", `{"name":"subpath-task-hijacked","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`},
		{"scripts", "/api/scripts/" + script.ID, "script not found", `{"name":"subpath-script-hijacked","interpreter":"bash","content":"true"}`},
		{"schedules", "/api/schedules/" + schedule.ID, "schedule not found", `{"task_id":"` + task.ID + `","type":"interval","interval_sec":120,"execution_owner":"hub"}`},
		{"groups", "/api/groups/" + group.ID, "group not found", `{"name":"subpath-group-hijacked","type":"static"}`},
		{"applications", "/api/applications/" + app.ID, "application not found", `{"name":"subpath-app-hijacked","unit_name":"subpath-app.service"}`},
		{"users", "/api/users/" + user.ID, "user not found", `{"role":"operator"}`},
		{"artifacts", "/api/artifacts/" + artifact.ID, "artifact not found", `{}`},
		{"executions", "/api/executions/" + execution.ID, "execution not found", `{}`},
		{"transfers", "/api/transfers/" + transfer.ID, "transfer action not found", `{}`},
	}
	for _, f := range families {
		for _, method := range []string{http.MethodGet, http.MethodPost, http.MethodPut, http.MethodDelete} {
			rec := s.do(t, method, f.path+"/bogus", f.putBody)
			if rec.Code != http.StatusNotFound {
				t.Fatalf("%s %s/bogus = %d %s, want 404", method, f.path, rec.Code, rec.Body.String())
			}
			if !strings.Contains(rec.Body.String(), f.want) {
				t.Fatalf("%s %s/bogus body = %s, want %q", method, f.path, rec.Body.String(), f.want)
			}
			assertNoStorageLeak(t, rec.Body.String())
		}
	}

	// 副作用核对：基础资源全部保留且未被改写。
	if n, err := s.store.GetNode(t.Context(), nodeID); err != nil || n.Status != models.NodeStatusOnline {
		t.Fatalf("node mutated by unknown subpath: %+v err=%v", n, err)
	}
	if tk, err := s.store.GetTask(t.Context(), task.ID); err != nil || tk.Name != "subpath-task" {
		t.Fatalf("task mutated by unknown subpath: %+v err=%v", tk, err)
	}
	if sc, err := s.store.GetScript(t.Context(), script.ID); err != nil || sc.Name != "subpath-script" {
		t.Fatalf("script mutated/deleted by unknown subpath: %+v err=%v", sc, err)
	}
	if sch, err := s.store.GetSchedule(t.Context(), schedule.ID); err != nil || sch.IntervalSec != 60 {
		t.Fatalf("schedule mutated/deleted by unknown subpath: %+v err=%v", sch, err)
	}
	if g, err := s.store.GetGroup(t.Context(), group.ID); err != nil || g.Name != "subpath-group" {
		t.Fatalf("group mutated/deleted by unknown subpath: %+v err=%v", g, err)
	}
	if a, err := s.store.GetApplication(t.Context(), app.ID); err != nil || a.Name != "subpath-app" {
		t.Fatalf("application mutated/deleted by unknown subpath: %+v err=%v", a, err)
	}
	if u, err := s.store.GetUserByID(t.Context(), user.ID); err != nil || u.Role != models.RoleViewer {
		t.Fatalf("user mutated by unknown subpath: %+v err=%v", u, err)
	}
	if _, err := s.store.GetArtifact(t.Context(), artifact.ID); err != nil {
		t.Fatalf("artifact deleted by unknown subpath: %v", err)
	}
	if _, err := s.store.GetExecution(t.Context(), execution.ID); err != nil {
		t.Fatalf("execution deleted by unknown subpath: %v", err)
	}
	if _, err := s.store.GetFileTransfer(t.Context(), transfer.ID); err != nil {
		t.Fatalf("transfer deleted by unknown subpath: %v", err)
	}
}

// TestKnownSubpathExtraSegmentsAre404 已知子路径后追加额外段同样 404：
// 此前 /run/extra 仍执行 RunNow、/revoke-credential/extra 仍撤销凭证、
// /state/extra 与 /logs/extra 仍返回本体数据。
func TestKnownSubpathExtraSegmentsAre404(t *testing.T) {
	s := newStressServer(t)

	nodeID := "extra-node"
	if err := s.store.UpsertNode(t.Context(), &models.Node{
		ID: nodeID, AgentID: "extra-agent", Hostname: "extra-node",
		Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative,
	}); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	rec := s.do(t, http.MethodPost, "/api/scripts", `{"name":"extra-script","interpreter":"bash","content":"true"}`)
	script := decodeResource[models.Script](t, rec)
	rec = s.do(t, http.MethodPost, "/api/tasks", `{"name":"extra-task","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`)
	task := decodeResource[models.Task](t, rec)
	rec = s.do(t, http.MethodPost, "/api/applications", `{"name":"extra-app","unit_name":"extra-app.service"}`)
	app := decodeResource[models.Application](t, rec)
	execution := &models.Execution{ID: uuid.NewString(), TaskID: task.ID, NodeID: nodeID,
		TriggerType: "manual", Status: models.ExecStatusSuccess}
	if err := s.store.CreateExecution(t.Context(), execution); err != nil {
		t.Fatalf("create execution: %v", err)
	}

	cases := []struct {
		method string
		path   string
		want   string
	}{
		{http.MethodPost, "/api/tasks/" + task.ID + "/run/extra", "task not found"},
		{http.MethodGet, "/api/tasks/" + task.ID + "/run/extra", "task not found"},
		{http.MethodPost, "/api/nodes/" + nodeID + "/revoke-credential/extra", "node not found"},
		{http.MethodGet, "/api/scripts/" + script.ID + "/revisions/1/extra", "script not found"},
		{http.MethodGet, "/api/applications/" + app.ID + "/state/extra", "application not found"},
		{http.MethodGet, "/api/executions/" + execution.ID + "/logs/extra", "execution not found"},
		{http.MethodPost, "/api/transfers/" + uuid.NewString() + "/retry/extra", "transfer action not found"},
	}
	for _, tc := range cases {
		rec := s.do(t, tc.method, tc.path, "")
		if rec.Code != http.StatusNotFound || !strings.Contains(rec.Body.String(), tc.want) {
			t.Fatalf("%s %s = %d %s, want 404 %q", tc.method, tc.path, rec.Code, rec.Body.String(), tc.want)
		}
	}

	// RunNow 未被 /run/extra 触发：零执行记录。
	count, err := s.store.CountExecutions(t.Context(), store.ExecutionFilter{})
	if err != nil {
		t.Fatalf("count executions: %v", err)
	}
	if count != 1 { // 仅造数时创建的那条
		t.Fatalf("executions after /run/extra = %d, want 1 (RunNow must not fire)", count)
	}
}

// TestValidSubpathsStillWork 修复不得误伤已知子路径的正常方法契约。
func TestValidSubpathsStillWork(t *testing.T) {
	s := newStressServer(t)

	rec := s.do(t, http.MethodPost, "/api/scripts", `{"name":"valid-script","interpreter":"bash","content":"true"}`)
	script := decodeResource[models.Script](t, rec)
	rec = s.do(t, http.MethodPost, "/api/tasks", `{"name":"valid-task","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`)
	task := decodeResource[models.Task](t, rec)
	rec = s.do(t, http.MethodPost, "/api/applications", `{"name":"valid-app","unit_name":"valid-app.service"}`)
	app := decodeResource[models.Application](t, rec)
	execution := &models.Execution{ID: uuid.NewString(), TaskID: task.ID, NodeID: "valid-node",
		TriggerType: "manual", Status: models.ExecStatusSuccess}
	if err := s.store.CreateExecution(t.Context(), execution); err != nil {
		t.Fatalf("create execution: %v", err)
	}

	// 已知子路径 + 支持的方法 → 200/预期状态。
	rec = s.do(t, http.MethodGet, "/api/scripts/"+script.ID+"/revisions", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("GET script revisions = %d %s, want 200", rec.Code, rec.Body.String())
	}
	rec = s.do(t, http.MethodGet, "/api/applications/"+app.ID+"/state", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("GET app state = %d %s, want 200", rec.Code, rec.Body.String())
	}
	rec = s.do(t, http.MethodGet, "/api/executions/"+execution.ID+"/logs", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("GET execution logs = %d %s, want 200", rec.Code, rec.Body.String())
	}
	rec = s.do(t, http.MethodGet, "/api/tasks/"+task.ID+"/run", "")
	if rec.Code != http.StatusMethodNotAllowed || rec.Header().Get("Allow") != "POST" {
		t.Fatalf("GET task run = %d Allow=%q, want 405 POST", rec.Code, rec.Header().Get("Allow"))
	}

	// 已知子路径 + 不支持的方法 → 405 + Allow（此前 PUT revisions 会静默改写脚本本体）。
	rec = s.do(t, http.MethodPut, "/api/scripts/"+script.ID+"/revisions", `{"name":"hijack","interpreter":"bash","content":"true"}`)
	if rec.Code != http.StatusMethodNotAllowed || rec.Header().Get("Allow") != "GET" {
		t.Fatalf("PUT script revisions = %d Allow=%q, want 405 GET", rec.Code, rec.Header().Get("Allow"))
	}
	if sc, err := s.store.GetScript(t.Context(), script.ID); err != nil || sc.Name != "valid-script" {
		t.Fatalf("script mutated via revisions subpath: %+v err=%v", sc, err)
	}
	rec = s.do(t, http.MethodDelete, "/api/applications/"+app.ID+"/nodes", "")
	if rec.Code != http.StatusMethodNotAllowed || rec.Header().Get("Allow") != "GET" {
		t.Fatalf("DELETE app nodes = %d Allow=%q, want 405 GET", rec.Code, rec.Header().Get("Allow"))
	}
}

// TestTrailingSlashIsBaseResource 末尾斜杠归一为同一资源（与既有 tasks/nodes 行为一致）：
// groups/schedules/users 此前把末尾斜杠并入 id 返回 404，现统一归一。
func TestTrailingSlashIsBaseResource(t *testing.T) {
	s := newStressServer(t)

	rec := s.do(t, http.MethodPost, "/api/groups", `{"name":"slash-group","type":"static"}`)
	group := decodeResource[models.Group](t, rec)
	rec = s.do(t, http.MethodPost, "/api/users", `{"username":"slash-user","password":"secret","role":"viewer"}`)
	user := decodeResource[models.User](t, rec)
	rec = s.do(t, http.MethodPost, "/api/tasks", `{"name":"slash-task","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`)
	task := decodeResource[models.Task](t, rec)

	if rec := s.do(t, http.MethodGet, "/api/groups/"+group.ID+"/", ""); rec.Code != http.StatusOK {
		t.Fatalf("GET group trailing slash = %d %s, want 200", rec.Code, rec.Body.String())
	}
	if rec := s.do(t, http.MethodGet, "/api/tasks/"+task.ID+"/", ""); rec.Code != http.StatusOK {
		t.Fatalf("GET task trailing slash = %d %s, want 200", rec.Code, rec.Body.String())
	}
	if rec := s.do(t, http.MethodPut, "/api/users/"+user.ID+"/", `{"role":"operator"}`); rec.Code != http.StatusOK {
		t.Fatalf("PUT user trailing slash = %d %s, want 200", rec.Code, rec.Body.String())
	}
	if u, err := s.store.GetUserByID(t.Context(), user.ID); err != nil || u.Role != models.RoleOperator {
		t.Fatalf("user role not persisted via trailing slash: %+v err=%v", u, err)
	}
}
