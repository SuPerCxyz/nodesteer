package hub

// SOAK-20261018-01/02 回归：
//   - 世代（attempt）去重：断连重连时被 ResumeNode 取代的在飞尝试，其结果不得影响传输终态；
//   - 终态冻结：SUCCESS/FAILED/CANCELED 后任何异步写入不得改写状态（唯一显式恢复路径是 Retry）。
//
// 复现依据：.e2e/runs/20260930-101945/repair/qa-resume-20261018-soak/evidence/repro-superseded-upload.json

import (
	"errors"
	"log/slog"
	"math"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// transferFixture 是一个源→目标中继传输的最小现场：源/目标在线、传输已创建并已下发首次上传。
type transferFixture struct {
	t           *testing.T
	m           *FileTransferManager
	st          store.Store
	nm          *NodeManager
	source      *models.Node
	target      *models.Node
	targetB     *models.Node
	sourceCred  string
	sourceConn  *transferTestConn
	targetConn  *transferTestConn
	targetConnB *transferTestConn
	item        *models.FileTransfer
	body        []byte
}

func newTransferFixture(t *testing.T) *transferFixture {
	t.Helper()
	return newTransferFixtureWithTargets(t, 1)
}

// newTransferFixtureWithTargets 支持 1 或 2 个目标（多目标用于验证目标级终态冻结）。
func newTransferFixtureWithTargets(t *testing.T, targetCount int) *transferFixture {
	t.Helper()
	return newTransferFixtureOpts(t, targetCount, false)
}

// newTransferMultiTargetFixture 双目标现场。
func newTransferMultiTargetFixture(t *testing.T) *transferFixture {
	t.Helper()
	return newTransferFixtureWithTargets(t, 2)
}

// newTransferFixtureFileStore 使用文件库的现场：节点删除（NodeManager.Delete →
// ListFileTransfers）等跨连接查询在 :memory: 库下可能落到空连接，涉及删除断言的用例用它。
func newTransferFixtureFileStore(t *testing.T) *transferFixture {
	t.Helper()
	return newTransferFixtureOpts(t, 1, true)
}

func newTransferFixtureOpts(t *testing.T, targetCount int, fileStore bool) *transferFixture {
	t.Helper()
	ctx := t.Context()
	var st *store.SQLiteStore
	var err error
	if fileStore {
		st, err = store.OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	} else {
		st, err = store.OpenInMemory()
	}
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	nm := NewNodeManager(st, NewRevisionManager(st))
	source, sourceCred, _, err := nm.RegisterOrUpdate(ctx, "epoch-source", "epoch-source", "10.9.0.1", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	target, _, _, err := nm.RegisterOrUpdate(ctx, "epoch-target", "epoch-target", "10.9.0.2", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionManager()
	sourceConn := &transferTestConn{nodeID: source.ID, sent: make(chan protocol.Envelope, 8)}
	targetConn := &transferTestConn{nodeID: target.ID, sent: make(chan protocol.Envelope, 8)}
	sessions.Register(sourceConn)
	sessions.Register(targetConn)
	f := &transferFixture{
		t: t, st: st, nm: nm, source: source, target: target, sourceCred: sourceCred,
		sourceConn: sourceConn, targetConn: targetConn,
		body: []byte("epoch-transfer-body"),
	}
	requests := []FileTransferTargetRequest{{NodeID: target.ID, DestinationPath: "/var/lib/nodesteer/epoch-target.bin"}}
	if targetCount > 1 {
		targetB, _, _, err := nm.RegisterOrUpdate(ctx, "epoch-target-b", "epoch-target-b", "10.9.0.3", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
		if err != nil {
			t.Fatal(err)
		}
		f.targetB = targetB
		f.targetConnB = &transferTestConn{nodeID: targetB.ID, sent: make(chan protocol.Envelope, 8)}
		sessions.Register(f.targetConnB)
		requests = append(requests, FileTransferTargetRequest{NodeID: targetB.ID, DestinationPath: "/var/lib/nodesteer/epoch-target-b.bin"})
	}
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.New(slog.DiscardHandler))
	if err != nil {
		t.Fatal(err)
	}
	f.m = m
	item, err := m.Create(ctx, source.ID, "/var/lib/nodesteer/epoch-source.bin", requests)
	if err != nil {
		t.Fatal(err)
	}
	f.item = item
	return f
}

// upload 模拟 Agent 的上传数据通道请求（分片长度未知，与真实 Agent chunked 上传一致）。
func (f *transferFixture) upload(content []byte, contentRange string) *httptest.ResponseRecorder {
	f.t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/agent/transfers/"+f.item.ID+"/upload", bytesReader(content))
	req.Header.Set("X-NodeSteer-Agent-ID", f.source.AgentID)
	req.Header.Set("X-NodeSteer-Agent-Token", f.sourceCred)
	req.Header.Set("X-NodeSteer-File-Size", strconv.Itoa(len(f.body)))
	if contentRange != "" {
		req.Header.Set("Content-Range", contentRange)
	}
	req.ContentLength = -1
	rec := httptest.NewRecorder()
	f.m.HandleAgentHTTP(rec, req)
	return rec
}

// uploadAndDeliver 完成整段上传并读取首个交付请求（此时传输为 DELIVERING）。
func (f *transferFixture) uploadAndDeliver() protocol.FileDeliveryRequestPayload {
	f.t.Helper()
	if rec := f.upload(f.body, ""); rec.Code != http.StatusOK {
		f.t.Fatalf("full upload status %d: %s", rec.Code, rec.Body.String())
	}
	return readDeliveryRequest(f.t, f.targetConn)
}

// state 读取库内最新传输状态。
func (f *transferFixture) state() *models.FileTransfer {
	f.t.Helper()
	state, err := f.m.Get(f.t.Context(), f.item.ID)
	if err != nil {
		f.t.Fatal(err)
	}
	return state
}

// drain 清空连接上已排队的消息（例如 Cancel 通知），便于断言“没有新指令下发”。
func drain(conn *transferTestConn) {
	for {
		select {
		case <-conn.sent:
		default:
			return
		}
	}
}

// TestFileTransferSupersededUploadAttemptIgnoredAndResumeCompletes 覆盖 SOAK-20261018-01：
// 断连重连时 ResumeNode 取代在飞上传，旧尝试的 context canceled 回执必须被忽略，
// 真实续传必须继续并在世代匹配的交付回执后完成 SUCCESS。
func TestFileTransferSupersededUploadAttemptIgnoredAndResumeCompletes(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixture(t)
	first := readUploadRequest(t, f.sourceConn)
	if first.Attempt == 0 {
		t.Fatalf("upload request must carry a nonzero attempt: %+v", first)
	}

	// 旧尝试已暂存一半：分片上传返回 409 + next_offset。
	half := f.body[:5]
	if rec := f.upload(half, ""); rec.Code != http.StatusConflict {
		t.Fatalf("partial upload status %d: %s", rec.Code, rec.Body.String())
	}

	// 断连重连：ResumeNode 从已暂存偏移重投，分配新世代。
	f.m.ResumeNode(ctx, f.source.ID)
	second := readUploadRequest(t, f.sourceConn)
	if second.Attempt <= first.Attempt {
		t.Fatalf("resume must allocate a newer attempt: first=%d second=%d", first.Attempt, second.Attempt)
	}
	if second.Offset != int64(len(half)) {
		t.Fatalf("resume offset = %d, want %d", second.Offset, len(half))
	}

	// 被取代的旧尝试回执（context canceled）：不得把传输终态化为 FAILED。
	if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
		TransferID: f.item.ID, OK: false, Error: "context canceled", Attempt: first.Attempt,
	}); err != nil {
		t.Fatal(err)
	}
	mid := f.state()
	if mid.Status != models.FileTransferUploading || mid.Error != "" {
		t.Fatalf("superseded attempt must not terminalize the transfer: %+v", mid)
	}
	if targetStatus(mid, f.target.ID) != models.FileTargetPending {
		t.Fatalf("superseded attempt must not fail targets: %+v", mid)
	}

	// 真实续传完成：剩余分片 → DELIVERING → 目标交付 → SUCCESS。
	if rec := f.upload(f.body[5:], "bytes 5-18/19"); rec.Code != http.StatusOK {
		t.Fatalf("resumed upload status %d: %s", rec.Code, rec.Body.String())
	}
	delivery := readDeliveryRequest(t, f.targetConn)
	if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
		TransferID: f.item.ID, OK: true, Attempt: delivery.Attempt,
	}); err != nil {
		t.Fatal(err)
	}
	final := f.state()
	if final.Status != models.FileTransferSuccess || targetStatus(final, f.target.ID) != models.FileTargetSuccess {
		t.Fatalf("resumed transfer must complete: %+v", final)
	}
	if final.SHA256 == "" {
		t.Fatalf("successful transfer must keep staged metadata: %+v", final)
	}
}

