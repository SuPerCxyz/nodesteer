package api

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// failingListNodesStore 包装真实 Store，仅把 ListNodes（PrepareEnrollment 的读路径）
// 替换为注入错误，用于验证存储层错误的归一化响应与 error 级日志。
type failingListNodesStore struct {
	*store.SQLiteStore
	err error
}

func (s failingListNodesStore) ListNodes(context.Context) ([]*models.Node, error) {
	return nil, s.err
}

// newStorageErrorServer 构造 ListNodes 必失败的 API Server（含管理员登录）。
func newStorageErrorServer(t *testing.T, injected error) (*Server, string, *bytes.Buffer) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	hash, err := auth.HashPassword("secret")
	if err != nil {
		t.Fatal(err)
	}
	if err := st.CreateUser(t.Context(), &models.User{
		Username: "storage-admin", PasswordHash: hash, Role: models.RoleAdministrator,
	}); err != nil {
		t.Fatalf("create admin: %v", err)
	}
	buf := &bytes.Buffer{}
	logger := slog.New(slog.NewTextHandler(buf, nil))
	wrapped := failingListNodesStore{SQLiteStore: st, err: injected}
	nm := hub.NewNodeManager(wrapped, hub.NewRevisionManager(wrapped))
	s := New(wrapped, auth.New(wrapped, time.Hour), nm, nil, nil, nil, nil, nil, nil, logger)
	s.SetEnrollmentConfig("reg-token", "https://hub.example:8443")
	return s, loginAs(t, s, "storage-admin", "secret"), buf
}

// TestEnrollmentStorageErrorNormalizedAndLogged 覆盖 P2 缺陷的两个次生问题：
//   - 存储层错误原文不得外泄（BUSY → 503 归一化文案；其余 → 500 internal error）；
//   - 存储层错误必须有 error 级日志（含请求上下文，此前隔离实例零记录）。
func TestEnrollmentStorageErrorNormalizedAndLogged(t *testing.T) {
	cases := []struct {
		name      string
		injected  error
		wantCode  int
		wantBody  string
		forbidden []string
	}{
		{
			name:      "SQLITE_BUSY 归一化为 503 可重试",
			injected:  errors.New("database is locked (5) (SQLITE_BUSY)"),
			wantCode:  http.StatusServiceUnavailable,
			wantBody:  "storage busy, please retry",
			forbidden: []string{"locked", "sqlite", "database"},
		},
		{
			name:      "其余存储错误归一化为 500 internal error",
			injected:  errors.New("sql: no rows in result set"),
			wantCode:  http.StatusInternalServerError,
			wantBody:  "internal error",
			forbidden: []string{"no rows", "sql:"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s, token, buf := newStorageErrorServer(t, tc.injected)
			req := httptest.NewRequest(http.MethodPost, "/api/nodes/enrollment",
				strings.NewReader(`{"node_name":"storage-fail","node_ip":"192.0.2.9"}`))
			req.Header.Set("Authorization", "Bearer "+token)
			req.Header.Set("Content-Type", "application/json")
			rec := httptest.NewRecorder()
			s.withAuth(s.handleNodeEnrollment, "read")(rec, req)

			if rec.Code != tc.wantCode {
				t.Fatalf("status = %d body %s, want %d", rec.Code, rec.Body.String(), tc.wantCode)
			}
			if !strings.Contains(rec.Body.String(), tc.wantBody) {
				t.Fatalf("body = %s, want %q", rec.Body.String(), tc.wantBody)
			}
			body := strings.ToLower(rec.Body.String())
			for _, leak := range tc.forbidden {
				if strings.Contains(body, leak) {
					t.Fatalf("storage error leaked to client: %s", rec.Body.String())
				}
			}

			log := buf.String()
			for _, want := range []string{"level=ERROR", "api request failed", "/api/nodes/enrollment"} {
				if !strings.Contains(log, want) {
					t.Fatalf("storage error log missing %q:\n%s", want, log)
				}
			}
		})
	}
}
