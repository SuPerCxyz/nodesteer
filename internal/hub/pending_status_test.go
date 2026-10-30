package hub

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// TestPendingNodeNotExecutableByTask 锁定波及面：仅 online 可执行，
// pending（未完成会话）节点不得被手动/调度任务执行。
func TestPendingNodeNotExecutableByTask(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	em := newTestExecMgr(t, st)

	n := &models.Node{ID: "n-pending", AgentID: "a-pending", Hostname: "host-pending",
		Status: models.NodeStatusPending, Capabilities: map[string]bool{"script": true}}
	if err := st.UpsertNode(ctx, n); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	task := &models.Task{ID: "t-pending", Name: "t-pending", Type: "command", Command: "echo", Enabled: true}
	_, err := em.RunManual(ctx, task, []string{n.ID}, nil, ExecutionStrategy{}, nil)
	if err == nil || !strings.Contains(err.Error(), "not online (status=pending)") {
		t.Fatalf("pending node must not be executable, got %v", err)
	}
}

// TestPendingNodeNotExecutableByApplication 锁定波及面：应用部署同样仅接受 online 节点。
func TestPendingNodeNotExecutableByApplication(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)

	n := &models.Node{ID: "n-pending-app", AgentID: "a-pending-app", Hostname: "host-pending-app",
		Status: models.NodeStatusPending, Capabilities: map[string]bool{models.CapApplicationDeploy: true}}
	if err := st.UpsertNode(ctx, n); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	app := &models.Application{ID: "app-pending", Name: "app-pending"}
	if err := st.CreateApplication(ctx, app); err != nil {
		t.Fatalf("create application: %v", err)
	}
	am := NewAppManager(st, NewRevisionManager(st), NewSessionManager(), nil, nil, "")
	_, err := am.Deploy(ctx, app.ID, []string{n.ID}, "deploy")
	if err == nil || !strings.Contains(err.Error(), "not online (status=pending)") {
		t.Fatalf("pending node must not accept application deploy, got %v", err)
	}
}

// TestRemoteOnlineConditionAcceptsPendingValue 锁定远端条件枚举判定：
// remote "online" 条件是观测状态比较集（Hub 上报状态 vs 任务期望值），
// pending 作为合法观测值必须可写；拼写错误仍被拒绝。
func TestRemoteOnlineConditionAcceptsPendingValue(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	n := &models.Node{ID: "n-cond", AgentID: "a-cond", Hostname: "host-cond", Status: models.NodeStatusPending}
	if err := st.UpsertNode(ctx, n); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	sch := &models.Schedule{TaskID: "t-cond", Type: models.ScheduleTypeManual,
		Target:         models.Target{Type: "label", LabelKey: "env"},
		Timezone:       "UTC",
		ExecutionOwner: models.ExecutionOwnerHub,
		OfflinePolicy:  models.OfflinePolicyAllowOffline,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		Condition: &models.Condition{Type: "remote", Remote: &models.RemoteCondition{
			NodeID: n.ID, Property: "online", Operator: "==", Value: "pending"}}}
	if err := validateSchedule(ctx, st, sch, time.Time{}); err != nil {
		t.Fatalf("pending should be a legal remote online condition value: %v", err)
	}
	sch.Condition.Remote.Value = "bogus"
	if err := validateSchedule(ctx, st, sch, time.Time{}); err == nil || !strings.Contains(err.Error(), "invalid online condition value") {
		t.Fatalf("invalid remote online condition value must still be rejected, got %v", err)
	}
}

// TestResolveRemoteStateReportsPending 锁定远端状态解析：pending 节点的
// "online" 属性如实上报 pending，不会被误判为 online。
func TestResolveRemoteStateReportsPending(t *testing.T) {
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)
	node, err := nm.PrepareEnrollment(ctx, "pending-remote", "10.0.0.86", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("prepare enrollment: %v", err)
	}
	value, _, _, _ := g.resolveRemoteState(ctx, protocol.RemoteStateReqPayload{
		TargetNodeID: node.ID,
		Property:     "online",
	})
	if value != models.NodeStatusPending {
		t.Fatalf("pending node must resolve as %q, got %q", models.NodeStatusPending, value)
	}
	if value == models.NodeStatusOnline {
		t.Fatal("pending node must not be reported as online")
	}
}
