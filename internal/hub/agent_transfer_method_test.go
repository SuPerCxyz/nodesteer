package hub

// P6 范围外观察：agent 文件传输端点（/agent/transfers/*）405 缺 Allow。
// 修复后与 Hub 侧 methodNotAllowed 风格一致；并核对 agent 侧无「方法未拒绝」路径。

import (
	"context"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestAgentTransferEndpoint405CarriesAllow(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	rm := NewRevisionManager(st)
	nm := NewNodeManager(st, rm)
	node, cred, _, err := nm.RegisterOrUpdate(ctx, "allow-agent", "allow-node", "10.0.0.3",
		"linux", "amd64", "1", "native", false, nil)
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionManager()
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.Default())
	if err != nil {
		t.Fatal(err)
	}

	do := func(method, path string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, nil)
		req.Header.Set("X-NodeSteer-Agent-ID", node.AgentID)
		req.Header.Set("X-NodeSteer-Agent-Token", cred)
		rec := httptest.NewRecorder()
		m.HandleAgentHTTP(rec, req)
		return rec
	}

	// 已知动作 + 不支持的方法 → 405 + 精确 Allow（此前 Allow 缺失）。
	cases := []struct {
		method string
		path   string
		allow  string
	}{
		{http.MethodGet, "/agent/transfers/xfer-1/upload", "POST"},
		{http.MethodPut, "/agent/transfers/xfer-1/upload", "POST"},
		{http.MethodDelete, "/agent/transfers/xfer-1/upload", "POST"},
		{http.MethodPost, "/agent/transfers/xfer-1/download", "GET"},
		{http.MethodPut, "/agent/transfers/xfer-1/download", "GET"},
		{http.MethodDelete, "/agent/transfers/xfer-1/download", "GET"},
	}
	for _, tc := range cases {
		rec := do(tc.method, tc.path)
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s %s = %d %s, want 405", tc.method, tc.path, rec.Code, rec.Body.String())
		}
		if allow := rec.Header().Get("Allow"); allow != tc.allow {
			t.Fatalf("%s %s Allow = %q, want %q", tc.method, tc.path, allow, tc.allow)
		}
	}

	// 未知动作 / 多余路径段 → 404（不得被当作已知动作执行）。
	for _, path := range []string{
		"/agent/transfers/xfer-1/bogus",
		"/agent/transfers/xfer-1/download/extra",
		"/agent/transfers/xfer-1/upload/extra",
		"/agent/transfers/",
	} {
		if rec := do(http.MethodGet, path); rec.Code != http.StatusNotFound {
			t.Fatalf("GET %s = %d %s, want 404", path, rec.Code, rec.Body.String())
		}
	}

	// 认证缺失 → 401（方法守卫不改变鉴权语义）。
	req := httptest.NewRequest(http.MethodGet, "/agent/transfers/xfer-1/upload", nil)
	rec := httptest.NewRecorder()
	m.HandleAgentHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated upload = %d %s, want 401", rec.Code, rec.Body.String())
	}

	// 支持的方法进入正常处理：不存在的传输 → 404（GET download 通过方法守卫）。
	if rec := do(http.MethodGet, "/agent/transfers/xfer-1/download"); rec.Code != http.StatusNotFound {
		t.Fatalf("GET download missing transfer = %d %s, want 404", rec.Code, rec.Body.String())
	}
}
