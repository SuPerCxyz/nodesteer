package hub

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// newUpgradeTestEnv 构造带会话管理器的执行管理器（可注册假连接验证下发）。
func newUpgradeTestEnv(t *testing.T) (store.Store, *ExecutionManager, *SessionManager) {
	t.Helper()
	st := newTestStore(t)
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	sessions := NewSessionManager()
	rm := NewRevisionManager(st)
	em := NewExecutionManager(st, sessions, NewNodeManager(st, rm), logger)
	return st, em, sessions
}

// upsertUpgradeTestNode 写入指定部署形态/状态/架构的测试节点。
func upsertUpgradeTestNode(t *testing.T, st store.Store, id, mode, status, arch string) {
	t.Helper()
	n := &models.Node{
		ID: id, AgentID: "agent-" + id, Hostname: id,
		DeploymentMode: mode, Status: status, Arch: arch,
		Capabilities: map[string]bool{models.CapScript: true, models.CapAgentUpgrade: mode == models.DeploymentModeNative},
	}
	if err := st.UpsertNode(context.Background(), n); err != nil {
		t.Fatalf("upsert node %s: %v", id, err)
	}
}

// TestUpgradeNodesDispatchesThroughRunExecution 链路：native online 节点创建 PENDING
// 记录并经既有 RUN_EXECUTION 链下发（类型/系统 Task 标识/下载地址正确）；
// 重复触发同节点在途升级被去重（返回既有记录、不重复下发）。
func TestUpgradeNodesDispatchesThroughRunExecution(t *testing.T) {
	ctx := context.Background()
	st, em, sessions := newUpgradeTestEnv(t)
	upsertUpgradeTestNode(t, st, "n-up-native", models.DeploymentModeNative, models.NodeStatusOnline, "amd64")
	conn := &transferTestConn{nodeID: "n-up-native", sent: make(chan protocol.Envelope, 4)}
	sessions.Register(conn)

	results, err := em.UpgradeNodes(ctx, []string{"n-up-native"}, "http://hub:8443")
	if err != nil {
		t.Fatalf("UpgradeNodes: %v", err)
	}
	if len(results) != 1 {
		t.Fatalf("results = %d, want 1", len(results))
	}
	got := results[0]
	if !got.Dispatched || got.Deduplicated || got.Status != models.ExecStatusPending || got.ExecutionID == "" {
		t.Fatalf("unexpected result: %+v", got)
	}

	select {
	case msg := <-conn.sent:
		if msg.Type != protocol.MsgRunExecution {
			t.Fatalf("msg type = %s, want %s", msg.Type, protocol.MsgRunExecution)
		}
		var p protocol.RunExecutionPayload
		if err := json.Unmarshal(msg.Payload, &p); err != nil {
			t.Fatalf("unmarshal payload: %v", err)
		}
		if p.Type != models.TaskTypeAgentUpgrade {
			t.Fatalf("payload type = %q, want agent_upgrade", p.Type)
		}
		if p.TaskID != models.AgentUpgradeTaskID {
			t.Fatalf("payload task id = %q, want %q", p.TaskID, models.AgentUpgradeTaskID)
		}
		if p.ExecutionID != got.ExecutionID {
			t.Fatalf("payload execution id = %q, want %q", p.ExecutionID, got.ExecutionID)
		}
		wantURL := "http://hub:8443/api/agent/binary?architecture=amd64"
		if p.DownloadURL != wantURL {
			t.Fatalf("download url = %q, want %q", p.DownloadURL, wantURL)
		}
	default:
		t.Fatal("online native node must receive RUN_EXECUTION dispatch")
	}

	// 执行历史可见该记录（task_id == 系统标识）
	ex, err := st.GetExecution(ctx, got.ExecutionID)
	if err != nil || ex.TaskID != models.AgentUpgradeTaskID || ex.Status != models.ExecStatusPending {
		t.Fatalf("execution record wrong: %+v err=%v", ex, err)
	}

	// 幂等：在途升级重复触发 → 拒绝重复下发，返回既有记录
	again, err := em.UpgradeNodes(ctx, []string{"n-up-native"}, "http://hub:8443")
	if err != nil {
		t.Fatalf("second UpgradeNodes: %v", err)
	}
	if len(again) != 1 || !again[0].Deduplicated || again[0].ExecutionID != got.ExecutionID {
		t.Fatalf("dedup result wrong: %+v", again)
	}
	select {
	case msg := <-conn.sent:
		t.Fatalf("duplicate trigger must not dispatch again, got %s", msg.Type)
	default:
	}
}

