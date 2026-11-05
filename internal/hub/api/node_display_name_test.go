package api

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func nodeDisplayNameTestServer(t *testing.T) (*contractServer, string, string) {
	t.Helper()
	c := newContractServer(t)
	hash, err := auth.HashPassword("secret")
	if err != nil {
		t.Fatalf("hash password: %v", err)
	}
	for _, user := range []struct {
		username string
		role     string
	}{
		{"operator", models.RoleOperator},
		{"viewer", models.RoleViewer},
	} {
		if err := c.store.CreateUser(t.Context(), &models.User{
			Username: user.username, PasswordHash: hash, Role: user.role,
		}); err != nil {
			t.Fatalf("create %s: %v", user.username, err)
		}
	}
	return c, loginAs(t, c.server, "operator", "secret"), loginAs(t, c.server, "viewer", "secret")
}

func nodeDisplayNameDo(t *testing.T, c *contractServer, token, method, path, body string) *httptest.ResponseRecorder {
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
	c.handler.ServeHTTP(rec, req)
	return rec
}

func createDisplayNameNode(t *testing.T, c *contractServer, id, hostname, displayName string) *models.Node {
	t.Helper()
	node := &models.Node{
		ID: id, AgentID: "agent-" + id, Hostname: hostname, DisplayName: displayName,
		Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative,
	}
	if err := c.store.UpsertNode(t.Context(), node); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	return node
}

func renameAudits(t *testing.T, c *contractServer, nodeID string) []*models.AuditLog {
	t.Helper()
	logs, err := c.store.ListAudit(t.Context(), store.AuditFilter{Action: "rename", Limit: 100})
	if err != nil {
		t.Fatalf("list audit: %v", err)
	}
	out := []*models.AuditLog{}
	for _, log := range logs {
		if log.Resource == "node" && log.ResourceID == nodeID {
			out = append(out, log)
		}
	}
	return out
}

// TestNodeDisplayNameAPIContract 覆盖读接口字段、Operator/Viewer/匿名权限、
// 清除、重复值与节点不存在语义。
func TestNodeDisplayNameAPIContract(t *testing.T) {
	c, operator, viewer := nodeDisplayNameTestServer(t)
	node := createDisplayNameNode(t, c, "node-1", "host-1", "初始名称")

	rec := nodeDisplayNameDo(t, c, operator, http.MethodGet, "/api/nodes/node-1", "")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"display_name":"初始名称"`) {
		t.Fatalf("GET node = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, operator, http.MethodGet, "/api/nodes", "")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"display_name":"初始名称"`) {
		t.Fatalf("GET nodes = %d %s", rec.Code, rec.Body.String())
	}

	// Operator 可重命名，首尾空白被裁剪。
	rec = nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID, `{"display_name":"  生产数据库 01  "}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("operator rename = %d %s", rec.Code, rec.Body.String())
	}
	persisted, err := c.store.GetNode(t.Context(), node.ID)
	if err != nil || persisted.DisplayName != "生产数据库 01" {
		t.Fatalf("display_name = %+v err=%v", persisted, err)
	}
	if persisted.Hostname != "host-1" {
		t.Fatalf("hostname changed to %q", persisted.Hostname)
	}
	audits := renameAudits(t, c, node.ID)
	if len(audits) != 1 || !strings.Contains(audits[0].Detail, `"old":"初始名称"`) || !strings.Contains(audits[0].Detail, `"new":"生产数据库 01"`) {
		t.Fatalf("rename audits = %+v", audits)
	}
	if audits[0].Username != "operator" {
		t.Fatalf("audit username = %q", audits[0].Username)
	}

	// 相同值重复提交成功但不新增审计。
	rec = nodeDisplayNameDo(t, c, operator, http.MethodPut, "/api/nodes/"+node.ID, `{"display_name":"生产数据库 01"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("same value = %d %s", rec.Code, rec.Body.String())
	}
	if got := len(renameAudits(t, c, node.ID)); got != 1 {
		t.Fatalf("rename audits after same value = %d, want 1", got)
	}

	// Viewer 403，值不变。
	rec = nodeDisplayNameDo(t, c, viewer, http.MethodPost, "/api/nodes/"+node.ID, `{"display_name":"viewer rename"}`)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("viewer rename = %d %s", rec.Code, rec.Body.String())
	}
	if persisted, _ = c.store.GetNode(t.Context(), node.ID); persisted.DisplayName != "生产数据库 01" {
		t.Fatalf("viewer changed display_name to %q", persisted.DisplayName)
	}
	if got := len(renameAudits(t, c, node.ID)); got != 1 {
		t.Fatalf("viewer wrote audit, count = %d", got)
	}

	// 匿名 401。
	rec = nodeDisplayNameDo(t, c, "", http.MethodPost, "/api/nodes/"+node.ID, `{"display_name":"anon"}`)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous rename = %d %s", rec.Code, rec.Body.String())
	}

	// 空值/仅空白清除。
	for _, body := range []string{`{"display_name":""}`, `{"display_name":"   "}`} {
		rec = nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID, body)
		if rec.Code != http.StatusOK {
			t.Fatalf("clear %s = %d %s", body, rec.Code, rec.Body.String())
		}
		if persisted, _ = c.store.GetNode(t.Context(), node.ID); persisted.DisplayName != "" {
			t.Fatalf("display_name after %s = %q", body, persisted.DisplayName)
		}
	}

	// 不存在的节点 404。
	rec = nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/missing", `{"display_name":"x"}`)
	if rec.Code != http.StatusNotFound || !strings.Contains(rec.Body.String(), "node not found") {
		t.Fatalf("missing node = %d %s", rec.Code, rec.Body.String())
	}

	// 批量接口不接受 display_name。
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes", `{"id":"`+node.ID+`","display_name":"batch"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("batch display_name = %d %s", rec.Code, rec.Body.String())
	}
	if persisted, _ = c.store.GetNode(t.Context(), node.ID); persisted.DisplayName != "" {
		t.Fatalf("batch wrote display_name = %q", persisted.DisplayName)
	}
}

