package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// stressServer 使用文件库（真实连接池 SetMaxOpenConns(8)）与全量 Manager 的 API Server，
// 用于并发/连接池缺陷（QA-20261018-F1/F2/F8）的回归验证。
type stressServer struct {
	server  *Server
	handler http.Handler
	store   *store.SQLiteStore
	nm      *hub.NodeManager
	token   string
	logs    *bytes.Buffer
}

func newStressServer(t *testing.T) *stressServer {
	t.Helper()
	st, err := store.OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	logs := &bytes.Buffer{}
	logger := slog.New(slog.NewTextHandler(logs, nil))
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
	transfers, err := hub.NewFileTransferManager(st, t.TempDir(), "http://hub.example:8443", sessions, nm, logger)
	if err != nil {
		t.Fatalf("file transfer manager: %v", err)
	}
	server.SetFileTransfers(transfers)

	hash, err := auth.HashPassword("secret")
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	if err := st.CreateUser(t.Context(), &models.User{
		Username: "stress-admin", PasswordHash: hash, Role: models.RoleAdministrator,
	}); err != nil {
		t.Fatalf("create admin: %v", err)
	}
	return &stressServer{
		server:  server,
		handler: server.Routes(),
		store:   st,
		nm:      nm,
		token:   loginAs(t, server, "stress-admin", "secret"),
		logs:    logs,
	}
}

func (s *stressServer) do(t *testing.T, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, path, reader)
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+s.token)
	rec := httptest.NewRecorder()
	s.handler.ServeHTTP(rec, req)
	return rec
}

// startConcurrent 以 barrier 同时发起 n 个请求；返回结果槽与完成信号。
func startConcurrent(handler http.Handler, n int, build func(i int) *http.Request) ([]*httptest.ResponseRecorder, <-chan struct{}) {
	recs := make([]*httptest.ResponseRecorder, n)
	start := make(chan struct{})
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			rec := httptest.NewRecorder()
			handler.ServeHTTP(rec, build(i))
			recs[i] = rec
		}(i)
	}
	done := make(chan struct{})
	go func() {
		close(start)
		wg.Wait()
		close(done)
	}()
	return recs, done
}

func seedTransfers(t *testing.T, st *store.SQLiteStore, count int) {
	t.Helper()
	for i := 0; i < count; i++ {
		tr := &models.FileTransfer{
			ID:           fmt.Sprintf("stress-xfer-%03d", i),
			SourceNodeID: "stress-src",
			SourcePath:   fmt.Sprintf("/var/tmp/src-%03d", i),
			Targets: []*models.FileTransferTarget{{
				NodeID:          fmt.Sprintf("stress-dst-%03d", i),
				DestinationPath: fmt.Sprintf("/var/tmp/dst-%03d", i),
			}},
		}
		if err := st.CreateFileTransfer(t.Context(), tr); err != nil {
			t.Fatalf("create transfer: %v", err)
		}
	}
}

// TestTransfersListConcurrentNoHang 覆盖 QA-20261018-F1：
//   - 16 并发 GET /api/transfers（31 条数据）必须在合理时限内全部 200 并带出目标；
//   - 并发期间 GET /api/tasks 不得被拖死（旧实现挂起时需 3024ms 甚至超时）。
func TestTransfersListConcurrentNoHang(t *testing.T) {
	s := newStressServer(t)
	seedTransfers(t, s.store, 31)

	const concurrency = 16
	recs, done := startConcurrent(s.handler, concurrency, func(int) *http.Request {
		req := httptest.NewRequest(http.MethodGet, "/api/transfers", nil)
		req.Header.Set("Authorization", "Bearer "+s.token)
		return req
	})

	// 挂起期间旁路端点必须保持可用（旧实现下池被占满，这里会超时）。
	collateral := make(chan *httptest.ResponseRecorder, 1)
	go func() {
		req := httptest.NewRequest(http.MethodGet, "/api/tasks", nil)
		req.Header.Set("Authorization", "Bearer "+s.token)
		rec := httptest.NewRecorder()
		s.handler.ServeHTTP(rec, req)
		collateral <- rec
	}()
	select {
	case rec := <-collateral:
		if rec.Code != http.StatusOK {
			t.Fatalf("GET /api/tasks during burst = %d %s, want 200", rec.Code, rec.Body.String())
		}
	case <-time.After(10 * time.Second):
		t.Fatal("GET /api/tasks blocked during transfers burst (connection pool starvation)")
	}

	select {
	case <-done:
	case <-time.After(20 * time.Second):
		t.Fatal("concurrent GET /api/transfers hung (connection pool deadlock)")
	}
	for i, rec := range recs {
		if rec.Code != http.StatusOK {
			t.Fatalf("request %d: status %d body %s", i, rec.Code, rec.Body.String())
		}
		var items []models.FileTransfer
		if err := json.Unmarshal(rec.Body.Bytes(), &items); err != nil {
			t.Fatalf("request %d: decode: %v", i, err)
		}
		if len(items) != 31 {
			t.Fatalf("request %d: transfers = %d, want 31", i, len(items))
		}
		for _, item := range items {
			if len(item.Targets) != 1 {
				t.Fatalf("request %d: transfer %s targets = %d, want 1", i, item.ID, len(item.Targets))
			}
		}
	}
}

