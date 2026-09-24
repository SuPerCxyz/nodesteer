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
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// upgradeAPITestConn 实现 hub.AgentConn，缓存下发消息供断言。
type upgradeAPITestConn struct {
	nodeID string
	sent   chan protocol.Envelope
}

func (c *upgradeAPITestConn) NodeID() string { return c.nodeID }
func (c *upgradeAPITestConn) Send(msg protocol.Envelope) error {
	select {
	case c.sent <- msg:
	default:
	}
	return nil
}
func (c *upgradeAPITestConn) Close() error { return nil }

// newUpgradeAPITestServer 构造带 nodes/execs/sessions 的 API Server 与真实路由。
func newUpgradeAPITestServer(t *testing.T) (*Server, store.Store, *hub.SessionManager) {
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
	execs := hub.NewExecutionManager(st, sessions, nm, logger)
	s := New(st, auth.New(st, time.Hour), nm, nil, nil, nil, nil, nil, execs, logger)
	s.SetBaseURL("http://hub:8443")

	hash, err := auth.HashPassword("secret")
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	for _, u := range []struct {
		name string
		role string
	}{
		{"up-admin", models.RoleAdministrator},
		{"up-viewer", models.RoleViewer},
	} {
		if err := st.CreateUser(t.Context(), &models.User{
			Username: u.name, PasswordHash: hash, Role: u.role,
		}); err != nil {
			t.Fatalf("create user %s: %v", u.name, err)
		}
	}
	return s, st, sessions
}

// upgradeDo 经真实 Routes 发起请求并返回状态码与响应体。
func upgradeDo(t *testing.T, ts *httptest.Server, method, path, token, body string) (int, string) {
	t.Helper()
	var req *http.Request
	var err error
	if body == "" {
		req, err = http.NewRequest(method, ts.URL+path, nil)
	} else {
		req, err = http.NewRequest(method, ts.URL+path, strings.NewReader(body))
	}
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do %s %s: %v", method, path, err)
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body: %v", err)
	}
	return resp.StatusCode, string(b)
}

// TestNodesUpgradeBatchAPI 批量升级 API：部分 docker / 部分 native → 逐节点独立
// 终态与在途状态；docker 不下发；重复触发在途节点被去重。
func TestNodesUpgradeBatchAPI(t *testing.T) {
	s, st, sessions := newUpgradeAPITestServer(t)
	ts := httptest.NewServer(s.Routes())
	defer ts.Close()
	ctx := t.Context()

	nodes := []struct {
		id   string
		mode string
	}{
		{"node-native", models.DeploymentModeNative},
		{"node-docker", models.DeploymentModeDocker},
	}
	conns := map[string]*upgradeAPITestConn{}
	for _, n := range nodes {
		if err := st.UpsertNode(ctx, &models.Node{
			ID: n.id, AgentID: "agent-" + n.id, Hostname: n.id,
			DeploymentMode: n.mode, Status: models.NodeStatusOnline, Arch: "amd64",
			Capabilities: map[string]bool{models.CapScript: true},
		}); err != nil {
			t.Fatalf("upsert node %s: %v", n.id, err)
		}
		c := &upgradeAPITestConn{nodeID: n.id, sent: make(chan protocol.Envelope, 4)}
		sessions.Register(c)
		conns[n.id] = c
	}

	token := loginAs(t, s, "up-admin", "secret")
	status, body := upgradeDo(t, ts, http.MethodPost, "/api/nodes/upgrade", token,
		`{"node_ids":["node-native","node-docker"]}`)
	if status != http.StatusOK {
		t.Fatalf("batch upgrade status = %d, body %s", status, body)
	}
	var results []hub.UpgradeNodeResult
	if err := json.Unmarshal([]byte(body), &results); err != nil {
		t.Fatalf("decode results: %v (body %s)", err, body)
	}
	if len(results) != 2 {
		t.Fatalf("results = %d, want 2（逐节点独立）", len(results))
	}
	var native, docker *hub.UpgradeNodeResult
	for i := range results {
		switch results[i].NodeID {
		case "node-native":
			native = &results[i]
		case "node-docker":
			docker = &results[i]
		}
	}
	if native == nil || docker == nil {
		t.Fatalf("missing node results: %+v", results)
	}
	if !native.Dispatched || native.Status != models.ExecStatusPending || native.ExecutionID == "" {
		t.Fatalf("native result wrong: %+v", native)
	}
	if docker.Dispatched || docker.Status != models.ExecStatusSkipped ||
		!strings.Contains(docker.BlockReason, "deployment mode docker") {
		t.Fatalf("docker result wrong: %+v", docker)
	}
	// 下发面：仅 native 节点收到 RUN_EXECUTION，docker 节点零下发
	if msg := <-conns["node-native"].sent; msg.Type != protocol.MsgRunExecution {
		t.Fatalf("native conn got %s, want RUN_EXECUTION", msg.Type)
	}
	select {
	case msg := <-conns["node-docker"].sent:
		t.Fatalf("docker conn must not receive dispatch, got %s", msg.Type)
	default:
	}

	// 重复触发：native 在途去重返回同一记录；docker 每次触发各自留痕
	status, body = upgradeDo(t, ts, http.MethodPost, "/api/nodes/upgrade", token,
		`{"node_ids":["node-native","node-native"]}`)
	if status != http.StatusOK {
		t.Fatalf("second upgrade status = %d, body %s", status, body)
	}
	var again []hub.UpgradeNodeResult
	if err := json.Unmarshal([]byte(body), &again); err != nil {
		t.Fatalf("decode second results: %v", err)
	}
	if len(again) != 1 {
		t.Fatalf("批内重复 ID 应去重，results = %d, want 1", len(again))
	}
	if !again[0].Deduplicated || again[0].ExecutionID != native.ExecutionID {
		t.Fatalf("dedup result wrong: %+v", again[0])
	}
	select {
	case msg := <-conns["node-native"].sent:
		t.Fatalf("duplicate trigger must not dispatch, got %s", msg.Type)
	default:
	}

	// 模拟 Agent 回报成功终态（EXECUTION_FINISHED → MarkFinished）：执行历史可见终态
	if err := s.execs.MarkFinished(ctx, "node-native", protocol.ExecutionFinishedPayload{
		ExecutionID: native.ExecutionID, TaskID: models.AgentUpgradeTaskID, NodeID: "node-native",
		Status: models.ExecStatusSuccess, EndTime: time.Now().Format(time.RFC3339Nano),
	}); err != nil {
		t.Fatalf("mark finished: %v", err)
	}
	ex, err := s.execs.GetExecution(ctx, native.ExecutionID)
	if err != nil || ex.Status != models.ExecStatusSuccess {
		t.Fatalf("execution history must show terminal state: %+v err=%v", ex, err)
	}

	// 终态后再次触发 → 在途去重解除，创建新记录
	status, body = upgradeDo(t, ts, http.MethodPost, "/api/nodes/upgrade", token, `{"node_ids":["node-native"]}`)
	if status != http.StatusOK {
		t.Fatalf("post-terminal upgrade status = %d, body %s", status, body)
	}
	var after []hub.UpgradeNodeResult
	if err := json.Unmarshal([]byte(body), &after); err != nil || len(after) != 1 {
		t.Fatalf("post-terminal results wrong: %v body=%s", err, body)
	}
	if after[0].Deduplicated || after[0].ExecutionID == native.ExecutionID {
		t.Fatalf("post-terminal trigger must create a new record: %+v", after[0])
	}
}

