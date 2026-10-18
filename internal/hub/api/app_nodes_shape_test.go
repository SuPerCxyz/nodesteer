package api

import (
	"net/http"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestApplicationNodesEmptyIsArray 契约形状锁定：无绑定节点时
// GET /api/applications/{id}/nodes 必须返回 [] 而不是 null
// （缺陷 OBS-20261012-APP-NODES-NULL-SHAPE；严格消费者对 null 会误判/抛错）。
func TestApplicationNodesEmptyIsArray(t *testing.T) {
	c := newContractServer(t)
	rec := c.do(t, http.MethodPost, "/api/applications",
		`{"name":"shape-app","unit_name":"shape-app.service","binary_path":"/usr/local/bin/shape-app"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create application = %d %s", rec.Code, rec.Body.String())
	}
	app := decodeResource[models.Application](t, rec)

	rec = c.do(t, http.MethodGet, "/api/applications/"+app.ID+"/nodes", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("get application nodes = %d %s", rec.Code, rec.Body.String())
	}
	if got := strings.TrimSpace(rec.Body.String()); got != "[]" {
		t.Fatalf("empty application nodes body = %q, want []", got)
	}
}
