package hub

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// upsertTestNode 写入指定状态的测试节点。
func upsertTestNode(t *testing.T, st store.Store, id, status string) *models.Node {
	t.Helper()
	n := &models.Node{
		ID: id, AgentID: "agent-" + id, Hostname: id,
		Status: status, Capabilities: map[string]bool{models.CapScript: true},
	}
	if err := st.UpsertNode(context.Background(), n); err != nil {
		t.Fatalf("upsert node %s: %v", id, err)
	}
	return n
}

// expectNoEnvelope 断言连接未收到任何下发消息。
func expectNoEnvelope(t *testing.T, conn *transferTestConn) {
	t.Helper()
	select {
	case msg := <-conn.sent:
		t.Fatalf("expected dispatch blocked, but got %s", msg.Type)
	default:
	}
}

// TestFileTransferDispatchBlockedByPause 拦截矩阵：maintenance 节点的源上传与目标交付
// 指令均被拦截（状态保持 pending、无消息下发）。
func TestFileTransferDispatchBlockedByPause(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	rm := NewRevisionManager(st)
	nm := NewNodeManager(st, rm)
	sessions := NewSessionManager()
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.Default())
	if err != nil {
		t.Fatal(err)
	}

	upsertTestNode(t, st, "n-pause-src", models.NodeStatusMaintenance)
	srcConn := &transferTestConn{nodeID: "n-pause-src", sent: make(chan protocol.Envelope, 2)}
	sessions.Register(srcConn)
	src := &models.FileTransfer{
		ID: "tr-pause", SourceNodeID: "n-pause-src",
		SourcePath: "/tmp/src.txt", Status: models.FileTransferPending,
	}
	m.dispatchSource(ctx, src)
	expectNoEnvelope(t, srcConn)
	if src.Status != models.FileTransferPending || src.Error != "" {
		t.Fatalf("paused source dispatch must be a no-op, got status=%q error=%q", src.Status, src.Error)
	}

	upsertTestNode(t, st, "n-pause-tgt", models.NodeStatusDisabled)
	tgtConn := &transferTestConn{nodeID: "n-pause-tgt", sent: make(chan protocol.Envelope, 2)}
	sessions.Register(tgtConn)
	tr := &models.FileTransfer{ID: "tr-pause-2", SHA256: "abc", Status: models.FileTransferUploading}
	target := &models.FileTransferTarget{
		TransferID: tr.ID, NodeID: "n-pause-tgt",
		DestinationPath: "/tmp/dst.txt", Status: models.FileTargetPending,
	}
	m.dispatchTarget(ctx, tr, target)
	expectNoEnvelope(t, tgtConn)
	if target.Status != models.FileTargetPending || target.Error != "" {
		t.Fatalf("paused target dispatch must be a no-op, got status=%q error=%q", target.Status, target.Error)
	}
}

// TestFileTransferDispatchProceedsWhenOnline 回归：online 节点传输指令正常放行。
func TestFileTransferDispatchProceedsWhenOnline(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	rm := NewRevisionManager(st)
	nm := NewNodeManager(st, rm)
	sessions := NewSessionManager()
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.Default())
	if err != nil {
		t.Fatal(err)
	}

	upsertTestNode(t, st, "n-online-src", models.NodeStatusOnline)
	conn := &transferTestConn{nodeID: "n-online-src", sent: make(chan protocol.Envelope, 2)}
	sessions.Register(conn)
	src := &models.FileTransfer{
		ID: "tr-online", SourceNodeID: "n-online-src",
		SourcePath: "/tmp/src.txt", Status: models.FileTransferPending,
	}
	if err := st.CreateFileTransfer(ctx, src); err != nil {
		t.Fatal(err)
	}
	m.dispatchSource(ctx, src)
	select {
	case msg := <-conn.sent:
		if msg.Type != protocol.MsgFileUploadRequest {
			t.Fatalf("expected upload request, got %s", msg.Type)
		}
	default:
		t.Fatal("online source dispatch must send upload request")
	}
	if src.Status != models.FileTransferUploading {
		t.Fatalf("online source status = %q, want uploading", src.Status)
	}
}

// TestArtifactPrefetchBlockedByPause 拦截矩阵：maintenance/disabled 节点制品预取被拦。
func TestArtifactPrefetchBlockedByPause(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	sessions := NewSessionManager()
	mgr, err := NewArtifactManager(st, t.TempDir(), sessions, nil)
	if err != nil {
		t.Fatal(err)
	}
	art, err := mgr.Upload(ctx, "pkg", "1.0", "amd64", "pkg.bin", strings.NewReader("payload"))
	if err != nil {
		t.Fatal(err)
	}

	upsertTestNode(t, st, "n-pause-art", models.NodeStatusMaintenance)
	upsertTestNode(t, st, "n-disabled-art", models.NodeStatusDisabled)
	for _, id := range []string{"n-pause-art", "n-disabled-art"} {
		// 会话存在也不能放行：暂停拦截在会话判定之前
		sessions.Register(&transferTestConn{nodeID: id, sent: make(chan protocol.Envelope, 2)})
		if err := mgr.Prefetch(ctx, id, art.ID, "http://hub:8443"); !errors.Is(err, ErrNodePaused) {
			t.Fatalf("prefetch to %s: err = %v, want ErrNodePaused", id, err)
		}
	}

	// online 正常放行
	upsertTestNode(t, st, "n-online-art", models.NodeStatusOnline)
	onlineConn := &transferTestConn{nodeID: "n-online-art", sent: make(chan protocol.Envelope, 2)}
	sessions.Register(onlineConn)
	if err := mgr.Prefetch(ctx, "n-online-art", art.ID, "http://hub:8443"); err != nil {
		t.Fatalf("prefetch to online node: %v", err)
	}
	select {
	case msg := <-onlineConn.sent:
		if msg.Type != protocol.MsgArtifactPrefetch {
			t.Fatalf("expected artifact prefetch, got %s", msg.Type)
		}
	default:
		t.Fatal("online prefetch must send message")
	}
}