// TestFileTransferSupersededDeliveryAttemptIgnored 同源问题（目标侧）：重投取代旧下载尝试后，
// 旧尝试的取消回执不得把目标/传输误判为 FAILED，新世代结果照常生效。
func TestFileTransferSupersededDeliveryAttemptIgnored(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixture(t)
	readUploadRequest(t, f.sourceConn)
	first := f.uploadAndDeliver()

	f.m.ResumeNode(ctx, f.target.ID)
	second := readDeliveryRequest(t, f.targetConn)
	if second.Attempt <= first.Attempt {
		t.Fatalf("redelivery must allocate a newer attempt: first=%d second=%d", first.Attempt, second.Attempt)
	}

	if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
		TransferID: f.item.ID, OK: false, Error: "context canceled", Attempt: first.Attempt,
	}); err != nil {
		t.Fatal(err)
	}
	mid := f.state()
	if mid.Status != models.FileTransferDelivering || targetStatus(mid, f.target.ID) != models.FileTargetDelivering {
		t.Fatalf("superseded delivery attempt must not fail target/transfer: %+v", mid)
	}

	if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
		TransferID: f.item.ID, OK: true, Attempt: second.Attempt,
	}); err != nil {
		t.Fatal(err)
	}
	final := f.state()
	if final.Status != models.FileTransferSuccess {
		t.Fatalf("current delivery attempt must complete the transfer: %+v", final)
	}
}

