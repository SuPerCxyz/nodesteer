package api

import (
	"database/sql"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// TestWriteMutationErrorNormalization 锁定写路径错误归一化契约（QA-20261018-F2/F4）：
//   - 缺行 → 404 归一化文案；
//   - SQLITE_BUSY → 503 storage busy + ERROR 日志（驱动原文只进日志）；
//   - 其它驱动错误（含 UNIQUE 约束原文）→ 500 internal error + ERROR 日志，不外泄；
//   - 业务校验错误 → 指定 4xx + 既有文案。
func TestWriteMutationErrorNormalization(t *testing.T) {
	s := newStressServer(t)

	cases := []struct {
		name     string
		err      error
		status   int
		wantCode int
		wantBody string
		wantLog  bool
	}{
		{
			name: "missing row -> 404", err: sql.ErrNoRows,
			status: http.StatusConflict, wantCode: http.StatusNotFound, wantBody: "node not found",
		},
		{
			name: "busy -> 503", err: errors.New("database is locked (5) (SQLITE_BUSY)"),
			status: http.StatusConflict, wantCode: http.StatusServiceUnavailable, wantBody: "storage busy, please retry", wantLog: true,
		},
		{
			name: "unique constraint -> 500", err: errors.New("UNIQUE constraint failed: users.username"),
			status: http.StatusConflict, wantCode: http.StatusInternalServerError, wantBody: "internal error", wantLog: true,
		},
		{
			name: "business conflict -> 409", err: errors.New("node n1 is referenced by group g1"),
			status: http.StatusConflict, wantCode: http.StatusConflict, wantBody: "node n1 is referenced by group g1",
		},
		{
			name: "5xx target never leaks", err: errors.New("some persistence failure"),
			status: http.StatusInternalServerError, wantCode: http.StatusInternalServerError, wantBody: "internal error", wantLog: true,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s.logs.Reset()
			rec := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodDelete, "/api/nodes/n1", nil)
			s.server.writeMutationError(rec, req, tc.status, "node not found", tc.err)

			if rec.Code != tc.wantCode || !strings.Contains(rec.Body.String(), tc.wantBody) {
				t.Fatalf("status = %d body = %s, want %d %q", rec.Code, rec.Body.String(), tc.wantCode, tc.wantBody)
			}
			for _, leak := range []string{"SQLITE_BUSY", "constraint failed", "persistence failure"} {
				if strings.Contains(rec.Body.String(), leak) {
					t.Fatalf("error text leaked to client: %s", rec.Body.String())
				}
			}
			logged := strings.Contains(s.logs.String(), "level=ERROR")
			if logged != tc.wantLog {
				t.Fatalf("error log = %v, want %v (logs: %s)", logged, tc.wantLog, s.logs.String())
			}
		})
	}
}
