package hub

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/coder/websocket"
)

// deliveryFixture 构造“源已暂存、目标停在 DELIVERING”的传输现场。
func deliveryFixture(t *testing.T) (*FileTransferManager, *NodeManager, *models.Node, *models.Node, *models.FileTransfer) {
	t.Helper()
	ctx := t.Context()
	st, err := store.OpenSQLite(t.TempDir() + "/hub.db")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	nm := NewNodeManager(st, NewRevisionManager(st))
	source, sourceCred, _, err := nm.RegisterOrUpdate(ctx, "source-agent", "source", "10.0.0.1", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	target, _, _, err := nm.RegisterOrUpdate(ctx, "target-agent", "target", "10.0.0.2", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionManager()
	sourceConn := &transferTestConn{nodeID: source.ID, sent: make(chan protocol.Envelope, 4)}
	targetConn := &transferTestConn{nodeID: target.ID, sent: make(chan protocol.Envelope, 4)}
	sessions.Register(sourceConn)
	sessions.Register(targetConn)
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.New(slog.DiscardHandler))
	if err != nil {
		t.Fatal(err)
	}
	item, err := m.Create(ctx, source.ID, "/var/lib/nodesteer/source.txt", []FileTransferTargetRequest{{NodeID: target.ID, DestinationPath: "/var/lib/nodesteer/target.txt"}})
	if err != nil {
		t.Fatal(err)
	}
	<-sourceConn.sent
	body := []byte("nodesteer-transfer")
	req := httptest.NewRequest(http.MethodPost, "/agent/transfers/"+item.ID+"/upload", io.NopCloser(bytesReader(body)))
	req.Header.Set("X-NodeSteer-Agent-ID", source.AgentID)
	req.Header.Set("X-NodeSteer-Agent-Token", sourceCred)
	req.Header.Set("X-NodeSteer-File-Size", strconv.Itoa(len(body)))
	req.ContentLength = int64(len(body))
	rec := httptest.NewRecorder()
	m.HandleAgentHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("upload status %d: %s", rec.Code, rec.Body.String())
	}
	msg := <-targetConn.sent
	if msg.Type != protocol.MsgFileDeliveryRequest {
		t.Fatalf("expected delivery request, got %s", msg.Type)
	}
	item, err = m.Get(ctx, item.ID)
	if err != nil {
		t.Fatal(err)
	}
	if item.Status != models.FileTransferDelivering || targetStatus(item, target.ID) != models.FileTargetDelivering {
		t.Fatalf("fixture must leave the transfer DELIVERING: %+v", item)
	}
	return m, nm, source, target, item
}

// TestDeliveryStallTimeoutTerminalizesTargetAndReleasesNode 目标停在 DELIVERING 且超过
// 交付时限（Hub 写失败/指令丢失，目标永不回报）时，必须终结为 FAILED 并让传输回到终态，
// 从而释放被 409 has active file transfer 永久阻断的节点删除
// （缺陷 E2E-20261017-XFER-REMOTE-TARGET-STUCK-DELIVERING）。
func TestDeliveryStallTimeoutTerminalizesTargetAndReleasesNode(t *testing.T) {
	ctx := t.Context()
	m, nm, source, target, item := deliveryFixture(t)

	// 边界：未到时限不得提前终结。
	before, err := m.SweepStalledDeliveries(ctx, time.Now().Add(defaultDeliveryStallTimeout-time.Minute))
	if err != nil || before != 0 {
		t.Fatalf("sweep before deadline = %d err=%v, want 0", before, err)
	}
	if got := targetStatus(mustGet(t, m, item.ID), target.ID); got != models.FileTargetDelivering {
		t.Fatalf("target status before deadline = %s, want DELIVERING", got)
	}
	if err := nm.Delete(ctx, target.ID); err == nil || !strings.Contains(err.Error(), "active file transfer") {
		t.Fatalf("node deletion must stay blocked while delivering: %v", err)
	}

	// 越过时限：目标 FAILED、传输 FAILED、错误可定位。
	deadline := time.Now().Add(deliveryStallTimeout(item.Size) + time.Minute)
	stalled, err := m.SweepStalledDeliveries(ctx, deadline)
	if err != nil || stalled != 1 {
		t.Fatalf("sweep after deadline = %d err=%v, want 1", stalled, err)
	}
	state := mustGet(t, m, item.ID)
	if state.Status != models.FileTransferFailed {
		t.Fatalf("transfer status = %s, want FAILED", state.Status)
	}
	if got := targetStatus(state, target.ID); got != models.FileTargetFailed {
		t.Fatalf("target status = %s, want FAILED", got)
	}
	message := targetError(state, target.ID)
	if !strings.Contains(message, "delivery timed out") || !strings.Contains(message, "target agent") {
		t.Fatalf("target error = %q, want locatable delivery timeout", message)
	}

	// 幂等：再次扫描不改变终态。
	if again, err := m.SweepStalledDeliveries(ctx, deadline.Add(time.Hour)); err != nil || again != 0 {
		t.Fatalf("second sweep = %d err=%v, want 0", again, err)
	}

	// 终态后源/目标节点均可删除（409 释放）。
	if err := nm.Delete(ctx, target.ID); err != nil {
		t.Fatalf("delete target node after timeout: %v", err)
	}
	if err := nm.Delete(ctx, source.ID); err != nil {
		t.Fatalf("delete source node after timeout: %v", err)
	}
}

