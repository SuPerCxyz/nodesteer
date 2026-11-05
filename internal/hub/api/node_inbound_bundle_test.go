package api

import (
	"context"
	"net/http"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type fakeInboundDialer struct {
	result *hub.InboundTestResult
	err    error
}

func (f *fakeInboundDialer) Trigger(string) {}
func (f *fakeInboundDialer) TestConnection(context.Context, *models.Node) (*hub.InboundTestResult, error) {
	return f.result, f.err
}

func TestNodeInboundBundleAPIContract(t *testing.T) {
	c, operator, viewer := nodeDisplayNameTestServer(t)
	c.server.SetSessions(c.sessions)
	authority, err := inbound.OpenAuthority(inbound.Paths{Dir: filepath.Join(t.TempDir(), "pki")})
	if err != nil {
		t.Fatal(err)
	}
	service := hub.NewInboundBundleService(authority, c.server.nodes, c.sessions, "reg-token", "http://hub.example:8443", "http://hub.example", nil)
	c.server.SetInboundBundleService(service)
	node := &models.Node{
		ID: "node-bundle-api", AgentID: "agent-bundle-api", Hostname: "bundle-api",
		Endpoint: "agent.example.com", Status: models.NodeStatusPending,
	}
	if err := c.store.UpsertNode(t.Context(), node); err != nil {
		t.Fatal(err)
	}

	rec := nodeDisplayNameDo(t, c, operator, http.MethodGet, "/api/nodes/"+node.ID+"/inbound-bundle", "")
	if rec.Code != http.StatusForbidden {
		t.Fatalf("operator bundle = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, viewer, http.MethodGet, "/api/nodes/"+node.ID+"/inbound-bundle", "")
	if rec.Code != http.StatusForbidden {
		t.Fatalf("viewer bundle = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, "", http.MethodGet, "/api/nodes/"+node.ID+"/inbound-bundle", "")
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous bundle = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodGet, "/api/nodes/"+node.ID+"/inbound-bundle", "")
	if rec.Code != http.StatusOK || rec.Header().Get("Cache-Control") != "no-store" ||
		rec.Header().Get("Content-Type") != "application/gzip" || rec.Body.Len() == 0 {
		t.Fatalf("admin bundle = %d headers=%v len=%d", rec.Code, rec.Header(), rec.Body.Len())
	}
	if !strings.Contains(rec.Header().Get("Content-Disposition"), "nodesteer-inbound-bundle-api.tar.gz") {
		t.Fatalf("content disposition = %q", rec.Header().Get("Content-Disposition"))
	}
	// 第二次下载必须拒绝，要求显式轮换。
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodGet, "/api/nodes/"+node.ID+"/inbound-bundle", "")
	if rec.Code != http.StatusConflict || !strings.Contains(rec.Body.String(), "inbound_bundle_already_issued") {
		t.Fatalf("second bundle = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes/"+node.ID+"/inbound-bundle/rotate", "")
	if rec.Code != http.StatusOK || rec.Header().Get("Content-Type") != "application/gzip" || rec.Body.Len() == 0 {
		t.Fatalf("rotate bundle = %d headers=%v len=%d", rec.Code, rec.Header(), rec.Body.Len())
	}
	audits, err := c.store.ListAudit(t.Context(), store.AuditFilter{Limit: 20})
	if err != nil {
		t.Fatal(err)
	}
	actions := map[string]bool{}
	for _, audit := range audits {
		actions[audit.Action] = true
		if strings.Contains(audit.Detail, "PRIVATE KEY") || strings.Contains(audit.Detail, "inbound-token") {
			t.Fatalf("bundle audit leaked secret: %+v", audit)
		}
	}
	if !actions["inbound_bundle_issue"] || !actions["inbound_bundle_rotate"] {
		t.Fatalf("bundle audit actions = %v", actions)
	}
}

func TestNodeConnectionTestAPIContract(t *testing.T) {
	c, operator, viewer := nodeDisplayNameTestServer(t)
	node := createDisplayNameNode(t, c, "node-test-api", "test-api", "")
	node.Endpoint = "agent.example.com:18444"
	if err := c.store.UpsertNode(t.Context(), node); err != nil {
		t.Fatal(err)
	}
	c.server.SetInboundDialer(&fakeInboundDialer{result: &hub.InboundTestResult{
		NodeID: node.ID, Endpoint: node.Endpoint, TestMode: true,
		Stages: []hub.InboundTestStage{{Name: "dns", OK: true}, {Name: "tcp", OK: true}},
	}})
	rec := nodeDisplayNameDo(t, c, operator, http.MethodPost, "/api/nodes/"+node.ID+"/connection-test", "")
	if rec.Code != http.StatusForbidden {
		t.Fatalf("operator connection test = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, viewer, http.MethodPost, "/api/nodes/"+node.ID+"/connection-test", "")
	if rec.Code != http.StatusForbidden {
		t.Fatalf("viewer connection test = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, "", http.MethodPost, "/api/nodes/"+node.ID+"/connection-test", "")
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous connection test = %d %s", rec.Code, rec.Body.String())
	}
	rec = nodeDisplayNameDo(t, c, c.token, http.MethodPost, "/api/nodes/"+node.ID+"/connection-test", "")
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"test_mode":true`) {
		t.Fatalf("admin connection test = %d %s", rec.Code, rec.Body.String())
	}
}