// TestFileTransferCurrentAttemptFailureStillFails 反向保证：世代匹配的失败回执必须照常生效，
// 不能把「忽略旧结果」变成「丢失有效结果」。
func TestFileTransferCurrentAttemptFailureStillFails(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixture(t)
	first := readUploadRequest(t, f.sourceConn)
	f.m.ResumeNode(ctx, f.source.ID)
	second := readUploadRequest(t, f.sourceConn)

	// 旧世代失败：忽略。
	if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
		TransferID: f.item.ID, OK: false, Error: "context canceled", Attempt: first.Attempt,
	}); err != nil {
		t.Fatal(err)
	}
	if got := f.state().Status; got != models.FileTransferUploading {
		t.Fatalf("stale failure changed status to %s", got)
	}

	// 当前世代失败：必须终态化为 FAILED 并保留原因。
	if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
		TransferID: f.item.ID, OK: false, Error: "upload retry limit exceeded", Attempt: second.Attempt,
	}); err != nil {
		t.Fatal(err)
	}
	final := f.state()
	if final.Status != models.FileTransferFailed {
		t.Fatalf("current attempt failure must fail the transfer: %+v", final)
	}
	if want := "source upload failed: upload retry limit exceeded"; final.Error != want {
		t.Fatalf("transfer error = %q, want %q", final.Error, want)
	}
	if targetStatus(final, f.target.ID) != models.FileTargetFailed {
		t.Fatalf("targets must be terminalized on source failure: %+v", final)
	}
}

// TestFileTransferUploadAttemptWraparound 边界：世代号 uint64 回绕后仍以「最近一次下发」为准；
// 回绕前的远古世代回执被忽略（2^64 次下发间隔内不可能存在在飞尝试，回绕值只会与新世代相同）。
func TestFileTransferUploadAttemptWraparound(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixture(t)
	first := readUploadRequest(t, f.sourceConn)
	if first.Attempt != 1 {
		t.Fatalf("first attempt = %d, want 1", first.Attempt)
	}

	// 直接推进世代号到回绕边界，模拟长期运行后的计数溢出。
	f.m.attemptMu.Lock()
	f.m.uploadAttempts[f.item.ID] = math.MaxUint64
	f.m.attemptMu.Unlock()
	f.m.ResumeNode(ctx, f.source.ID)
	wrapped := readUploadRequest(t, f.sourceConn)
	if wrapped.Attempt != 0 {
		t.Fatalf("wrapped attempt = %d, want 0", wrapped.Attempt)
	}

	// 远古世代回执被忽略。
	if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
		TransferID: f.item.ID, OK: false, Error: "ancient failure", Attempt: math.MaxUint64,
	}); err != nil {
		t.Fatal(err)
	}
	if got := f.state().Status; got != models.FileTransferUploading {
		t.Fatalf("pre-wrap attempt must be ignored, status=%s", got)
	}

	// 回绕后的当前世代（0）回执照常生效。
	if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
		TransferID: f.item.ID, OK: false, Error: "wrapped failure", Attempt: 0,
	}); err != nil {
		t.Fatal(err)
	}
	if got := f.state().Status; got != models.FileTransferFailed {
		t.Fatalf("wrapped current attempt must be accepted, status=%s", got)
	}
}

