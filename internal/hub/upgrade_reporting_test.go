package hub

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// ---------- 缺口1：MarkFinished 实时回报持久化失败原因 ----------

// TestMarkFinishedPersistsAgentFailureReason Hub 创建的升级记录经实时回报路径收到
// FAILED 时，终态必须含 Agent 回报的失败原因（sha256 校验失败 / 重启回滚结果，
// 均只携带在 ExecutionFinishedPayload.BlockReason）。修复前更新分支丢弃该字段，
// 执行历史 block_reason 为空，违反 specs/agent/core「升级失败回滚」scenario。
func TestMarkFinishedPersistsAgentFailureReason(t *testing.T) {
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	ctx := context.Background()

	cases := []struct {
		name   string
		execID string
		reason string
	}{
		{name: "sha256 失败原因", execID: "upg-reason-sha", reason: "download agent binary: sha256 mismatch"},
		{name: "重启回滚结果", execID: "upg-reason-restart", reason: "restart agent service: unit failed; rollback restored previous binary"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			// Hub 创建的在途升级记录（模拟 UpgradeNodes 下发后 agent 回报前的状态）
			ex := &models.Execution{
				ID: tc.execID, TaskID: models.AgentUpgradeTaskID, NodeID: "n-upg-reason",
				TriggerType: models.TriggerManual, Status: models.ExecStatusRunning,
				StartTime: time.Now(),
			}
			if err := st.CreateExecution(ctx, ex); err != nil {
				t.Fatalf("create execution: %v", err)
			}
			// Agent 实时回报 FAILED：原因仅在 BlockReason，Stdout/Stderr 为空
			if err := em.MarkFinished(ctx, "n-upg-reason", protocol.ExecutionFinishedPayload{
				ExecutionID: tc.execID, TaskID: models.AgentUpgradeTaskID, NodeID: "n-upg-reason",
				Status: models.ExecStatusFailed, ExitCode: -1, BlockReason: tc.reason,
				EndTime: time.Now().Format(time.RFC3339Nano),
			}); err != nil {
				t.Fatalf("mark finished: %v", err)
			}
			got, err := st.GetExecution(ctx, tc.execID)
			if err != nil {
				t.Fatalf("get execution: %v", err)
			}
			if got.Status != models.ExecStatusFailed {
				t.Fatalf("status = %q, want FAILED", got.Status)
			}
			if got.BlockReason != tc.reason {
				t.Fatalf("block reason = %q, want %q（Agent 回报的失败原因必须持久化）", got.BlockReason, tc.reason)
			}
		})
	}
}

// ---------- 缺口2：credential HELLO 刷新节点元数据 ----------

// credentialHello 在 credential 认证路径发送 HELLO 并返回 accepted ack。
func credentialHello(t *testing.T, g *Gateway, payload protocol.HelloPayload) protocol.HelloAckPayload {
	t.Helper()
	payload.ProtocolVersion = protocol.ProtocolVersion
	conn := newHelloTestConn(t, g)
	if err := g.handleHello(t.Context(), conn, protocol.NewEnvelope(protocol.MsgHello, "r-cred", payload)); err != nil {
		t.Fatalf("expected accepted credential HELLO, got %v", err)
	}
	return drainHelloAck(t, conn)
}

// TestCredentialHelloRefreshesVersionAndCapabilities 升级场景端到端：Agent 以
// credential 重连（升级重启后的实际路径）上报新 agent_version 与含
// agent_upgrade 的能力集 → 节点记录与 JSON 序列化值同步更新。
// 修复前该路径只写心跳，节点详情滞留旧版本。
func TestCredentialHelloRefreshesVersionAndCapabilities(t *testing.T) {
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)
	node, cred, _, err := nm.RegisterOrUpdate(ctx, "agent-upg-e2e", "host-upg-e2e", "10.0.0.99",
		"linux", "amd64", "v1.0.0", models.DeploymentModeNative, false,
		map[string]bool{models.CapScript: true})
	if err != nil {
		t.Fatalf("register: %v", err)
	}

	ack := credentialHello(t, g, protocol.HelloPayload{
		AgentID:         node.AgentID,
		AgentCredential: cred,
		Hostname:        "host-upg-e2e",
		AgentVersion:    "v2.0.0",
		DeploymentMode:  models.DeploymentModeNative,
		Capabilities: map[string]bool{
			models.CapScript:       true,
			models.CapAgentUpgrade: true,
		},
	})
	if ack.NodeID != node.ID {
		t.Fatalf("ack node = %q, want %q", ack.NodeID, node.ID)
	}

	got, err := nm.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.AgentVersion != "v2.0.0" {
		t.Fatalf("agent_version = %q, want v2.0.0（升级重连后必须同步新版本）", got.AgentVersion)
	}
	if !got.Capabilities[models.CapAgentUpgrade] {
		t.Fatalf("capabilities must report agent_upgrade after reconnect, got %+v", got.Capabilities)
	}
	// GET /api/nodes 直接序列化 models.Node：JSON 值同样必须是新版本
	b, err := json.Marshal(got)
	if err != nil {
		t.Fatalf("marshal node: %v", err)
	}
	if !strings.Contains(string(b), `"agent_version":"v2.0.0"`) {
		t.Fatalf("serialized node must carry new agent_version, got %s", b)
	}
}