// TestNodesDeleteConcurrentAllSucceed 覆盖 QA-20261018-F1 的第二条触发路径：
// NodeManager.Delete 内部调用 ListFileTransfers，旧实现下 16 并发删除只成功 1 个。
func TestNodesDeleteConcurrentAllSucceed(t *testing.T) {
	s := newStressServer(t)
	const nodes = 16
	ids := make([]string, 0, nodes)
	for i := 0; i < nodes; i++ {
		n, _, _, err := s.nm.RegisterOrUpdate(t.Context(),
			fmt.Sprintf("stress-agent-%02d", i), fmt.Sprintf("stress-node-%02d", i),
			fmt.Sprintf("192.0.2.%d", i+1), "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
		if err != nil {
			t.Fatalf("register node %d: %v", i, err)
		}
		ids = append(ids, n.ID)
	}

	recs, done := startConcurrent(s.handler, nodes, func(i int) *http.Request {
		req := httptest.NewRequest(http.MethodDelete, "/api/nodes/"+ids[i], nil)
		req.Header.Set("Authorization", "Bearer "+s.token)
		return req
	})
	select {
	case <-done:
	case <-time.After(20 * time.Second):
		t.Fatal("concurrent DELETE /api/nodes hung (connection pool deadlock)")
	}
	for i, rec := range recs {
		if rec.Code != http.StatusOK {
			t.Fatalf("delete node %d: status %d body %s", i, rec.Code, rec.Body.String())
		}
	}
	for _, id := range ids {
		if _, err := s.nm.GetNode(t.Context(), id); err == nil {
			t.Fatalf("node %s still exists after concurrent delete", id)
		}
	}
}

// TestSchedulesDeleteConcurrentNoBusyLeak 覆盖 QA-20261018-F2：
// 16 并发 DELETE /api/schedules 必须全部成功（或 503 归一化文案），
// 不得返回 409 + 驱动原文 "database is locked (5) (SQLITE_BUSY)"。
func TestSchedulesDeleteConcurrentNoBusyLeak(t *testing.T) {
	s := newStressServer(t)
	rec := s.do(t, http.MethodPost, "/api/tasks",
		`{"name":"stress-task","type":"command","command":"true","target":{"type":"label","label_key":"env"}}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create task = %d %s", rec.Code, rec.Body.String())
	}
	taskID := decodeResource[models.Task](t, rec).ID

	const concurrency = 16
	ids := make([]string, 0, concurrency)
	for i := 0; i < concurrency; i++ {
		rec := s.do(t, http.MethodPost, "/api/schedules",
			fmt.Sprintf(`{"task_id":%q,"type":"interval","interval_sec":60,"execution_owner":"hub"}`, taskID))
		if rec.Code != http.StatusOK {
			t.Fatalf("create schedule %d = %d %s", i, rec.Code, rec.Body.String())
		}
		ids = append(ids, decodeResource[models.Schedule](t, rec).ID)
	}

	recs, done := startConcurrent(s.handler, concurrency, func(i int) *http.Request {
		req := httptest.NewRequest(http.MethodDelete, "/api/schedules/"+ids[i], nil)
		req.Header.Set("Authorization", "Bearer "+s.token)
		return req
	})
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		t.Fatal("concurrent DELETE /api/schedules hung")
	}

	failures := 0
	for i, rec := range recs {
		body := rec.Body.String()
		for _, leak := range []string{"SQLITE_BUSY", "database is locked", "sqlite"} {
			if strings.Contains(strings.ToLower(body), strings.ToLower(leak)) {
				t.Fatalf("schedule %d leaked driver text: %d %s", i, rec.Code, body)
			}
		}
		switch rec.Code {
		case http.StatusOK:
		case http.StatusServiceUnavailable:
			// 允许可重试的归一化失败，但必须有 error 日志且文案归一化。
			if !strings.Contains(body, "storage busy, please retry") {
				t.Fatalf("schedule %d 503 body = %s, want normalized storage busy", i, body)
			}
			failures++
		default:
			t.Fatalf("schedule %d delete = %d %s, want 200 or 503", i, rec.Code, body)
		}
	}
	if failures > 0 && !strings.Contains(s.logs.String(), "level=ERROR") {
		t.Fatalf("normalized 503 without error log:\n%s", s.logs.String())
	}
	if strings.Contains(s.logs.String(), "context canceled") && strings.Contains(s.logs.String(), "level=ERROR") {
		t.Fatalf("client cancellation must not be logged as ERROR:\n%s", s.logs.String())
	}

	// 全部删除：列表应为空。
	rec = s.do(t, http.MethodGet, "/api/schedules", "")
	var remaining []models.Schedule
	if err := json.Unmarshal(rec.Body.Bytes(), &remaining); err != nil {
		t.Fatalf("decode schedules: %v (body %s)", err, rec.Body.String())
	}
	if len(remaining) != 0 {
		t.Fatalf("schedules remaining after concurrent delete = %d, want 0", len(remaining))
	}
}

// TestClientCancelNotLoggedAsError 覆盖 QA-20261018-F8：
// 客户端取消（context canceled）不得记 ERROR 级日志；真实存储错误仍必须记 ERROR。
func TestClientCancelNotLoggedAsError(t *testing.T) {
	s := newStressServer(t)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	req := httptest.NewRequest(http.MethodGet, "/api/nodes", nil).WithContext(ctx)
	rec := httptest.NewRecorder()
	s.server.handleNodes(rec, req)
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("canceled request status = %d, want 500 (响应语义不变)", rec.Code)
	}
	if logs := s.logs.String(); strings.Contains(logs, "level=ERROR") {
		t.Fatalf("client cancellation logged as ERROR:\n%s", logs)
	}

	// 对照：真实存储错误仍按 ERROR 记录（错误语义未被整体降级）。
	s.logs.Reset()
	errRec := httptest.NewRecorder()
	s.server.writeInternalError(errRec, httptest.NewRequest(http.MethodGet, "/api/nodes", nil), io.ErrUnexpectedEOF)
	if logs := s.logs.String(); !strings.Contains(logs, "level=ERROR") || !strings.Contains(logs, "/api/nodes") {
		t.Fatalf("genuine error must still be logged at ERROR:\n%s", logs)
	}
}
