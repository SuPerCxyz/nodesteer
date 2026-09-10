package hub

import (
	"context"
	"testing"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/store"
)

// TestResolveTargetLabelGroup 验证 Bug G：label 分组成员为动态解析，
// 作为 group 目标时应命中带对应 label 的节点。
func TestResolveTargetLabelGroup(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	nm := NewNodeManager(st, NewRevisionManager(st))

	nodeA, _, _, err := nm.RegisterOrUpdate(ctx, "agent-a", "host-a", "10.0.0.1", "linux", "amd64", "1.0", "native", false, map[string]bool{"host_filesystem": true})
	if err != nil {
		t.Fatalf("register a: %v", err)
	}
	nodeB, _, _, err := nm.RegisterOrUpdate(ctx, "agent-b", "host-b", "10.0.0.2", "linux", "amd64", "1.0", "native", false, map[string]bool{"host_filesystem": true})
	if err != nil {
		t.Fatalf("register b: %v", err)
	}
	if err := nm.SetLabels(ctx, nodeA.ID, map[string]string{"env": "prod"}); err != nil {
		t.Fatalf("set labels a: %v", err)
	}
	if err := nm.SetLabels(ctx, nodeB.ID, map[string]string{"env": "dev"}); err != nil {
		t.Fatalf("set labels b: %v", err)
	}

	grp := &models.Group{Name: "prod-group", Type: "label", LabelKey: "env", LabelValue: "prod"}
	if err := st.CreateGroup(ctx, grp); err != nil {
		t.Fatalf("create group: %v", err)
	}

	got, err := nm.ResolveTarget(ctx, models.Target{Type: "group", GroupIDs: []string{grp.ID}})
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	if len(got) != 1 || got[0] != nodeA.ID {
		t.Fatalf("expect label group resolve to node A (%s), got %v", nodeA.ID, got)
	}
}

func TestTargetMutationAdvancesRevision(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	rm := NewRevisionManager(st)
	nm := NewNodeManager(st, rm)
	sm := NewSyncManager(st, rm, NewSessionManager(), nm)
	nm.SetSyncManager(sm)
	n := &models.Node{ID: "node-1", AgentID: "agent-1", Hostname: "host", Status: models.NodeStatusOnline}
	if err := st.UpsertNode(ctx, n); err != nil {
		t.Fatal(err)
	}
	if err := nm.SetLabels(ctx, n.ID, map[string]string{"env": "prod"}); err != nil {
		t.Fatal(err)
	}
	group := &models.Group{Name: "prod", Type: "label", LabelKey: "env", LabelValue: "prod"}
	if err := nm.CreateGroup(ctx, group); err != nil {
		t.Fatal(err)
	}
	if rev, err := rm.Current(ctx); err != nil || rev != 2 {
		t.Fatalf("expected two target revisions, got rev=%d err=%v", rev, err)
	}
	changes, err := st.GetChangesSince(ctx, 0)
	if err != nil || len(changes) != 2 {
		t.Fatalf("expected two target changes, got %d err=%v", len(changes), err)
	}
}

func TestPreparedEnrollmentReusesNodeOnFirstHello(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))

	pending, err := nm.PrepareEnrollment(ctx, "pending-node", "10.0.0.10", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("prepare enrollment: %v", err)
	}
	if pending.Status != models.NodeStatusOffline || pending.AgentID == "" ||
		pending.AgentVersion != "" || pending.Arch != "" || pending.DeploymentMode != "" {
		t.Fatalf("unexpected pending node: %+v", pending)
	}

	registered, credential, isNew, err := nm.RegisterOrUpdate(ctx, pending.AgentID, "real-node", "10.0.0.10", "linux", "amd64", "1.0", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatalf("register prepared node: %v", err)
	}
	if isNew || registered.ID != pending.ID || registered.Status != models.NodeStatusOnline || credential == "" {
		t.Fatalf("prepared node was not reused: node=%+v isNew=%t credential=%q", registered, isNew, credential)
	}
	nodes, err := nm.ListNodes(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(nodes) != 1 {
		t.Fatalf("expected one node after first hello, got %d", len(nodes))
	}
}

func TestHeartbeatRestoresOfflineButPreservesMaintenance(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))
	n := &models.Node{ID: "node-heartbeat", AgentID: "agent-heartbeat", Hostname: "host", Status: models.NodeStatusOffline}
	if err := st.UpsertNode(ctx, n); err != nil {
		t.Fatal(err)
	}

	if err := nm.UpdateHeartbeat(ctx, n.ID, time.Now()); err != nil {
		t.Fatal(err)
	}
	got, err := nm.GetNode(ctx, n.ID)
	if err != nil || got.Status != models.NodeStatusOnline {
		t.Fatalf("heartbeat should restore offline node, err=%v node=%+v", err, got)
	}
	if err := nm.SetNodeStatus(ctx, n.ID, models.NodeStatusMaintenance); err != nil {
		t.Fatal(err)
	}
	if err := nm.UpdateHeartbeat(ctx, n.ID, time.Now()); err != nil {
		t.Fatal(err)
	}
	got, err = nm.GetNode(ctx, n.ID)
	if err != nil || got.Status != models.NodeStatusMaintenance {
		t.Fatalf("heartbeat should preserve maintenance, err=%v node=%+v", err, got)
	}
}