// TestCredentialHelloKeepsMetadataWhenPayloadEmpty 旧 Agent/缺省载荷回归：
// credential HELLO 未携带 version/capabilities/deployment_mode 时保留既有值，
// docker 形态不得被误翻转为 native。
func TestCredentialHelloKeepsMetadataWhenPayloadEmpty(t *testing.T) {
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)
	caps := map[string]bool{models.CapScript: true}
	node, cred, _, err := nm.RegisterOrUpdate(ctx, "agent-empty-payload", "host-empty", "10.0.0.98",
		"linux", "amd64", "v1.0.0", models.DeploymentModeDocker, false, caps)
	if err != nil {
		t.Fatalf("register: %v", err)
	}

	credentialHello(t, g, protocol.HelloPayload{
		AgentID:         node.AgentID,
		AgentCredential: cred,
	})

	got, err := nm.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.AgentVersion != "v1.0.0" {
		t.Fatalf("agent_version = %q, want preserved v1.0.0", got.AgentVersion)
	}
	if got.DeploymentMode != models.DeploymentModeDocker {
		t.Fatalf("deployment_mode = %q, want preserved docker", got.DeploymentMode)
	}
	if !got.Capabilities[models.CapScript] || len(got.Capabilities) != 1 {
		t.Fatalf("capabilities = %+v, want preserved {script:true}", got.Capabilities)
	}
}

// TestCredentialHelloKeepsMaintenanceStatusWhileRefreshing m/d 状态不被触碰：
// maintenance 节点 credential HELLO 携新元数据 → 状态保持 maintenance、元数据已刷新；
// 非法 deployment_mode 经白名单归一化为 native（与注册路径同一语义）。
func TestCredentialHelloKeepsMaintenanceStatusWhileRefreshing(t *testing.T) {
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)
	node, cred, _, err := nm.RegisterOrUpdate(ctx, "agent-maint-meta", "host-maint-meta", "10.0.0.97",
		"linux", "amd64", "v1.0.0", models.DeploymentModeNative, false,
		map[string]bool{models.CapScript: true})
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	if err := nm.SetNodeStatus(ctx, node.ID, models.NodeStatusMaintenance); err != nil {
		t.Fatalf("set maintenance: %v", err)
	}

	credentialHello(t, g, protocol.HelloPayload{
		AgentID:         node.AgentID,
		AgentCredential: cred,
		AgentVersion:    "v2.1.0",
		DeploymentMode:  "vmware", // 非法枚举 → 归一化 native
		Capabilities:    map[string]bool{models.CapScript: true, models.CapAgentUpgrade: true},
	})

	got, err := nm.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if got.Status != models.NodeStatusMaintenance {
		t.Fatalf("status = %q, want maintenance（元数据刷新不得触碰状态）", got.Status)
	}
	if got.AgentVersion != "v2.1.0" {
		t.Fatalf("agent_version = %q, want v2.1.0", got.AgentVersion)
	}
	if got.DeploymentMode != models.DeploymentModeNative {
		t.Fatalf("deployment_mode = %q, want normalized native", got.DeploymentMode)
	}
	if !got.Capabilities[models.CapAgentUpgrade] {
		t.Fatalf("capabilities must be refreshed, got %+v", got.Capabilities)
	}
}
