package hubserver

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"

	"github.com/google/uuid"
)

// apiDoWithHeaders 发起带认证请求并返回状态码、响应头与响应体。
func apiDoWithHeaders(t *testing.T, base, method, path, token, body string) (int, http.Header, string) {
	t.Helper()
	var reader io.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, base+path, reader)
	if err != nil {
		t.Fatalf("request %s %s: %v", method, path, err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
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
	return resp.StatusCode, resp.Header, string(b)
}

// TestTransfersMethodGuardIs405 方法守卫先于鉴权：非 GET/POST 一律 405 + Allow，
// 不再把方法错误伪装成 403（缺陷 OBS-20261015-API-CONTRACT-DEVIATIONS-LOW ③）。
func TestTransfersMethodGuardIs405(t *testing.T) {
	_, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	missing := uuid.NewString()

	for _, tc := range []struct {
		method string
		path   string
	}{
		{http.MethodPut, "/api/transfers/" + missing},
		{http.MethodDelete, "/api/transfers/" + missing},
		{http.MethodPatch, "/api/transfers/" + missing + "/cancel"},
	} {
		status, header, body := apiDoWithHeaders(t, base, tc.method, tc.path, token, "")
		if status != http.StatusMethodNotAllowed {
			t.Fatalf("%s %s: status %d body %s, want 405", tc.method, tc.path, status, body)
		}
		if allow := header.Get("Allow"); !strings.Contains(allow, "GET") || !strings.Contains(allow, "POST") {
			t.Fatalf("%s %s: Allow = %q, want GET, POST", tc.method, tc.path, allow)
		}
	}

	// 动作子路径仅支持 POST：GET /{id}/cancel 同样 405（Allow: POST）
	status, header, body := apiDoWithHeaders(t, base, http.MethodGet, "/api/transfers/"+missing+"/cancel", token, "")
	if status != http.StatusMethodNotAllowed {
		t.Fatalf("GET transfer action: status %d body %s, want 405", status, body)
	}
	if allow := header.Get("Allow"); !strings.Contains(allow, "POST") {
		t.Fatalf("GET transfer action: Allow = %q, want POST", allow)
	}
}

// TestNodeDeleteExistenceOracleClosed 无写权限用户对「存在 id」与「不存在 id」
// 必须得到同一 403，不能据 403/404 差异枚举 node id
// （缺陷 OBS-20261015-API-CONTRACT-DEVIATIONS-LOW ⑧）。
func TestNodeDeleteExistenceOracleClosed(t *testing.T) {
	h, base := newRBACTestServer(t)
	adminToken := apiLogin(t, base, "admin", "admin123")

	// 准备：一个真实节点 + 一个 operator 用户
	existing, _, _, err := h.Nodes().RegisterOrUpdate(t.Context(), "oracle-agent", "oracle-node", "10.0.0.9",
		"linux", "amd64", "1", "native", false, nil)
	if err != nil {
		t.Fatal(err)
	}
	status, body := apiDo(t, base, http.MethodPost, "/api/users",
		adminToken, `{"username":"oracle-op","password":"op-secret","role":"operator"}`)
	if status != http.StatusOK {
		t.Fatalf("create operator: %d %s", status, body)
	}
	opToken := apiLogin(t, base, "oracle-op", "op-secret")

	for _, id := range []string{existing.ID, uuid.NewString()} {
		status, _, body := apiDoWithHeaders(t, base, http.MethodDelete, "/api/nodes/"+id, opToken, "")
		if status != http.StatusForbidden {
			t.Fatalf("operator DELETE node %s: status %d body %s, want 403 (no oracle)", id, status, body)
		}
	}

	// 管理员对不存在 id：统一 404 归一化文案
	status, _, body = apiDoWithHeaders(t, base, http.MethodDelete, "/api/nodes/"+uuid.NewString(), adminToken, "")
	if status != http.StatusNotFound || !strings.Contains(body, "node not found") {
		t.Fatalf("admin DELETE missing node: status %d body %s, want 404 node not found", status, body)
	}
}

// TestNodeUpdateMissingReturns404 更新不存在的节点必须 404 归一化文案，
// 不再回 400 + 存储层 "sql: no rows in result set" 原文。
func TestNodeUpdateMissingReturns404(t *testing.T) {
	_, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	status, _, body := apiDoWithHeaders(t, base, http.MethodPut, "/api/nodes/"+uuid.NewString(), token, `{"status":"offline"}`)
	if status != http.StatusNotFound || !strings.Contains(body, "node not found") {
		t.Fatalf("PUT missing node: status %d body %s, want 404 node not found", status, body)
	}
	for _, leak := range []string{"no rows", "sql:"} {
		if strings.Contains(strings.ToLower(body), leak) {
			t.Fatalf("storage error leaked: %s", body)
		}
	}
}

// TestSessionKeysAreSnakeCase /api/login.user 与 /api/me 键名统一小写 snake_case，
// 旧 PascalCase 键不得再出现（缺陷 OBS-20261015-API-CONTRACT-DEVIATIONS-LOW ⑤）。
// 注意：Web 消费方（SessionInfo）需同批切换到小写键。
func TestSessionKeysAreSnakeCase(t *testing.T) {
	_, base := newRBACTestServer(t)
	resp, err := http.Post(base+"/api/login", "application/json",
		strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var login struct {
		Token string         `json:"token"`
		User  map[string]any `json:"user"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&login); err != nil {
		t.Fatalf("decode login: %v", err)
	}
	if login.Token == "" {
		t.Fatal("login token missing")
	}
	assertSnakeCaseSessionKeys(t, "login.user", login.User)

	status, _, body := apiDoWithHeaders(t, base, http.MethodGet, "/api/me", login.Token, "")
	if status != http.StatusOK {
		t.Fatalf("GET /api/me: %d %s", status, body)
	}
	var me map[string]any
	if err := json.Unmarshal([]byte(body), &me); err != nil {
		t.Fatalf("decode /api/me: %v", err)
	}
	assertSnakeCaseSessionKeys(t, "/api/me", me)
}

// TestListTotalCountOnlyOnPaginatedEndpoints 锁定 X-Total-Count 边界：
// 只有带 limit/offset 分页参数的列表端点（executions/audit）返回该头；
// 无分页参数的列表端点（如 nodes）不返回（缺陷
// OBS-20261015-API-CONTRACT-DEVIATIONS-LOW ④：只补已有分页的端点）。
func TestListTotalCountOnlyOnPaginatedEndpoints(t *testing.T) {
	_, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	for _, path := range []string{"/api/nodes", "/api/tasks", "/api/scripts", "/api/applications", "/api/schedules", "/api/users", "/api/transfers"} {
		status, header, body := apiDoWithHeaders(t, base, http.MethodGet, path, token, "")
		if status != http.StatusOK {
			t.Fatalf("GET %s: %d %s", path, status, body)
		}
		if got := header.Get("X-Total-Count"); got != "" {
			t.Fatalf("GET %s: X-Total-Count = %q, want absent (no pagination params)", path, got)
		}
	}
}

func assertSnakeCaseSessionKeys(t *testing.T, what string, keys map[string]any) {
	t.Helper()
	for _, key := range []string{"token", "user_id", "username", "role", "expires"} {
		if _, ok := keys[key]; !ok {
			t.Fatalf("%s missing snake_case key %q: %v", what, key, keys)
		}
	}
	for _, legacy := range []string{"Token", "UserID", "Username", "Role", "Expires"} {
		if _, ok := keys[legacy]; ok {
			t.Fatalf("%s must not expose legacy key %q: %v", what, legacy, keys)
		}
	}
}

// TestDuplicateUsernameNormalized 重复用户名归一化为 "username already exists"，
// 不回显 SQLite UNIQUE 原文（缺陷 OBS-20261016-G1-USER-DUP-SQL-MESSAGE）。
func TestDuplicateUsernameNormalized(t *testing.T) {
	_, base := newRBACTestServer(t)
	token := apiLogin(t, base, "admin", "admin123")
	create := func() (int, string) {
		return apiDo(t, base, http.MethodPost, "/api/users", token,
			`{"username":"dup-user","password":"dup-secret","role":"viewer"}`)
	}
	if status, body := create(); status != http.StatusOK {
		t.Fatalf("first create: %d %s", status, body)
	}
	status, body := create()
	if status != http.StatusBadRequest || !strings.Contains(body, "username already exists") {
		t.Fatalf("duplicate create: status %d body %s, want 400 username already exists", status, body)
	}
	for _, leak := range []string{"unique", "constraint", "sqlite", "users.username"} {
		if strings.Contains(strings.ToLower(body), leak) {
			t.Fatalf("database detail leaked: %s", body)
		}
	}
}
