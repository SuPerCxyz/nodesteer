package api

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/metrics"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type contractServer struct {
	server  *Server
	handler http.Handler
	store   *store.SQLiteStore
	rm      *hub.RevisionManager
	token   string
}

// newContractServer 构造带全部 Manager 的真实路由 API Server，并以管理员登录。
func newContractServer(t *testing.T) *contractServer {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	rm := hub.NewRevisionManager(st)
	nm := hub.NewNodeManager(st, rm)
	sessions := hub.NewSessionManager()
	syncMgr := hub.NewSyncManager(st, rm, sessions, nm)
	scripts := hub.NewScriptManager(st, rm, syncMgr)
	tasks := hub.NewTaskManager(st, rm, syncMgr)
	artifacts, err := hub.NewArtifactManager(st, t.TempDir(), sessions, syncMgr)
	if err != nil {
		t.Fatalf("artifact manager: %v", err)
	}
	execs := hub.NewExecutionManager(st, sessions, nm, logger)
	schedules := hub.NewScheduleManager(st, rm, syncMgr, nm, execs)
	apps := hub.NewAppManager(st, rm, sessions, artifacts, execs, "")
	apps.SetSyncMgr(syncMgr)
	authMgr := auth.New(st, time.Hour)
	server := New(st, authMgr, nm, scripts, tasks, schedules, artifacts, apps, execs, logger)

	hash, err := auth.HashPassword("secret")
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	if err := st.CreateUser(t.Context(), &models.User{
		Username: "admin", PasswordHash: hash, Role: models.RoleAdministrator,
	}); err != nil {
		t.Fatalf("create admin: %v", err)
	}
	return &contractServer{
		server:  server,
		handler: server.Routes(),
		store:   st,
		rm:      rm,
		token:   loginAs(t, server, "admin", "secret"),
	}
}

func (c *contractServer) do(t *testing.T, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, path, reader)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	rec := httptest.NewRecorder()
	c.handler.ServeHTTP(rec, req)
	return rec
}

func decodeResource[T any](t *testing.T, rec *httptest.ResponseRecorder) T {
	t.Helper()
	var out T
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("decode response %s: %v", rec.Body.String(), err)
	}
	return out
}

// TestGroupsContractValidation 覆盖缺陷：POST 空名 400、PUT 不存在 id 404（不再 upsert）、
// PUT 重名归一化文案；且不产生孤儿成员行与无效 Desired Revision 推进。
func TestGroupsContractValidation(t *testing.T) {
	c := newContractServer(t)

	// POST 缺 name / 纯空白 → 400 name required，不落库
	for _, body := range []string{`{}`, `{"name":"   "}`} {
		rec := c.do(t, http.MethodPost, "/api/groups", body)
		if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "name required") {
			t.Fatalf("POST %s = %d %s, want 400 name required", body, rec.Code, rec.Body.String())
		}
	}
	if groups, err := c.store.ListGroups(t.Context()); err != nil || len(groups) != 0 {
		t.Fatalf("groups after empty-name posts = %+v err=%v", groups, err)
	}

	// 正常创建
	rec := c.do(t, http.MethodPost, "/api/groups", `{"name":"Group A","type":"static"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("POST group = %d %s", rec.Code, rec.Body.String())
	}
	created := decodeResource[models.Group](t, rec)

	// 重名（忽略大小写与首尾空白）→ 400 归一化文案，不外泄 SQLite UNIQUE 原文
	rec = c.do(t, http.MethodPost, "/api/groups", `{"name":" group a "}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "group name already exists") {
		t.Fatalf("duplicate POST = %d %s", rec.Code, rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), "UNIQUE") || strings.Contains(rec.Body.String(), "sql") {
		t.Fatalf("duplicate POST leaked storage error: %s", rec.Body.String())
	}

	// PUT 不存在 id → 404（此前 200 upsert），无成员孤儿行、revision 不前进
	revBefore, err := c.rm.Current(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	rec = c.do(t, http.MethodPut, "/api/groups/does-not-exist", `{"name":"ghost","type":"static","members":["node-x"]}`)
	if rec.Code != http.StatusNotFound || !strings.Contains(rec.Body.String(), "group not found") {
		t.Fatalf("PUT missing group = %d %s, want 404 group not found", rec.Code, rec.Body.String())
	}
	if groups, err := c.store.ListGroups(t.Context()); err != nil || len(groups) != 1 {
		t.Fatalf("group count changed after 404: %+v err=%v", groups, err)
	}
	if ids, err := c.store.GroupMemberIDs(t.Context(), "does-not-exist"); err != nil || len(ids) != 0 {
		t.Fatalf("orphan members written: %v err=%v", ids, err)
	}
	if revAfter, err := c.rm.Current(t.Context()); err != nil || revAfter != revBefore {
		t.Fatalf("revision advanced on rejected PUT: %d -> %d err=%v", revBefore, revAfter, err)
	}

	// 正常 PUT（含自我重命名）→ 200
	rec = c.do(t, http.MethodPut, "/api/groups/"+created.ID, `{"name":"Group A renamed","type":"static"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("PUT group = %d %s", rec.Code, rec.Body.String())
	}
	rec = c.do(t, http.MethodGet, "/api/groups/"+created.ID, "")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "Group A renamed") {
		t.Fatalf("GET updated group = %d %s", rec.Code, rec.Body.String())
	}

	// PUT 空名 → 400（先于 404 校验）
	rec = c.do(t, http.MethodPut, "/api/groups/"+created.ID, `{"name":""}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "name required") {
		t.Fatalf("PUT empty name = %d %s", rec.Code, rec.Body.String())
	}
}