// TestDeliveryStallSweepLeavesPendingTargetsAlone PENDING 表示“等源暂存/等节点重连”，
// 属正常可恢复状态，不得被交付看门狗终结。
func TestDeliveryStallSweepLeavesPendingTargetsAlone(t *testing.T) {
	ctx := t.Context()
	m, _, source, target, _ := deliveryFixture(t)
	// 新建一个只有 PENDING 目标的传输（源尚未上传）。
	pending, err := m.Create(ctx, source.ID, "/var/lib/nodesteer/other.txt", []FileTransferTargetRequest{{NodeID: target.ID, DestinationPath: "/var/lib/nodesteer/other-target.txt"}})
	if err != nil {
		t.Fatal(err)
	}
	if got := targetStatus(pending, target.ID); got != models.FileTargetPending {
		t.Fatalf("fixture target status = %s, want PENDING", got)
	}
	// 只有第一个传输的 DELIVERING 目标会被终结；PENDING 目标必须原样保留。
	if _, err := m.SweepStalledDeliveries(ctx, time.Now().Add(24*time.Hour)); err != nil {
		t.Fatalf("sweep: %v", err)
	}
	state := mustGet(t, m, pending.ID)
	if got := targetStatus(state, target.ID); got != models.FileTargetPending {
		t.Fatalf("PENDING target was terminalized: %s", got)
	}
	if state.Status == models.FileTransferFailed || state.Status == models.FileTransferSuccess {
		t.Fatalf("waiting-for-upload transfer terminalized: %+v", state)
	}
}

// TestDeliveryStallTimeoutScalesWithSize 阈值 = 基线 + 按 1 MiB/s 折算的下载时间。
func TestDeliveryStallTimeoutScalesWithSize(t *testing.T) {
	cases := []struct {
		size int64
		want time.Duration
	}{
		{size: 0, want: defaultDeliveryStallTimeout},
		{size: 512, want: defaultDeliveryStallTimeout},
		{size: 1 << 20, want: defaultDeliveryStallTimeout + time.Second},
		{size: 10 << 30, want: defaultDeliveryStallTimeout + 10240*time.Second},
	}
	for _, tc := range cases {
		if got := deliveryStallTimeout(tc.size); got != tc.want {
			t.Fatalf("deliveryStallTimeout(%d) = %s, want %s", tc.size, got, tc.want)
		}
	}
}

// TestWriteLoopMarksConnUnavailableOnWriteFailure 写失败必须可观测并让上层立刻知道
// 通道不可用：此前 writeLoop 静默 return，send() 仍返回成功（消息进缓冲区但永不送达），
// 缓冲区满后发送方永久阻塞。
func TestWriteLoopMarksConnUnavailableOnWriteFailure(t *testing.T) {
	// 服务端接受后即失去对端：后续写入必然失败。
	accepted := make(chan *websocket.Conn, 1)
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		accepted <- c
	}))
	defer ts.Close()
	wsURL := "ws://" + strings.TrimPrefix(ts.URL, "http://")
	client, _, err := websocket.Dial(t.Context(), wsURL, nil)
	if err != nil {
		t.Fatalf("dial websocket: %v", err)
	}
	var server *websocket.Conn
	select {
	case server = <-accepted:
	case <-time.After(5 * time.Second):
		t.Fatal("websocket accept timeout")
	}
	defer server.CloseNow()
	// 客户端侧粗暴断开（无 close 握手）→ 服务端写入失败。
	client.CloseNow()

	conn := &wsConn{
		c:       server,
		meta:    &connMeta{nodeID: "node-write-fail"},
		gateway: &Gateway{logger: slog.New(slog.DiscardHandler)},
		sendCh:  make(chan protocol.Envelope, 4),
		done:    make(chan struct{}),
	}
	go conn.writeLoop(t.Context())

	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-conn.done:
			// 通道已判定不可用：后续发送必须立即失败（不再静默入队）。
			if err := conn.send(protocol.NewEnvelope(protocol.MsgHeartbeatAck, "x", nil)); err != errConnUnavailable {
				t.Fatalf("send after write failure = %v, want %v", err, errConnUnavailable)
			}
			return
		default:
		}
		_ = conn.send(protocol.NewEnvelope(protocol.MsgHeartbeatAck, "x", nil))
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("writeLoop did not mark the connection unavailable after a write failure")
}

func mustGet(t *testing.T, m *FileTransferManager, id string) *models.FileTransfer {
	t.Helper()
	item, err := m.Get(t.Context(), id)
	if err != nil {
		t.Fatal(err)
	}
	return item
}
