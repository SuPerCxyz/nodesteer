package api

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/google/uuid"
)

// TestMissingNodeStatusUpdateIs404 覆盖 QA-20261018-F3：
// POST /api/nodes 携带不存在的 id 必须 404 归一化文案（此前 500 internal error）。
func TestMissingNodeStatusUpdateIs404(t *testing.T) {
	s := newStressServer(t)

	rec := s.do(t, http.MethodPost, "/api/nodes", `{"id":"`+uuid.NewString()+`","status":"offline"}`)
	if rec.Code != http.StatusNotFound || !strings.Contains(rec.Body.String(), "node not found") {
		t.Fatalf("missing node status = %d %s, want 404 node not found", rec.Code, rec.Body.String())
	}
	assertNoStorageLeak(t, rec.Body.String())

	// 仅标签路径同样不得静默 200。
	rec = s.do(t, http.MethodPost, "/api/nodes", `{"id":"`+uuid.NewString()+`","labels":{"env":"prod"}}`)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("missing node labels = %d %s, want 404", rec.Code, rec.Body.String())
	}

	// 存在节点仍可更新（正常路径不受影响）。
	node := &models.Node{ID: uuid.NewString(), AgentID: "agent-" + uuid.NewString(), Hostname: "status-node",
		Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative}
	if err := s.store.UpsertNode(t.Context(), node); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	rec = s.do(t, http.MethodPost, "/api/nodes", `{"id":"`+node.ID+`","status":"offline"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("existing node status = %d %s, want 200", rec.Code, rec.Body.String())
	}
}

// TestSubresourceMissingIDIs404 覆盖 QA-20261018-F4：
// revoke-credential / transfer retry|cancel / execution cancel 对不存在 id
// 必须 404 归一化文案，不得回 400 + "sql: no rows in result set" 原文。
func TestSubresourceMissingIDIs404(t *testing.T) {
	s := newStressServer(t)
	missing := uuid.NewString()

	cases := []struct {
		method string
		path   string
		want   string
	}{
		{http.MethodPost, "/api/nodes/" + missing + "/revoke-credential", "node not found"},
		{http.MethodPost, "/api/transfers/" + missing + "/retry", "transfer not found"},
		{http.MethodPost, "/api/transfers/" + missing + "/cancel", "transfer not found"},
		{http.MethodPost, "/api/executions/" + missing + "/cancel", "execution not found"},
	}
	for _, tc := range cases {
		rec := s.do(t, tc.method, tc.path, "")
		if rec.Code != http.StatusNotFound || !strings.Contains(rec.Body.String(), tc.want) {
			t.Fatalf("%s %s = %d %s, want 404 %q", tc.method, tc.path, rec.Code, rec.Body.String(), tc.want)
		}
		assertNoStorageLeak(t, rec.Body.String())
	}

	// 正常路径不受影响：存在节点的 revoke-credential 仍为 200。
	node := &models.Node{ID: uuid.NewString(), AgentID: "agent-" + uuid.NewString(), Hostname: "revoke-node",
		Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative}
	if err := s.store.UpsertNode(t.Context(), node); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	if rec := s.do(t, http.MethodPost, "/api/nodes/"+node.ID+"/revoke-credential", ""); rec.Code != http.StatusOK {
		t.Fatalf("revoke existing node = %d %s, want 200", rec.Code, rec.Body.String())
	}

	// 存在传输的 cancel 仍为 200（业务错误保持 400 语义）。
	transfer := &models.FileTransfer{ID: uuid.NewString(), SourceNodeID: node.ID, SourcePath: "/var/tmp/src",
		Targets: []*models.FileTransferTarget{{NodeID: "other-node", DestinationPath: "/var/tmp/dst"}}}
	if err := s.store.CreateFileTransfer(t.Context(), transfer); err != nil {
		t.Fatalf("create transfer: %v", err)
	}
	if rec := s.do(t, http.MethodPost, "/api/transfers/"+transfer.ID+"/cancel", ""); rec.Code != http.StatusOK {
		t.Fatalf("cancel existing transfer = %d %s, want 200", rec.Code, rec.Body.String())
	}
}

// TestUserUpdateMissingIs404 覆盖 QA-20261018-F5：
// PUT /api/users/{id} 对不存在用户必须 404（此前 UPDATE 零行命中仍静默 200）。
func TestUserUpdateMissingIs404(t *testing.T) {
	s := newStressServer(t)
	missing := uuid.NewString()

	for _, body := range []string{`{"role":"viewer"}`, `{"password":"changed-password"}`} {
		rec := s.do(t, http.MethodPut, "/api/users/"+missing, body)
		if rec.Code != http.StatusNotFound || !strings.Contains(rec.Body.String(), "user not found") {
			t.Fatalf("PUT missing user %s = %d %s, want 404 user not found", body, rec.Code, rec.Body.String())
		}
		assertNoStorageLeak(t, rec.Body.String())
	}

	// 存在用户仍可更新角色与密码。
	rec := s.do(t, http.MethodPost, "/api/users", `{"username":"contract-user","password":"secret-pass","role":"viewer"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create user = %d %s", rec.Code, rec.Body.String())
	}
	created := decodeResource[models.User](t, rec)
	rec = s.do(t, http.MethodPut, "/api/users/"+created.ID, `{"role":"operator"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("update existing user = %d %s, want 200", rec.Code, rec.Body.String())
	}
	persisted, err := s.store.GetUserByUsername(t.Context(), "contract-user")
	if err != nil || persisted.Role != models.RoleOperator {
		t.Fatalf("role not persisted: user=%+v err=%v", persisted, err)
	}
}

// TestMethodNotAllowedCarriesAllowHeader 覆盖 QA-20261018-F6：
// 405 必须携带 Allow 头（与既有正确端点一致）。
func TestMethodNotAllowedCarriesAllowHeader(t *testing.T) {
	s := newStressServer(t)

	rec := s.do(t, http.MethodPost, "/api/scripts", `{"name":"allow-script","interpreter":"bash","content":"true"}`)
	script := decodeResource[models.Script](t, rec)
	rec = s.do(t, http.MethodPost, "/api/tasks", `{"name":"allow-task","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`)
	task := decodeResource[models.Task](t, rec)
	rec = s.do(t, http.MethodPost, "/api/schedules", `{"task_id":"`+task.ID+`","type":"interval","interval_sec":60,"execution_owner":"hub"}`)
	schedule := decodeResource[models.Schedule](t, rec)
	rec = s.do(t, http.MethodPost, "/api/groups", `{"name":"allow-group","type":"static"}`)
	group := decodeResource[models.Group](t, rec)
	rec = s.do(t, http.MethodPost, "/api/applications", `{"name":"allow-app","unit_name":"allow-app.service"}`)
	app := decodeResource[models.Application](t, rec)

	cases := []struct {
		method string
		path   string
		allow  string
	}{
		{http.MethodPatch, "/api/tasks", "GET, POST"},
		{http.MethodPatch, "/api/tasks/" + task.ID, "GET, PUT, DELETE"},
		{http.MethodPatch, "/api/settings", "GET, PUT"},
		{http.MethodPatch, "/api/groups/" + group.ID, "GET, PUT, DELETE"},
		{http.MethodPatch, "/api/scripts/" + script.ID, "GET, PUT, DELETE"},
		{http.MethodPatch, "/api/schedules/" + schedule.ID, "GET, PUT, DELETE"},
		{http.MethodPatch, "/api/artifacts/" + uuid.NewString(), "GET, DELETE"},
		{http.MethodPatch, "/api/nodes", "GET, POST"},
		{http.MethodPatch, "/api/nodes/upgrade", "POST"},
		{http.MethodPatch, "/api/transfers", "GET, POST"},
		{http.MethodGet, "/api/executions/" + uuid.NewString() + "/cancel", "POST"},
		{http.MethodPatch, "/api/applications/" + app.ID + "/state", "GET"},
		{http.MethodPatch, "/api/oidc/state", "GET"},
		{http.MethodPatch, "/api/login", "POST"},
	}
	for _, tc := range cases {
		rec := s.do(t, tc.method, tc.path, `{}`)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s %s = %d %s, want 405", tc.method, tc.path, rec.Code, rec.Body.String())
		}
		if allow := rec.Header().Get("Allow"); allow != tc.allow {
			t.Fatalf("%s %s Allow = %q, want %q", tc.method, tc.path, allow, tc.allow)
		}
	}
}

// TestLoginRejectsNonPostMethods 覆盖 QA-20261018-F7 第 1 项：
// /api/login 仅接受 POST，GET/PATCH 等一律 405 + Allow: POST，且不得建立会话。
func TestLoginRejectsNonPostMethods(t *testing.T) {
	s := newStressServer(t)
	for _, method := range []string{http.MethodGet, http.MethodPatch, http.MethodPut} {
		req := newJSONRequest(method, "/api/login", `{"username":"stress-admin","password":"secret"}`)
		rec := newRecorder(s.handler, req)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s /api/login = %d %s, want 405", method, rec.Code, rec.Body.String())
		}
		if allow := rec.Header().Get("Allow"); allow != "POST" {
			t.Fatalf("%s /api/login Allow = %q, want POST", method, allow)
		}
		if strings.Contains(rec.Body.String(), "token") {
			t.Fatalf("%s /api/login must not issue a session: %s", method, rec.Body.String())
		}
	}
	// POST 正常路径不变。
	req := newJSONRequest(http.MethodPost, "/api/login", `{"username":"stress-admin","password":"secret"}`)
	if rec := newRecorder(s.handler, req); rec.Code != http.StatusOK {
		t.Fatalf("POST /api/login = %d %s, want 200", rec.Code, rec.Body.String())
	}
}