// TestChangeNotifyNotBlockedByPause 不拦项：变更通知对暂停节点照常下发（配置同步类不受影响）。
func TestChangeNotifyNotBlockedByPause(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	rm := NewRevisionManager(st)
	sessions := NewSessionManager()
	nm := NewNodeManager(st, rm)
	upsertTestNode(t, st, "n-pause-notify", models.NodeStatusMaintenance)
	conn := &transferTestConn{nodeID: "n-pause-notify", sent: make(chan protocol.Envelope, 1)}
	sessions.Register(conn)

	sync := NewSyncManager(st, rm, sessions, nm)
	sync.NotifyChange(ctx, models.ObjectScript, "script-1", 1, 2)
	select {
	case msg := <-conn.sent:
		if msg.Type != protocol.MsgChangeNotif {
			t.Fatalf("expected change notification, got %s", msg.Type)
		}
	default:
		t.Fatal("change notification must not be blocked for paused node")
	}
}

// pausedScheduleTask 构造指向目标节点的 command 任务。
func pausedScheduleTask(t *testing.T, st store.Store, id string, nodeIDs ...string) *models.Task {
	t.Helper()
	task := &models.Task{
		ID: id, Name: id, Type: models.TaskTypeCommand, Command: "echo hi",
		Target:  models.Target{Type: "node", NodeIDs: nodeIDs},
		Enabled: true,
	}
	if err := st.CreateTask(context.Background(), task); err != nil {
		t.Fatalf("create task: %v", err)
	}
	return task
}

// TestRunScheduledHubPausedNodeCreatesSkipped T6：调度触发暂停节点 → SKIPPED 留痕
// （含目标节点与原因 node paused），同 slot 幂等不重复。
func TestRunScheduledHubPausedNodeCreatesSkipped(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	upsertTestNode(t, st, "n-pause-sched", models.NodeStatusMaintenance)
	task := pausedScheduleTask(t, st, "task-pause", "n-pause-sched")
	schedTime := time.Now().UTC().Truncate(time.Second)

	out, err := em.RunScheduledHub(ctx, task, []string{"n-pause-sched"}, schedTime)
	if err != nil {
		t.Fatalf("paused schedule must not error out, got %v", err)
	}
	if len(out) != 1 {
		t.Fatalf("expected 1 skipped execution, got %d", len(out))
	}
	ex := out[0]
	if ex.Status != models.ExecStatusSkipped {
		t.Fatalf("status = %q, want SKIPPED", ex.Status)
	}
	if ex.NodeID != "n-pause-sched" {
		t.Fatalf("node = %q, want n-pause-sched", ex.NodeID)
	}
	if ex.BlockReason != "node paused" {
		t.Fatalf("reason = %q, want %q", ex.BlockReason, "node paused")
	}
	got, err := st.GetExecution(ctx, ex.ID)
	if err != nil || got.Status != models.ExecStatusSkipped || got.BlockReason != "node paused" {
		t.Fatalf("skipped record not persisted: %+v err=%v", got, err)
	}

	// 同 slot 再次触发：幂等不重复留痕
	if _, err := em.RunScheduledHub(ctx, task, []string{"n-pause-sched"}, schedTime); err != nil {
		t.Fatalf("second fire should be idempotent, got %v", err)
	}
	list, err := st.ListExecutions(ctx, store.ExecutionFilter{NodeID: "n-pause-sched"})
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 1 {
		t.Fatalf("expected exactly 1 skipped record, got %d", len(list))
	}
}

// TestRunScheduledHubDisabledNodeCreatesSkipped disabled（暂停类）同样留痕。
func TestRunScheduledHubDisabledNodeCreatesSkipped(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	upsertTestNode(t, st, "n-disabled-sched", models.NodeStatusDisabled)
	task := pausedScheduleTask(t, st, "task-disabled", "n-disabled-sched")

	out, err := em.RunScheduledHub(ctx, task, []string{"n-disabled-sched"}, time.Now().UTC().Truncate(time.Second))
	if err != nil {
		t.Fatalf("disabled schedule must not error out, got %v", err)
	}
	if len(out) != 1 || out[0].Status != models.ExecStatusSkipped || out[0].BlockReason != "node paused" {
		t.Fatalf("expected SKIPPED record with reason node paused, got %+v", out)
	}
}