// TestUpgradeNodesFiltersUnsupportedAndUnreachable 过滤矩阵：
// docker 形态 → SKIPPED 不支持终态且不下发（即使有会话）；
// 暂停节点 → SKIPPED node paused 不下发；离线节点 → FAILED 终态不下发。
func TestUpgradeNodesFiltersUnsupportedAndUnreachable(t *testing.T) {
	ctx := context.Background()
	st, em, sessions := newUpgradeTestEnv(t)
	upsertUpgradeTestNode(t, st, "n-up-docker", models.DeploymentModeDocker, models.NodeStatusOnline, "amd64")
	upsertUpgradeTestNode(t, st, "n-up-paused", models.DeploymentModeNative, models.NodeStatusMaintenance, "amd64")
	upsertUpgradeTestNode(t, st, "n-up-offline", models.DeploymentModeNative, models.NodeStatusOffline, "amd64")
	dockerConn := &transferTestConn{nodeID: "n-up-docker", sent: make(chan protocol.Envelope, 2)}
	sessions.Register(dockerConn)

	results, err := em.UpgradeNodes(ctx, []string{"n-up-docker", "n-up-paused", "n-up-offline"}, "http://hub:8443")
	if err != nil {
		t.Fatalf("UpgradeNodes: %v", err)
	}
	if len(results) != 3 {
		t.Fatalf("results = %d, want 3（逐节点独立）", len(results))
	}

	docker := results[0]
	if docker.Dispatched || docker.Status != models.ExecStatusSkipped ||
		!strings.Contains(docker.BlockReason, "deployment mode docker") {
		t.Fatalf("docker result wrong: %+v", docker)
	}
	paused := results[1]
	if paused.Dispatched || paused.Status != models.ExecStatusSkipped || paused.BlockReason != reasonNodePaused {
		t.Fatalf("paused result wrong: %+v", paused)
	}
	offline := results[2]
	if offline.Dispatched || offline.Status != models.ExecStatusFailed ||
		!strings.Contains(offline.BlockReason, "not online") {
		t.Fatalf("offline result wrong: %+v", offline)
	}
	expectNoEnvelope(t, dockerConn)

	// 各自独立终态记录落库
	for _, id := range []string{docker.ExecutionID, paused.ExecutionID, offline.ExecutionID} {
		ex, err := st.GetExecution(ctx, id)
		if err != nil {
			t.Fatalf("terminal record missing: %v", err)
		}
		if ex.TaskID != models.AgentUpgradeTaskID || ex.BlockReason == "" {
			t.Fatalf("terminal record wrong: %+v", ex)
		}
	}
}

// TestUpgradeNodesDispatchFailureBecomesTerminal 下发失败（有会话前/发送失败）
// → 记录就地标 FAILED 终态、原因可见，且不中断同批其余节点。
func TestUpgradeNodesDispatchFailureBecomesTerminal(t *testing.T) {
	ctx := context.Background()
	st, em, _ := newUpgradeTestEnv(t)
	// online 但没有注册会话：dispatch 阶段失败
	upsertUpgradeTestNode(t, st, "n-up-nosession", models.DeploymentModeNative, models.NodeStatusOnline, "amd64")

	results, err := em.UpgradeNodes(ctx, []string{"n-up-nosession"}, "http://hub:8443")
	if err != nil {
		t.Fatalf("UpgradeNodes: %v", err)
	}
	if len(results) != 1 {
		t.Fatalf("results = %d, want 1", len(results))
	}
	got := results[0]
	if got.Dispatched || got.Status != models.ExecStatusFailed || !strings.Contains(got.BlockReason, "offline") {
		t.Fatalf("dispatch failure result wrong: %+v", got)
	}
	ex, err := st.GetExecution(ctx, got.ExecutionID)
	if err != nil || ex.Status != models.ExecStatusFailed || ex.BlockReason == "" {
		t.Fatalf("failed record wrong: %+v err=%v", ex, err)
	}
}