// TestNodeDisplayNameValidation 覆盖 64/65 码点、emoji、控制字符、非法 JSON 与重名。
func TestNodeDisplayNameValidation(t *testing.T) {
	c, operator, _ := nodeDisplayNameTestServer(t)
	node := createDisplayNameNode(t, c, "node-1", "host-1", "keep")

	cases := []struct {
		name string
		body string
		want int
	}{
		{"64 runes", `{"display_name":"` + strings.Repeat("a", 64) + `"}`, http.StatusOK},
		{"65 runes", `{"display_name":"` + strings.Repeat("a", 65) + `"}`, http.StatusBadRequest},
		{"64 emoji", `{"display_name":"` + strings.Repeat("😀", 64) + `"}`, http.StatusOK},
		{"65 emoji", `{"display_name":"` + strings.Repeat("😀", 65) + `"}`, http.StatusBadRequest},
		{"newline", `{"display_name":"a\nb"}`, http.StatusBadRequest},
		{"tab", `{"display_name":"a\tb"}`, http.StatusBadRequest},
		{"del", `{"display_name":"a\u007fb"}`, http.StatusBadRequest},
		{"c1 control", `{"display_name":"a\u0085b"}`, http.StatusBadRequest},
		{"internal spaces", `{"display_name":"生产 数据库"}`, http.StatusOK},
	}
	for _, tc := range cases {
		rec := nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID, tc.body)
		if rec.Code != tc.want {
			t.Fatalf("%s = %d %s, want %d", tc.name, rec.Code, rec.Body.String(), tc.want)
		}
		if tc.want == http.StatusBadRequest {
			persisted, _ := c.store.GetNode(t.Context(), node.ID)
			if persisted.DisplayName == strings.Repeat("a", 65) || persisted.DisplayName == strings.Repeat("😀", 65) {
				t.Fatalf("%s persisted invalid value", tc.name)
			}
		}
	}
	// 非法 JSON 不写库。
	rec := nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID, `{"display_name":`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid json = %d %s", rec.Code, rec.Body.String())
	}

	// 重名允许：两个节点可共享同一显示名。
	other := createDisplayNameNode(t, c, "node-2", "host-2", "")
	rec = nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+other.ID, `{"display_name":"重名"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("rename node-2 = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID, `{"display_name":"重名"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("duplicate display_name = %d %s", rec.Code, rec.Body.String())
	}
}