// TestRunScheduledHubOfflineNodeKeepsStatusQuo 回归：offline 跳过语义保持现状
// （报非 online 错误、被调度循环吞掉、不产生执行记录）。
func TestRunScheduledHubOfflineNodeKeepsStatusQuo(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	upsertTestNode(t, st, "n-offline-sched", models.NodeStatusOffline)
	task := pausedScheduleTask(t, st, "task-offline", "n-offline-sched")

	_, err := em.RunScheduledHub(ctx, task, []string{"n-offline-sched"}, time.Now().UTC().Truncate(time.Second))
	if err == nil || !strings.Contains(err.Error(), "not online") {
		t.Fatalf("offline schedule should keep failing with not-online error, got %v", err)
	}
	list, err := st.ListExecutions(ctx, store.ExecutionFilter{NodeID: "n-offline-sched"})
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 0 {
		t.Fatalf("offline skip must leave no execution record, got %d", len(list))
	}
}

// TestRunScheduledHubOnlineNodeDispatches 回归：online 节点调度正常下发。
func TestRunScheduledHubOnlineNodeDispatches(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	upsertTestNode(t, st, "n-online-sched", models.NodeStatusOnline)
	conn := &transferTestConn{nodeID: "n-online-sched", sent: make(chan protocol.Envelope, 2)}
	em.sessions.Register(conn)
	task := pausedScheduleTask(t, st, "task-online", "n-online-sched")

	out, err := em.RunScheduledHub(ctx, task, []string{"n-online-sched"}, time.Now().UTC().Truncate(time.Second))
	if err != nil {
		t.Fatalf("online schedule dispatch: %v", err)
	}
	if len(out) != 1 || out[0].Status != models.ExecStatusPending {
		t.Fatalf("expected pending dispatched execution, got %+v", out)
	}
	select {
	case msg := <-conn.sent:
		if msg.Type != protocol.MsgRunExecution {
			t.Fatalf("expected run execution, got %s", msg.Type)
		}
	default:
		t.Fatal("online schedule must dispatch to agent")
	}
}

// TestRunManualPausedNodeRejected 手动执行对暂停节点保持原 online 门禁错误，不产生 SKIPPED。
func TestRunManualPausedNodeRejected(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	upsertTestNode(t, st, "n-pause-manual", models.NodeStatusMaintenance)
	task := pausedScheduleTask(t, st, "task-manual", "n-pause-manual")

	_, err := em.RunManual(ctx, task, []string{"n-pause-manual"}, nil)
	if err == nil || !strings.Contains(err.Error(), "not online") {
		t.Fatalf("manual run on paused node should fail with not-online, got %v", err)
	}
	list, err := st.ListExecutions(ctx, store.ExecutionFilter{NodeID: "n-pause-manual"})
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 0 {
		t.Fatalf("manual rejection must not create SKIPPED record, got %d", len(list))
	}
}

// TestRunDueHUBPausedNodeCreatesSkipped 端到端：Hub owner 调度循环触发暂停节点 →
// 产生 SKIPPED 记录（此前被静默吞掉）。
func TestRunDueHUBPausedNodeCreatesSkipped(t *testing.T) {
	ctx := context.Background()
	st := newTestStore(t)
	revisions := NewRevisionManager(st)
	sessions := NewSessionManager()
	nm := NewNodeManager(st, revisions)
	sync := NewSyncManager(st, revisions, sessions, nm)
	em := NewExecutionManager(st, sessions, nm, slog.New(slog.NewTextHandler(io.Discard, nil)))
	sm := NewScheduleManager(st, revisions, sync, nm, em)

	upsertTestNode(t, st, "n-pause-hub", models.NodeStatusMaintenance)
	task := pausedScheduleTask(t, st, "task-hub-sched", "n-pause-hub")
	sched := &models.Schedule{
		TaskID: task.ID, Type: models.ScheduleTypeInterval, IntervalSec: 60,
		Timezone: "UTC", ExecutionOwner: models.ExecutionOwnerHub,
		Enabled: true, CreatedAt: time.Now().Add(-2 * time.Hour),
	}
	if err := sm.Create(ctx, sched); err != nil {
		t.Fatalf("create schedule: %v", err)
	}

	// Create 会覆盖 CreatedAt=now，因此用未来时刻推进到期（interval 60s）
	if err := sm.RunDueHUB(ctx, time.Now().Add(2*time.Hour)); err != nil {
		t.Fatalf("RunDueHUB: %v", err)
	}
	list, err := st.ListExecutions(ctx, store.ExecutionFilter{NodeID: "n-pause-hub"})
	if err != nil {
		t.Fatal(err)
	}
	if len(list) != 1 {
		t.Fatalf("expected 1 SKIPPED record from hub schedule, got %d", len(list))
	}
	ex := list[0]
	if ex.Status != models.ExecStatusSkipped || ex.BlockReason != "node paused" || ex.TriggerType != models.TriggerSchedule {
		t.Fatalf("unexpected skipped record: %+v", ex)
	}
}