// TestNodesUpgradeValidation 输入与鉴权校验：空数组 / 不存在节点 / 方法 / 角色。
func TestNodesUpgradeValidation(t *testing.T) {
	s, st, _ := newUpgradeAPITestServer(t)
	ts := httptest.NewServer(s.Routes())
	defer ts.Close()
	ctx := t.Context()

	if err := st.UpsertNode(ctx, &models.Node{
		ID: "node-valid", AgentID: "agent-node-valid", Hostname: "node-valid",
		DeploymentMode: models.DeploymentModeNative, Status: models.NodeStatusOnline, Arch: "amd64",
		Capabilities: map[string]bool{models.CapScript: true},
	}); err != nil {
		t.Fatalf("upsert node: %v", err)
	}

	admin := loginAs(t, s, "up-admin", "secret")
	cases := []struct {
		name   string
		method string
		body   string
		token  string
		want   int
	}{
		{name: "empty node_ids", method: http.MethodPost, body: `{"node_ids":[]}`, token: admin, want: http.StatusBadRequest},
		{name: "missing body", method: http.MethodPost, body: "", token: admin, want: http.StatusBadRequest},
		{name: "unknown node id", method: http.MethodPost, body: `{"node_ids":["node-missing"]}`, token: admin, want: http.StatusBadRequest},
		{name: "empty id in array", method: http.MethodPost, body: `{"node_ids":[""]}`, token: admin, want: http.StatusBadRequest},
		{name: "method not allowed", method: http.MethodGet, body: "", token: admin, want: http.StatusMethodNotAllowed},
		{name: "viewer forbidden", method: http.MethodPost, body: `{"node_ids":["node-valid"]}`, token: loginAs(t, s, "up-viewer", "secret"), want: http.StatusForbidden},
		{name: "unauthorized", method: http.MethodPost, body: `{"node_ids":["node-valid"]}`, token: "", want: http.StatusUnauthorized},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			status, body := upgradeDo(t, ts, tc.method, "/api/nodes/upgrade", tc.token, tc.body)
			if status != tc.want {
				t.Fatalf("status = %d, want %d (body %s)", status, tc.want, body)
			}
		})
	}

	// 单节点与批量同一执行路径：单个 native online 节点成功
	status, body := upgradeDo(t, ts, http.MethodPost, "/api/nodes/upgrade", admin, `{"node_ids":["node-valid"]}`)
	if status != http.StatusOK {
		t.Fatalf("single-node upgrade status = %d, body %s", status, body)
	}
	var results []hub.UpgradeNodeResult
	if err := json.Unmarshal([]byte(body), &results); err != nil || len(results) != 1 {
		t.Fatalf("single-node results wrong: %v body=%s", err, body)
	}
	if results[0].ExecutionID == "" {
		t.Fatalf("single-node result missing execution id: %+v", results[0])
	}
}