// TestDefinitionPutNotFoundContract 覆盖缺陷：schedules/applications/scripts/tasks 四处
// PUT 对不存在 id 返回 404 + 归一化文案（不再 400 + sql: no rows in result set）。
func TestDefinitionPutNotFoundContract(t *testing.T) {
	c := newContractServer(t)

	// 准备四类真实资源
	rec := c.do(t, http.MethodPost, "/api/scripts", `{"name":"contract-script","interpreter":"bash","content":"true"}`)
	script := decodeResource[models.Script](t, rec)
	if rec.Code != http.StatusOK {
		t.Fatalf("create script = %d %s", rec.Code, rec.Body.String())
	}
	rec = c.do(t, http.MethodPost, "/api/tasks", `{"name":"contract-task","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`)
	task := decodeResource[models.Task](t, rec)
	if rec.Code != http.StatusOK {
		t.Fatalf("create task = %d %s", rec.Code, rec.Body.String())
	}
	rec = c.do(t, http.MethodPost, "/api/schedules", `{"task_id":"`+task.ID+`","type":"interval","interval_sec":60,"execution_owner":"hub"}`)
	schedule := decodeResource[models.Schedule](t, rec)
	if rec.Code != http.StatusOK {
		t.Fatalf("create schedule = %d %s", rec.Code, rec.Body.String())
	}
	rec = c.do(t, http.MethodPost, "/api/applications", `{"name":"contract-app","unit_name":"contract-app.service"}`)
	app := decodeResource[models.Application](t, rec)
	if rec.Code != http.StatusOK {
		t.Fatalf("create application = %d %s", rec.Code, rec.Body.String())
	}

	notFoundCases := []struct {
		name string
		path string
		body string
		want string
	}{
		{"scripts", "/api/scripts/missing-id", `{"name":"contract-script-missing","interpreter":"bash","content":"true"}`, "script not found"},
		{"tasks", "/api/tasks/missing-id", `{"name":"contract-task-missing","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`, "task not found"},
		{"schedules", "/api/schedules/missing-id", `{"task_id":"` + task.ID + `","type":"interval","interval_sec":60,"execution_owner":"hub"}`, "schedule not found"},
		{"applications", "/api/applications/missing-id", `{"name":"contract-app-missing","unit_name":"contract-app-missing.service"}`, "application not found"},
	}
	for _, tc := range notFoundCases {
		t.Run(tc.name, func(t *testing.T) {
			rec := c.do(t, http.MethodPut, tc.path, tc.body)
			body := rec.Body.String()
			if rec.Code != http.StatusNotFound || !strings.Contains(body, tc.want) {
				t.Fatalf("PUT %s = %d %s, want 404 %q", tc.path, rec.Code, body, tc.want)
			}
			if strings.Contains(body, "sql") || strings.Contains(body, "no rows") {
				t.Fatalf("PUT %s leaked storage error: %s", tc.path, body)
			}
		})
	}

	// 存在 id → 200
	okCases := []struct {
		name string
		path string
		body string
	}{
		{"scripts", "/api/scripts/" + script.ID, `{"name":"contract-script","interpreter":"bash","content":"echo ok"}`},
		{"tasks", "/api/tasks/" + task.ID, `{"name":"contract-task","type":"command","command":"echo ok","target":{"type":"label","label_key":"env"}}`},
		{"schedules", "/api/schedules/" + schedule.ID, `{"task_id":"` + task.ID + `","type":"interval","interval_sec":120,"execution_owner":"hub"}`},
		{"applications", "/api/applications/" + app.ID, `{"name":"contract-app","unit_name":"contract-app.service"}`},
	}
	for _, tc := range okCases {
		t.Run(tc.name+" update", func(t *testing.T) {
			if rec := c.do(t, http.MethodPut, tc.path, tc.body); rec.Code != http.StatusOK {
				t.Fatalf("PUT %s = %d %s, want 200", tc.path, rec.Code, rec.Body.String())
			}
		})
	}

	// 校验失败保留既有 400 文案（不得被 404 映射吞掉）
	if rec := c.do(t, http.MethodPut, "/api/scripts/"+script.ID, `{"name":"contract-script","interpreter":"bash","content":"true","timeout":-5}`); rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "timeout cannot be negative") {
		t.Fatalf("invalid script PUT = %d %s, want 400 timeout error", rec.Code, rec.Body.String())
	}
	if rec := c.do(t, http.MethodPut, "/api/schedules/"+schedule.ID, `{"task_id":"`+task.ID+`","type":"bogus","execution_owner":"hub"}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid schedule PUT = %d %s, want 400", rec.Code, rec.Body.String())
	}
}

// TestAuditAndReadEndpointsMethodSemantics 覆盖缺陷：审计端点非 GET → 405（Allow: GET），
// 同批收紧 /api/me、/metrics、/api/logout 的方法边界；OPTIONS 预检与既有正确方法不受影响。
func TestAuditAndReadEndpointsMethodSemantics(t *testing.T) {
	c := newContractServer(t)
	c.server.SetMetrics(metrics.New())

	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodDelete, http.MethodPatch} {
		rec := c.do(t, method, "/api/audit", `{}`)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s /api/audit = %d %s, want 405", method, rec.Code, rec.Body.String())
		}
		if allow := rec.Header().Get("Allow"); allow != "GET" {
			t.Fatalf("%s /api/audit Allow = %q, want GET", method, allow)
		}
	}
	// POST /api/audit 不得执行审计查询（保持列表接口只读，不因为方法误用产生 200 列表）
	rec := c.do(t, http.MethodPost, "/api/audit", "")
	if rec.Code != http.StatusMethodNotAllowed || strings.Contains(rec.Body.String(), "action") {
		t.Fatalf("POST /api/audit body = %d %s", rec.Code, rec.Body.String())
	}

	// OPTIONS 预检仍按 CORS 处理
	if rec := c.do(t, http.MethodOptions, "/api/audit", ""); rec.Code != http.StatusNoContent {
		t.Fatalf("OPTIONS /api/audit = %d, want 204", rec.Code)
	}
	// GET 保留 200 与计数头
	rec = c.do(t, http.MethodGet, "/api/audit", "")
	if rec.Code != http.StatusOK || rec.Header().Get("X-Total-Count") == "" {
		t.Fatalf("GET /api/audit = %d headers=%v", rec.Code, rec.Header())
	}

	// /api/me 与 /metrics 同为只读端点
	for _, path := range []string{"/api/me", "/metrics"} {
		if rec := c.do(t, http.MethodPut, path, `{}`); rec.Code != http.StatusMethodNotAllowed || rec.Header().Get("Allow") != "GET" {
			t.Fatalf("PUT %s = %d Allow=%q, want 405 GET", path, rec.Code, rec.Header().Get("Allow"))
		}
		if rec := c.do(t, http.MethodGet, path, ""); rec.Code != http.StatusOK {
			t.Fatalf("GET %s = %d %s, want 200", path, rec.Code, rec.Body.String())
		}
	}

	// /api/logout：POST 是既有正确语义，其他方法 405 且不注销
	if rec := c.do(t, http.MethodGet, "/api/logout", ""); rec.Code != http.StatusMethodNotAllowed || rec.Header().Get("Allow") != "POST" {
		t.Fatalf("GET /api/logout = %d Allow=%q, want 405 POST", rec.Code, rec.Header().Get("Allow"))
	}
	if rec := c.do(t, http.MethodGet, "/api/me", ""); rec.Code != http.StatusOK {
		t.Fatal("rejected GET /api/logout must not revoke the session")
	}
	if rec := c.do(t, http.MethodPost, "/api/logout", ""); rec.Code != http.StatusOK {
		t.Fatalf("POST /api/logout = %d %s, want 200", rec.Code, rec.Body.String())
	}
	if rec := c.do(t, http.MethodGet, "/api/me", ""); rec.Code != http.StatusUnauthorized {
		t.Fatalf("POST /api/logout did not revoke session: %d", rec.Code)
	}
}