// TestNodeDisplayNameAtomicFieldPermissions 覆盖同请求多字段：权限与格式校验先于写入，
// 任一失败不产生部分修改；Administrator 可同时更新状态与显示名。
func TestNodeDisplayNameAtomicFieldPermissions(t *testing.T) {
	c, operator, _ := nodeDisplayNameTestServer(t)
	node := createDisplayNameNode(t, c, "node-1", "host-1", "keep")

	// Operator 同时提交 status → 整体 403，display_name 也不得写入。
	rec := nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID,
		`{"status":"maintenance","display_name":"partial"}`)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("operator status+rename = %d %s", rec.Code, rec.Body.String())
	}
	persisted, _ := c.store.GetNode(t.Context(), node.ID)
	if persisted.DisplayName != "keep" || persisted.Status != models.NodeStatusOnline {
		t.Fatalf("partial update: display=%q status=%q", persisted.DisplayName, persisted.Status)
	}

	// Administrator 提交非法 display_name + 合法 status → 400，status 不变。
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes/"+node.ID,
		`{"status":"maintenance","display_name":"`+strings.Repeat("x", 65)+`"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid combined = %d %s", rec.Code, rec.Body.String())
	}
	persisted, _ = c.store.GetNode(t.Context(), node.ID)
	if persisted.DisplayName != "keep" || persisted.Status != models.NodeStatusOnline {
		t.Fatalf("invalid combined partial update: display=%q status=%q", persisted.DisplayName, persisted.Status)
	}

	// 合法组合原子成功，并产生 node.update 与 node.rename。
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes/"+node.ID,
		`{"status":"maintenance","labels":{"env":"prod"},"display_name":"生产库"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("admin combined = %d %s", rec.Code, rec.Body.String())
	}
	persisted, _ = c.store.GetNode(t.Context(), node.ID)
	if persisted.DisplayName != "生产库" || persisted.Status != models.NodeStatusMaintenance || persisted.Labels["env"] != "prod" {
		t.Fatalf("combined update = %+v", persisted)
	}
	logs, err := c.store.ListAudit(t.Context(), store.AuditFilter{Limit: 100})
	if err != nil {
		t.Fatalf("list audit: %v", err)
	}
	var update, rename bool
	for _, log := range logs {
		if log.Resource != "node" || log.ResourceID != node.ID {
			continue
		}
		if log.Action == "update" {
			update = true
		}
		if log.Action == "rename" {
			rename = true
		}
	}
	if !update || !rename {
		t.Fatalf("audit actions update=%v rename=%v", update, rename)
	}

	// 审计不包含 credential 或令牌字样。
	for _, log := range logs {
		lower := strings.ToLower(log.Detail)
		if strings.Contains(lower, "cred") || strings.Contains(lower, "token") {
			t.Fatalf("rename audit leaked secret: %s", log.Detail)
		}
	}
}

// TestNodeDisplayNameAuditTimestampsAndActor 覆盖审计的时间与操作者字段。
func TestNodeDisplayNameAuditTimestampsAndActor(t *testing.T) {
	c, operator, _ := nodeDisplayNameTestServer(t)
	node := createDisplayNameNode(t, c, "node-1", "host-1", "")
	rec := nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID, `{"display_name":"审计节点"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("rename = %d %s", rec.Code, rec.Body.String())
	}
	audits := renameAudits(t, c, node.ID)
	if len(audits) != 1 {
		t.Fatalf("audits = %d", len(audits))
	}
	if audits[0].Action != "rename" || audits[0].Resource != "node" {
		t.Fatalf("audit resource/action = %s/%s", audits[0].Resource, audits[0].Action)
	}
	if audits[0].Detail != `{"old":"","new":"审计节点"}` {
		t.Fatalf("audit detail = %s", audits[0].Detail)
	}
	if audits[0].Username == "" || audits[0].CreatedAt.IsZero() {
		t.Fatalf("audit actor/time missing: %+v", audits[0])
	}
}