// TestFileTransferConcurrentUploadResultsOnlyCurrentAttemptWins 边界：两个在飞尝试的回执
// 并发到达时，只有当前世代生效（-race 下验证无数据竞争）。
func TestFileTransferConcurrentUploadResultsOnlyCurrentAttemptWins(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixture(t)
	first := readUploadRequest(t, f.sourceConn)
	f.m.ResumeNode(ctx, f.source.ID)
	second := readUploadRequest(t, f.sourceConn)

	var wg sync.WaitGroup
	for _, payload := range []protocol.FileUploadResultPayload{
		{TransferID: f.item.ID, OK: false, Error: "context canceled", Attempt: first.Attempt},
		{TransferID: f.item.ID, OK: false, Error: "disk full", Attempt: second.Attempt},
	} {
		wg.Add(1)
		go func(p protocol.FileUploadResultPayload) {
			defer wg.Done()
			if err := f.m.HandleUploadResult(ctx, f.source.ID, p); err != nil {
				t.Errorf("HandleUploadResult(%+v): %v", p, err)
			}
		}(payload)
	}
	wg.Wait()

	final := f.state()
	if final.Status != models.FileTransferFailed {
		t.Fatalf("current attempt failure must win: %+v", final)
	}
	if want := "source upload failed: disk full"; final.Error != want {
		t.Fatalf("transfer error = %q, want %q (stale result must not win)", final.Error, want)
	}
}

// TestFileTransferCancelRaceWithStaleDispatchDoesNotResurrect 取消竞态：ResumeNode 在锁外
// 读到陈旧实例后 Cancel 先落 CANCELED，陈旧实例的 dispatch 不得把传输/目标复活为进行中。
func TestFileTransferCancelRaceWithStaleDispatchDoesNotResurrect(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixture(t)
	readUploadRequest(t, f.sourceConn)

	// 模拟 ResumeNode 先读列表（UPLOADING/PENDING），随后 Cancel 抢先在锁内落终态。
	stale := f.state()
	if stale.Status != models.FileTransferUploading {
		t.Fatalf("fixture must start UPLOADING: %+v", stale)
	}
	if _, err := f.m.Cancel(ctx, f.item.ID); err != nil {
		t.Fatal(err)
	}
	drain(f.sourceConn)
	drain(f.targetConn)

	f.m.dispatchSource(ctx, stale)
	f.m.dispatchTarget(ctx, stale, stale.Targets[0])

	final := f.state()
	if final.Status != models.FileTransferCanceled {
		t.Fatalf("stale dispatch resurrected canceled transfer: %+v", final)
	}
	if targetStatus(final, f.target.ID) != models.FileTargetCanceled {
		t.Fatalf("stale dispatch resurrected canceled target: %+v", final)
	}
	expectNoEnvelope(t, f.sourceConn)
	expectNoEnvelope(t, f.targetConn)
}

