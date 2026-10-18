package hubserver

import (
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// TestDeleteMissingResourceIs404 锁定统一后的 DELETE 语义
// （缺陷 OBS-20261015-API-CONTRACT-DEVIATIONS-LOW ②）：
//   - 不存在 / 缺 id → 404 归一化文案（与 nodes 一致），不再静默 200；
//   - 方法不支持（users / executions 无 DELETE）→ 405。
//
// 旧用例曾把「删除不存在资源返回 200」当作幂等设计固定，本批按
// 「统一为 404」的产品裁决改为锁定 404。
func TestDeleteMissingResourceIs404(t *testing.T) {
	_, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	missing := uuid.NewString()
	cases := []struct {
		path string
		msg  string
	}{
		{"/api/scripts/" + missing, "script not found"},
		{"/api/tasks/" + missing, "task not found"},
		{"/api/schedules/" + missing, "schedule not found"},
		{"/api/groups/" + missing, "group not found"},
		{"/api/artifacts/" + missing, "artifact not found"},
		{"/api/applications/" + missing, "application not found"},
		{"/api/nodes/" + missing, "node not found"},
	}
	for _, tc := range cases {
		req, err := http.NewRequest(http.MethodDelete, base+tc.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("delete %s: %v", tc.path, err)
		}
		raw, _ := io.ReadAll(resp.Body)
		body := string(raw)
		resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
			t.Fatalf("delete %s: got %d body %s want 404", tc.path, resp.StatusCode, body)
		}
		if !strings.Contains(body, tc.msg) {
			t.Fatalf("delete %s: body %s want normalized %q", tc.path, body, tc.msg)
		}
	}
}

// TestDeleteUnsupportedMethodIs405 无 DELETE 的资源统一 405（带 Allow）。
func TestDeleteUnsupportedMethodIs405(t *testing.T) {
	_, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	for _, path := range []string{"/api/users/" + uuid.NewString(), "/api/executions/" + uuid.NewString()} {
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
		if resp.StatusCode != http.StatusMethodNotAllowed {
			t.Fatalf("delete %s: got %d want 405", path, resp.StatusCode)
		}
		if resp.Header.Get("Allow") == "" {
			t.Fatalf("delete %s: 405 must carry Allow header", path)
		}
	}
}
