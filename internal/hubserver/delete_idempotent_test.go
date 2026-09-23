package hubserver

import (
	"net/http"
	"testing"

	"github.com/google/uuid"
)

// TestDeleteMissingResourceIsIdempotent 记录产品的既有设计：DELETE 对不存在的资源返回 200，
// 以保证客户端重试幂等（见 api_test.go 中 artifact 用例注释）。
// E2E 报告中的 FAIL-A-016（DELETE 不存在资源返回 200）经复核属设计行为，不作为缺陷修复；
// 本用例用于固定该行为，避免后续误改。
func TestDeleteMissingResourceIsIdempotent(t *testing.T) {
	_, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	missing := uuid.NewString()
	for _, path := range []string{
		"/api/scripts/" + missing,
		"/api/tasks/" + missing,
		"/api/schedules/" + missing,
		"/api/groups/" + missing,
		"/api/artifacts/" + missing,
		"/api/applications/" + missing,
	} {
		req, err := http.NewRequest(http.MethodDelete, base+path, nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("delete %s: %v", path, err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("delete %s: got %d want 200 (idempotent delete)", path, resp.StatusCode)
		}
	}
}
