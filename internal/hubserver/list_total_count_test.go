package hubserver

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/google/uuid"
)

// apiGetRaw 发起带认证的 GET 请求，返回状态码、X-Total-Count、Access-Control-Expose-Headers 与响应体。
func apiGetRaw(t *testing.T, base, path, token string) (int, string, string, string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodGet, base+path, nil)
	if err != nil {
		t.Fatalf("request GET %s: %v", path, err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("do GET %s: %v", path, err)
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("read body %s: %v", path, err)
	}
	return resp.StatusCode, resp.Header.Get("X-Total-Count"),
		resp.Header.Get("Access-Control-Expose-Headers"), string(b)
}

func decodeExecutionList(t *testing.T, body string) []*models.Execution {
	t.Helper()
	var out []*models.Execution
	if err := json.Unmarshal([]byte(body), &out); err != nil {
		t.Fatalf("decode executions: %v (body %s)", err, body)
	}
	return out
}

// FAIL-A-005：执行列表暴露 X-Total-Count，总数不随 limit/offset 变化，且 offset 可用于翻页。
func TestExecutionsListTotalCount(t *testing.T) {
	h, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	ctx := context.Background()

	// 7 条 task-page 记录（唯一索引含 node_id，故每条使用不同 node）+ 3 条干扰记录。
	for i := 0; i < 7; i++ {
		if err := h.Store().CreateExecution(ctx, &models.Execution{
			ID: uuid.NewString(), TaskID: "task-page", NodeID: fmt.Sprintf("node-%d", i),
			TriggerType: models.TriggerManual, Status: models.ExecStatusSuccess,
		}); err != nil {
			t.Fatalf("create execution %d: %v", i, err)
		}
	}
	for i := 0; i < 3; i++ {
		if err := h.Store().CreateExecution(ctx, &models.Execution{
			ID: uuid.NewString(), TaskID: "task-other", NodeID: fmt.Sprintf("other-%d", i),
			TriggerType: models.TriggerManual, Status: models.ExecStatusFailed,
		}); err != nil {
			t.Fatalf("create other execution %d: %v", i, err)
		}
	}

	// 第一页：响应体仍是裸数组，长度为 limit，总数为全部匹配行数。
	status, total, expose, body := apiGetRaw(t, base, "/api/executions?limit=2", token)
	if status != http.StatusOK {
		t.Fatalf("list executions: status %d body %s", status, body)
	}
	if total != "10" {
		t.Fatalf("X-Total-Count = %q, want 10 (body %s)", total, body)
	}
	if !strings.Contains(expose, "X-Total-Count") {
		t.Fatalf("Access-Control-Expose-Headers = %q, want it to contain X-Total-Count", expose)
	}
	first := decodeExecutionList(t, body)
	if len(first) != 2 {
		t.Fatalf("first page size = %d, want 2", len(first))
	}

	// 第二页：offset 生效且总数不变；两页记录不重复。
	status, total, _, body = apiGetRaw(t, base, "/api/executions?limit=2&offset=2", token)
	if status != http.StatusOK {
		t.Fatalf("list executions page 2: status %d body %s", status, body)
	}
	if total != "10" {
		t.Fatalf("page 2 X-Total-Count = %q, want 10", total)
	}
	second := decodeExecutionList(t, body)
	if len(second) != 2 {
		t.Fatalf("second page size = %d, want 2", len(second))
	}
	seen := map[string]bool{}
	for _, e := range first {
		seen[e.ID] = true
	}
	for _, e := range second {
		if seen[e.ID] {
			t.Fatalf("execution %s appears on both pages", e.ID)
		}
	}

	// 过滤条件参与计数：task-page 共 7 条，limit 不改变总数。
	status, total, _, body = apiGetRaw(t, base, "/api/executions?task_id=task-page&limit=1", token)
	if status != http.StatusOK {
		t.Fatalf("filtered executions: status %d body %s", status, body)
	}
	if total != "7" {
		t.Fatalf("filtered X-Total-Count = %q, want 7", total)
	}
	if got := decodeExecutionList(t, body); len(got) != 1 {
		t.Fatalf("filtered page size = %d, want 1", len(got))
	}
}

// FAIL-A-005：审计列表暴露 X-Total-Count，过滤计数正确且不随 limit 变化。
func TestAuditListTotalCount(t *testing.T) {
	h, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	ctx := context.Background()

	for i := 0; i < 5; i++ {
		if err := h.Store().AddAudit(ctx, &models.AuditLog{
			ID: uuid.NewString(), UserID: "u-page", Username: "page-user",
			Action: "total_count_test", Resource: "test", ResourceID: fmt.Sprintf("r-%d", i),
		}); err != nil {
			t.Fatalf("add audit %d: %v", i, err)
		}
	}
	if err := h.Store().AddAudit(ctx, &models.AuditLog{
		ID: uuid.NewString(), UserID: "u-other", Username: "other",
		Action: "other_action", Resource: "test",
	}); err != nil {
		t.Fatalf("add other audit: %v", err)
	}

	status, total, expose, body := apiGetRaw(t, base, "/api/audit?action=total_count_test&limit=2", token)
	if status != http.StatusOK {
		t.Fatalf("list audit: status %d body %s", status, body)
	}
	if total != "5" {
		t.Fatalf("X-Total-Count = %q, want 5 (body %s)", total, body)
	}
	if !strings.Contains(expose, "X-Total-Count") {
		t.Fatalf("Access-Control-Expose-Headers = %q, want it to contain X-Total-Count", expose)
	}
	var list []*models.AuditLog
	if err := json.Unmarshal([]byte(body), &list); err != nil {
		t.Fatalf("audit body is not a JSON array: %v (body %s)", err, body)
	}
	if len(list) != 2 {
		t.Fatalf("audit page size = %d, want 2", len(list))
	}
	for _, a := range list {
		if a.Action != "total_count_test" {
			t.Fatalf("audit action = %q, want total_count_test", a.Action)
		}
	}

	// limit 变化不影响总数；按 user_id 过滤同样生效。
	status, total, _, _ = apiGetRaw(t, base, "/api/audit?action=total_count_test&limit=500", token)
	if status != http.StatusOK || total != "5" {
		t.Fatalf("audit total with limit=500: status %d total %q, want 200/5", status, total)
	}
	status, total, _, _ = apiGetRaw(t, base, "/api/audit?user_id=u-page&limit=3", token)
	if status != http.StatusOK || total != "5" {
		t.Fatalf("audit total by user: status %d total %q, want 200/5", status, total)
	}
}