// TestTaskRunRejectsNonPostMethods 覆盖 QA-20261018-F7 第 2 项：
// GET /api/tasks/{id}/run 不得执行 RunNow（405 + Allow: POST，且零执行记录）。
func TestTaskRunRejectsNonPostMethods(t *testing.T) {
	s := newStressServer(t)
	node := &models.Node{
		ID: "run-node", AgentID: "run-agent", Hostname: "run-node",
		Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative,
		Capabilities: map[string]bool{models.CapScript: true},
	}
	if err := s.store.UpsertNode(t.Context(), node); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	rec := s.do(t, http.MethodPost, "/api/tasks",
		`{"name":"run-task","type":"command","command":"true","enabled":true,"target":{"type":"node","node_ids":["run-node"]}}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create task = %d %s", rec.Code, rec.Body.String())
	}
	task := decodeResource[models.Task](t, rec)

	before, err := s.store.CountExecutions(t.Context(), store.ExecutionFilter{})
	if err != nil {
		t.Fatalf("count executions: %v", err)
	}
	for _, method := range []string{http.MethodGet, http.MethodPut} {
		rec := s.do(t, method, "/api/tasks/"+task.ID+"/run", "")
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s task run = %d %s, want 405", method, rec.Code, rec.Body.String())
		}
		if allow := rec.Header().Get("Allow"); allow != "POST" {
			t.Fatalf("%s task run Allow = %q, want POST", method, allow)
		}
	}
	after, err := s.store.CountExecutions(t.Context(), store.ExecutionFilter{})
	if err != nil {
		t.Fatalf("count executions: %v", err)
	}
	if after != before {
		t.Fatalf("GET/PUT task run executed RunNow: executions %d -> %d", before, after)
	}

	// POST 仍进入 RunNow（执行记录出现；节点无会话时按业务失败处理）。
	rec = s.do(t, http.MethodPost, "/api/tasks/"+task.ID+"/run", "")
	if rec.Code == http.StatusMethodNotAllowed {
		t.Fatalf("POST task run = 405, want RunNow path")
	}
	after, err = s.store.CountExecutions(t.Context(), store.ExecutionFilter{})
	if err != nil {
		t.Fatalf("count executions: %v", err)
	}
	if after <= before {
		t.Fatalf("POST task run created no execution (before=%d after=%d, status=%d body=%s)", before, after, rec.Code, rec.Body.String())
	}
}

// TestApplicationSubresourcesRejectUnsupportedMethods 覆盖 QA-20261018-F7 第 3/4 项：
// revisions/nodes 仅支持 GET（DELETE 不再回 200）；assign/deploy 仅支持 POST。
func TestApplicationSubresourcesRejectUnsupportedMethods(t *testing.T) {
	s := newStressServer(t)
	rec := s.do(t, http.MethodPost, "/api/applications", `{"name":"sub-app","unit_name":"sub-app.service"}`)
	app := decodeResource[models.Application](t, rec)

	cases := []struct {
		method string
		path   string
		allow  string
	}{
		{http.MethodDelete, "/api/applications/" + app.ID + "/revisions", "GET"},
		{http.MethodDelete, "/api/applications/" + app.ID + "/nodes", "GET"},
		{http.MethodGet, "/api/applications/" + app.ID + "/assign", "POST"},
		{http.MethodGet, "/api/applications/" + app.ID + "/deploy", "POST"},
		{http.MethodDelete, "/api/applications/" + app.ID + "/assign", "POST"},
	}
	for _, tc := range cases {
		rec := s.do(t, tc.method, tc.path, "")
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s %s = %d %s, want 405", tc.method, tc.path, rec.Code, rec.Body.String())
		}
		if allow := rec.Header().Get("Allow"); allow != tc.allow {
			t.Fatalf("%s %s Allow = %q, want %q", tc.method, tc.path, allow, tc.allow)
		}
	}

	// 正常读取路径不受影响。
	for _, path := range []string{"/api/applications/" + app.ID + "/revisions", "/api/applications/" + app.ID + "/nodes"} {
		if rec := s.do(t, http.MethodGet, path, ""); rec.Code != http.StatusOK {
			t.Fatalf("GET %s = %d %s, want 200", path, rec.Code, rec.Body.String())
		}
	}
}

func assertNoStorageLeak(t *testing.T, body string) {
	t.Helper()
	lower := strings.ToLower(body)
	for _, leak := range []string{"sql:", "no rows", "sqlite", "database is locked"} {
		if strings.Contains(lower, leak) {
			t.Fatalf("storage error leaked to client: %s", body)
		}
	}
}

func newJSONRequest(method, path, body string) *http.Request {
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, path, reader)
	req.Header.Set("Content-Type", "application/json")
	return req
}

func newRecorder(handler http.Handler, req *http.Request) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}
