package api

import (
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
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// newMonitoringAPI 构造只注入监控仓储的 API Server；withSchedules 为规则
// 测试注入一个可用的 ScheduleManager（规则创建/更新只读取 Schedule 是否存在）。
func newMonitoringAPI(t *testing.T, withSchedules bool) (*Server, *store.SQLiteStore) {
	t.Helper()
	return newMonitoringAPIWithLogger(t, slog.New(slog.NewTextHandler(io.Discard, nil)), withSchedules)
}

func newMonitoringAPIWithLogger(t *testing.T, logger *slog.Logger, withSchedules bool) (*Server, *store.SQLiteStore) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	s := New(st, auth.New(st, time.Hour), nil, nil, nil, nil, nil, nil, nil, logger)
	if withSchedules {
		// 规则 API 只通过 ScheduleManager.Get 校验引用；此处无需完整调度链。
		s.schedules = hub.NewScheduleManager(st, nil, nil, nil, nil)
	}
	s.SetMonitoring(monitoring.NewRepository(st.DB()))
	return s, st
}

func seedMonitoringUsers(t *testing.T, st *store.SQLiteStore) {
	t.Helper()
	hash, err := auth.HashPassword("password")
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	for _, user := range []*models.User{
		{ID: "admin", Username: "admin", PasswordHash: hash, Role: models.RoleAdministrator},
		{ID: "viewer", Username: "viewer", PasswordHash: hash, Role: models.RoleViewer},
	} {
		if err := st.CreateUser(t.Context(), user); err != nil {
			t.Fatalf("create user %s: %v", user.Username, err)
		}
	}
}

func createMonitoringNode(t *testing.T, st *store.SQLiteStore, id string) {
	t.Helper()
	if err := st.UpsertNode(t.Context(), &models.Node{
		ID:       id,
		AgentID:  "agent-" + id,
		Hostname: id,
		Status:   "online",
	}); err != nil {
		t.Fatalf("create node %s: %v", id, err)
	}
}

func createMonitoringSchedule(t *testing.T, st *store.SQLiteStore, id string) {
	t.Helper()
	if err := st.CreateSchedule(t.Context(), &models.Schedule{
		ID:      id,
		TaskID:  "task-" + id,
		Name:    id,
		Type:    models.ScheduleTypeManual,
		Enabled: true,
	}); err != nil {
		t.Fatalf("create schedule %s: %v", id, err)
	}
}

func doMonitoringRequest(t *testing.T, handler http.Handler, method, path, token, body string) *httptest.ResponseRecorder {
	t.Helper()
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, path, reader)
	req.Header.Set("Content-Type", "application/json")
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	return rec
}