// TestFileTransferTerminalStatesFrozen 终态冻结矩阵（SOAK-20261018-02）：SUCCESS/FAILED/CANCELED
// 后迟到的上传回执、交付回执、续传 POST、重连重投、交付看门狗与直接 completeUpload 一律被拒绝，
// 状态保持不变、无副作用落盘。
func TestFileTransferTerminalStatesFrozen(t *testing.T) {
	ctx := t.Context()
	cases := []struct {
		name    string
		prepare func(f *transferFixture) string // 返回期望终态
	}{
		{
			name: "SUCCESS",
			prepare: func(f *transferFixture) string {
				readUploadRequest(f.t, f.sourceConn)
				delivery := f.uploadAndDeliver()
				if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
					TransferID: f.item.ID, OK: true, Attempt: delivery.Attempt,
				}); err != nil {
					f.t.Fatal(err)
				}
				return models.FileTransferSuccess
			},
		},
		{
			name: "FAILED",
			prepare: func(f *transferFixture) string {
				request := readUploadRequest(f.t, f.sourceConn)
				if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
					TransferID: f.item.ID, OK: false, Error: "no such file", Attempt: request.Attempt,
				}); err != nil {
					f.t.Fatal(err)
				}
				return models.FileTransferFailed
			},
		},
		{
			name: "CANCELED",
			prepare: func(f *transferFixture) string {
				readUploadRequest(f.t, f.sourceConn)
				if _, err := f.m.Cancel(ctx, f.item.ID); err != nil {
					f.t.Fatal(err)
				}
				drain(f.sourceConn)
				drain(f.targetConn)
				return models.FileTransferCanceled
			},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newTransferFixture(t)
			want := tc.prepare(f)
			if got := f.state().Status; got != want {
				t.Fatalf("fixture status = %s, want %s", got, want)
			}

			// 迟到的源失败回执（当前世代号也不得改写终态）。
			if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
				TransferID: f.item.ID, OK: false, Error: "late failure", Attempt: 1,
			}); err != nil {
				t.Fatal(err)
			}
			// 迟到的目标结果（成功/失败两个方向）。
			for _, ok := range []bool{true, false} {
				if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
					TransferID: f.item.ID, OK: ok, Error: "late delivery", Attempt: 1,
				}); err != nil {
					t.Fatal(err)
				}
			}
			// 续传 POST（整段与分片两种）：终态一律拒绝且不落盘。
			// SUCCESS/FAILED → 410（终态判定先于“已暂存”早退）；CANCELED → 409。
			rec := f.upload(f.body, "")
			partial := f.upload(f.body[:1], "")
			switch want {
			case models.FileTransferSuccess, models.FileTransferFailed:
				if rec.Code != http.StatusGone {
					t.Fatalf("terminal upload replay = %d %s, want 410", rec.Code, rec.Body.String())
				}
				if partial.Code != http.StatusGone {
					t.Fatalf("terminal partial upload = %d %s, want 410", partial.Code, partial.Body.String())
				}
			case models.FileTransferCanceled:
				if rec.Code != http.StatusConflict {
					t.Fatalf("canceled upload replay = %d %s, want 409", rec.Code, rec.Body.String())
				}
				if partial.Code != http.StatusConflict {
					t.Fatalf("canceled partial upload = %d %s, want 409", partial.Code, partial.Body.String())
				}
			}
			if _, statErr := os.Stat(f.m.partPath(f.item.ID)); statErr == nil {
				t.Fatalf("terminal transfer must not stage upload data (part file exists)")
			}
			if _, statErr := os.Stat(f.m.blobPath(f.item.ID)); statErr == nil && want != models.FileTransferSuccess {
				t.Fatalf("terminal transfer must not stage blob (blob file exists)")
			}
			// 分片写回 UPLOADING 的写点本身也必须拒绝终态（纵深防御）。
			if f.m.markUploading(ctx, f.state()) {
				t.Fatalf("markUploading must refuse terminal transfer")
			}
			if got := f.state().Status; got != want {
				t.Fatalf("markUploading rewrote terminal state: %s", got)
			}

			// 重连重投：终态不自动重投。
			f.m.ResumeNode(ctx, f.source.ID)
			f.m.ResumeNode(ctx, f.target.ID)
			expectNoEnvelope(t, f.sourceConn)
			expectNoEnvelope(t, f.targetConn)

			// 交付看门狗与源上传看门狗：终态跳过。
			if stalled, err := f.m.SweepStalledDeliveries(ctx, time.Now().Add(48*time.Hour)); err != nil || stalled != 0 {
				t.Fatalf("sweep on terminal transfer = %d err=%v, want 0", stalled, err)
			}
			if stalled, err := f.m.SweepStalledUploads(ctx, time.Now().Add(48*time.Hour)); err != nil || stalled != 0 {
				t.Fatalf("upload sweep on terminal transfer = %d err=%v, want 0", stalled, err)
			}

			// 纵深防御：直接调用 completeUpload 必须被终态拒绝。
			if err := f.m.completeUpload(ctx, f.state(), int64(len(f.body))); !errors.Is(err, errTransferTerminal) {
				t.Fatalf("completeUpload on terminal transfer = %v, want errTransferTerminal", err)
			}

			final := f.state()
			if final.Status != want {
				t.Fatalf("terminal state was rewritten: %+v", final)
			}
			if final.Error == "late failure" || final.Error == "late delivery" {
				t.Fatalf("late result rewrote terminal error: %+v", final)
			}
		})
	}
}

// TestFileTransferExplicitActionsOnTerminal 显式管理动作的终态语义（P15 评审 major-01(2)）：
// CANCELED 幂等且不可重试；SUCCESS/FAILED 拒绝取消（终态不可被取消改写）；
// FAILED 的唯一恢复路径是显式 Retry。
func TestFileTransferExplicitActionsOnTerminal(t *testing.T) {
	ctx := t.Context()

	t.Run("CANCELED idempotent and retry rejected", func(t *testing.T) {
		f := newTransferFixture(t)
		readUploadRequest(t, f.sourceConn)
		if _, err := f.m.Cancel(ctx, f.item.ID); err != nil {
			t.Fatal(err)
		}
		drain(f.sourceConn)
		drain(f.targetConn)
		// 重复取消：幂等，无新指令。
		again, err := f.m.Cancel(ctx, f.item.ID)
		if err != nil || again.Status != models.FileTransferCanceled {
			t.Fatalf("second cancel = %+v err=%v", again, err)
		}
		expectNoEnvelope(t, f.sourceConn)
		if _, err := f.m.Retry(ctx, f.item.ID, ""); err == nil {
			t.Fatal("retry of canceled transfer must be rejected")
		}
		if got := f.state().Status; got != models.FileTransferCanceled {
			t.Fatalf("canceled transfer was resurrected: %s", got)
		}
	})

	t.Run("SUCCESS cancel rejected", func(t *testing.T) {
		f := newTransferFixture(t)
		readUploadRequest(t, f.sourceConn)
		delivery := f.uploadAndDeliver()
		if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
			TransferID: f.item.ID, OK: true, Attempt: delivery.Attempt,
		}); err != nil {
			t.Fatal(err)
		}
		if _, err := f.m.Cancel(ctx, f.item.ID); err == nil {
			t.Fatal("cancel of successful transfer must be rejected")
		}
		if got := f.state().Status; got != models.FileTransferSuccess {
			t.Fatalf("successful transfer was rewritten: %s", got)
		}
		if got := targetStatus(f.state(), f.target.ID); got != models.FileTargetSuccess {
			t.Fatalf("target of successful transfer was rewritten: %s", got)
		}
	})

	t.Run("FAILED cancel rejected and state preserved", func(t *testing.T) {
		f := newTransferFixture(t)
		request := readUploadRequest(t, f.sourceConn)
		if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
			TransferID: f.item.ID, OK: false, Error: "boom", Attempt: request.Attempt,
		}); err != nil {
			t.Fatal(err)
		}
		before := f.state()
		if _, err := f.m.Cancel(ctx, f.item.ID); err == nil {
			t.Fatal("cancel of failed transfer must be rejected (terminal freeze)")
		}
		after := f.state()
		if after.Status != models.FileTransferFailed || after.Error != before.Error {
			t.Fatalf("failed transfer was rewritten by cancel: %+v", after)
		}
		if got := targetStatus(after, f.target.ID); got != models.FileTargetFailed {
			t.Fatalf("target of failed transfer was rewritten: %s", got)
		}
	})

	t.Run("FAILED retry is the explicit recovery path", func(t *testing.T) {
		f := newTransferFixture(t)
		first := readUploadRequest(t, f.sourceConn)
		if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
			TransferID: f.item.ID, OK: false, Error: "boom", Attempt: first.Attempt,
		}); err != nil {
			t.Fatal(err)
		}
		retried, err := f.m.Retry(ctx, f.item.ID, "")
		if err != nil {
			t.Fatal(err)
		}
		if retried.Status != models.FileTransferUploading {
			t.Fatalf("retry of failed transfer = %+v, want UPLOADING", retried)
		}
		retryRequest := readUploadRequest(t, f.sourceConn)
		if retryRequest.Attempt <= first.Attempt {
			t.Fatalf("retry must allocate a newer attempt: first=%d retry=%d", first.Attempt, retryRequest.Attempt)
		}
		// 重试后的当前世代失败仍必须生效（终态冻结不吞掉显式恢复后的真实结果）。
		if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
			TransferID: f.item.ID, OK: false, Error: "still missing", Attempt: retryRequest.Attempt,
		}); err != nil {
			t.Fatal(err)
		}
		if got := f.state().Status; got != models.FileTransferFailed {
			t.Fatalf("retried attempt failure must fail again: %s", got)
		}
	})
}

// TestFileTransferTerminalTargetsFrozen 目标级终态冻结（P15 评审 major-01(3)）：
// 已终态目标不得被迟到结果改写、不得被重投复活；多目标下不得出现
// 「已成功目标被迟到失败回执翻转为 FAILED」的反向翻转。
func TestFileTransferTerminalTargetsFrozen(t *testing.T) {
	ctx := t.Context()

	t.Run("late duplicate result cannot flip succeeded target", func(t *testing.T) {
		f := newTransferMultiTargetFixture(t)
		readUploadRequest(t, f.sourceConn)
		if rec := f.upload(f.body, ""); rec.Code != http.StatusOK {
			t.Fatalf("upload status %d", rec.Code)
		}
		requestA := readDeliveryRequest(t, f.targetConn)
		requestB := readDeliveryRequest(t, f.targetConnB)
		// A 交付成功：传输仍在 DELIVERING（B 未回报）。
		if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
			TransferID: f.item.ID, OK: true, Attempt: requestA.Attempt,
		}); err != nil {
			t.Fatal(err)
		}
		mid := f.state()
		if targetStatus(mid, f.target.ID) != models.FileTargetSuccess || mid.Status != models.FileTransferDelivering {
			t.Fatalf("fixture must leave A SUCCESS and transfer DELIVERING: %+v", mid)
		}
		// 迟到/重复的失败回执（同世代）不得把已成功目标翻转。
		if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
			TransferID: f.item.ID, OK: false, Error: "late duplicate", Attempt: requestA.Attempt,
		}); err != nil {
			t.Fatal(err)
		}
		state := f.state()
		if targetStatus(state, f.target.ID) != models.FileTargetSuccess {
			t.Fatalf("terminal target was flipped by late result: %+v", state)
		}
		if state.Status != models.FileTransferDelivering {
			t.Fatalf("transfer aggregate changed by late duplicate: %+v", state)
		}
		// B 正常完成 → SUCCESS。
		if err := f.m.HandleDeliveryResult(ctx, f.targetB.ID, protocol.FileDeliveryResultPayload{
			TransferID: f.item.ID, OK: true, Attempt: requestB.Attempt,
		}); err != nil {
			t.Fatal(err)
		}
		if got := f.state().Status; got != models.FileTransferSuccess {
			t.Fatalf("transfer must complete after B succeeds: %s", got)
		}
	})

	t.Run("dispatchTarget does not resurrect terminal targets", func(t *testing.T) {
		f := newTransferMultiTargetFixture(t)
		readUploadRequest(t, f.sourceConn)
		if rec := f.upload(f.body, ""); rec.Code != http.StatusOK {
			t.Fatalf("upload status %d", rec.Code)
		}
		requestA := readDeliveryRequest(t, f.targetConn)
		_ = readDeliveryRequest(t, f.targetConnB)
		// A 交付失败 → 目标终态 FAILED，而传输因 B 未回报仍是 DELIVERING。
		if err := f.m.HandleDeliveryResult(ctx, f.target.ID, protocol.FileDeliveryResultPayload{
			TransferID: f.item.ID, OK: false, Error: "boom", Attempt: requestA.Attempt,
		}); err != nil {
			t.Fatal(err)
		}
		mid := f.state()
		if targetStatus(mid, f.target.ID) != models.FileTargetFailed || mid.Status != models.FileTransferDelivering {
			t.Fatalf("fixture must leave A FAILED and transfer DELIVERING: %+v", mid)
		}
		drain(f.targetConn)
		// 目标已 FAILED：直接重投必须被拒绝（终态目标不复活；显式 Retry 会先重置为 PENDING）。
		f.m.dispatchTarget(ctx, mid, mid.Targets[0])
		expectNoEnvelope(t, f.targetConn)
		if got := targetStatus(f.state(), f.target.ID); got != models.FileTargetFailed {
			t.Fatalf("terminal target was resurrected: %s", got)
		}
		// 显式 Retry 仍是唯一恢复路径：重置为 PENDING 后重投成功。
		if _, err := f.m.Retry(ctx, f.item.ID, f.target.ID); err != nil {
			t.Fatal(err)
		}
		if got := targetStatus(f.state(), f.target.ID); got != models.FileTargetDelivering {
			t.Fatalf("retry must re-dispatch the failed target: %s", got)
		}
		readDeliveryRequest(t, f.targetConn)
	})
}
