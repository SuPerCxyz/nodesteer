package hub

// P15 评审 major-02 回归：源上传停滞兜底。
//
// 背景：SOAK-20261018-01 修复后，被取代的旧尝试失败结果被世代号忽略（不误报）；
// 但若当前尝试失败且结果丢失（Agent 进程被杀、网络中断未触发回报），源上传没有
// 任何终态化路径（SweepStalledDeliveries 跳过未暂存传输）→ 传输永久停在 UPLOADING，
// DELETE /api/nodes/{id} 被 409 has active file transfer 永久阻断。
//
// SweepStalledUploads 以「part 文件 mtime / 传输 UpdatedAt 的较新者」为进展信号，
// 超过阈值（30min + 按 1 MiB/s 折算）无进展时确定性终结为 FAILED。

import (
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// TestSourceUploadStallTimeoutTerminalizesTransfer 超时兜底：UPLOADING 且无进展的传输
// 越过阈值必须终结为 FAILED（含目标与错误文案），从而释放节点删除；未到阈值不终结；幂等。
func TestSourceUploadStallTimeoutTerminalizesTransfer(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixtureFileStore(t)
	readUploadRequest(t, f.sourceConn)
	timeout := uploadStallTimeout(int64(len(f.body)))

	// 未到阈值：不终结，节点删除仍被活跃传输阻断。
	if stalled, err := f.m.SweepStalledUploads(ctx, time.Now().Add(timeout-time.Minute)); err != nil || stalled != 0 {
		t.Fatalf("sweep before deadline = %d err=%v, want 0", stalled, err)
	}
	if got := f.state().Status; got != models.FileTransferUploading {
		t.Fatalf("status before deadline = %s, want UPLOADING", got)
	}
	if err := f.nm.Delete(ctx, f.source.ID); err == nil || !strings.Contains(err.Error(), "active file transfer") {
		t.Fatalf("node deletion must stay blocked while uploading: %v", err)
	}

	// 越过阈值：传输与目标终结为 FAILED，错误可定位。
	stalled, err := f.m.SweepStalledUploads(ctx, time.Now().Add(timeout+time.Minute))
	if err != nil || stalled != 1 {
		t.Fatalf("sweep after deadline = %d err=%v, want 1", stalled, err)
	}
	state := f.state()
	if state.Status != models.FileTransferFailed {
		t.Fatalf("transfer status = %s, want FAILED", state.Status)
	}
	if !strings.Contains(state.Error, "source upload timed out") || !strings.Contains(state.Error, "source agent") {
		t.Fatalf("transfer error = %q, want locatable upload timeout", state.Error)
	}
	if state.SHA256 != "" {
		t.Fatalf("stalled upload must not keep staged metadata: %+v", state)
	}
	if got := targetStatus(state, f.target.ID); got != models.FileTargetFailed {
		t.Fatalf("target status = %s, want FAILED", got)
	}
	if got := targetError(state, f.target.ID); got != state.Error {
		t.Fatalf("target error %q does not explain source stall", got)
	}

	// 幂等：再次扫描不改变终态。
	if again, err := f.m.SweepStalledUploads(ctx, time.Now().Add(24*time.Hour)); err != nil || again != 0 {
		t.Fatalf("second sweep = %d err=%v, want 0", again, err)
	}

	// 终态后源/目标节点均可删除（409 释放）。
	if err := f.nm.Delete(ctx, f.source.ID); err != nil {
		t.Fatalf("delete source node after upload timeout: %v", err)
	}
	if err := f.nm.Delete(ctx, f.target.ID); err != nil {
		t.Fatalf("delete target node after upload timeout: %v", err)
	}
}

// TestSourceUploadStallSweepUsesPartProgress part 文件 mtime 是进展信号：
// 分片在持续写入时即使传输 UpdatedAt 较旧也不得判定停滞；停止写入后按 part mtime 计时。
func TestSourceUploadStallSweepUsesPartProgress(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixture(t)
	readUploadRequest(t, f.sourceConn)
	// 部分上传制造 part 文件（UPLOADING + 有进展）。
	if rec := f.upload(f.body[:5], ""); rec.Code != http.StatusConflict {
		t.Fatalf("partial upload status %d: %s", rec.Code, rec.Body.String())
	}
	part := f.m.partPath(f.item.ID)
	timeout := uploadStallTimeout(int64(len(f.body)))

	// 模拟 part 仍在被写入：mtime 推到未来 → 未停滞。
	active := time.Now().Add(10 * time.Minute)
	if err := os.Chtimes(part, active, active); err != nil {
		t.Fatal(err)
	}
	if stalled, err := f.m.SweepStalledUploads(ctx, active.Add(timeout-time.Minute)); err != nil || stalled != 0 {
		t.Fatalf("sweep with fresh part progress = %d err=%v, want 0", stalled, err)
	}
	if got := f.state().Status; got != models.FileTransferUploading {
		t.Fatalf("status with fresh progress = %s, want UPLOADING", got)
	}
	// 进展停止（part mtime 不再推进）→ 越过 part mtime + 阈值即停滞。
	if stalled, err := f.m.SweepStalledUploads(ctx, active.Add(timeout+time.Minute)); err != nil || stalled != 1 {
		t.Fatalf("sweep after part progress stalls = %d err=%v, want 1", stalled, err)
	}
	if got := f.state().Status; got != models.FileTransferFailed {
		t.Fatalf("transfer status = %s, want FAILED", got)
	}
}

// TestSourceUploadStallSweepLeavesPendingAndStagedAlone PENDING（等节点重连，离线持久化
// 重试语义）与已暂存传输不得被源上传看门狗终结。
func TestSourceUploadStallSweepLeavesPendingAndStagedAlone(t *testing.T) {
	ctx := t.Context()

	// PENDING：源节点无会话，上传指令从未送达。
	pending := newTransferFixtureFileStore(t)
	// 终结现场传输（CANCELED，看门狗跳过），再注销源会话模拟离线。
	readUploadRequest(t, pending.sourceConn)
	if _, err := pending.m.Cancel(ctx, pending.item.ID); err != nil {
		t.Fatal(err)
	}
	pending.m.sessions.Unregister(pending.source.ID)
	pendingItem, err := pending.m.Create(ctx, pending.source.ID, "/var/lib/nodesteer/pending.bin",
		[]FileTransferTargetRequest{{NodeID: pending.target.ID, DestinationPath: "/var/lib/nodesteer/pending-target.bin"}})
	if err != nil {
		t.Fatal(err)
	}
	if got := pendingItem.Status; got != models.FileTransferPending {
		t.Fatalf("offline source transfer = %s, want PENDING", got)
	}
	if stalled, err := pending.m.SweepStalledUploads(ctx, time.Now().Add(48*time.Hour)); err != nil || stalled != 0 {
		t.Fatalf("pending sweep = %d err=%v, want 0", stalled, err)
	}
	if got := mustGet(t, pending.m, pendingItem.ID).Status; got != models.FileTransferPending {
		t.Fatalf("PENDING transfer was terminalized: %s", got)
	}

	// 已暂存（DELIVERING）：源上传已完成，不涉及上传停滞。
	staged := newTransferFixture(t)
	readUploadRequest(t, staged.sourceConn)
	staged.uploadAndDeliver()
	if stalled, err := staged.m.SweepStalledUploads(ctx, time.Now().Add(48*time.Hour)); err != nil || stalled != 0 {
		t.Fatalf("staged sweep = %d err=%v, want 0", stalled, err)
	}
	if got := staged.state().Status; got != models.FileTransferDelivering {
		t.Fatalf("staged transfer was terminalized: %s", got)
	}
}

// TestSupersededFailureThenStallTerminalizes 组合场景（major-02 的两个分支）：
// 被取代尝试的 context canceled 不误报（保持 UPLOADING）；随后无进展时由兜底
// 确定性终结为 FAILED——即「忽略旧结果」不会变成「永久悬挂」。
func TestSupersededFailureThenStallTerminalizes(t *testing.T) {
	ctx := t.Context()
	f := newTransferFixture(t)
	first := readUploadRequest(t, f.sourceConn)
	f.m.ResumeNode(ctx, f.source.ID)
	second := readUploadRequest(t, f.sourceConn)
	if second.Attempt <= first.Attempt {
		t.Fatalf("resume must allocate a newer attempt: %d -> %d", first.Attempt, second.Attempt)
	}

	// 分支一：被取代（旧世代）失败不误报。
	if err := f.m.HandleUploadResult(ctx, f.source.ID, protocol.FileUploadResultPayload{
		TransferID: f.item.ID, OK: false, Error: "context canceled", Attempt: first.Attempt,
	}); err != nil {
		t.Fatal(err)
	}
	if got := f.state().Status; got != models.FileTransferUploading {
		t.Fatalf("superseded failure must not terminalize: %s", got)
	}

	// 分支二：当前尝试的失败结果丢失（无回报、无进展）→ 兜底确定性终态化。
	timeout := uploadStallTimeout(int64(len(f.body)))
	if stalled, err := f.m.SweepStalledUploads(ctx, time.Now().Add(timeout+time.Minute)); err != nil || stalled != 1 {
		t.Fatalf("stall sweep = %d err=%v, want 1", stalled, err)
	}
	final := f.state()
	if final.Status != models.FileTransferFailed || !strings.Contains(final.Error, "source upload timed out") {
		t.Fatalf("unreported current-attempt failure must be terminalized: %+v", final)
	}
}

// TestUploadStallTimeoutScalesWithSize 阈值 = 基线 + 按 1 MiB/s 折算的上传时间。
func TestUploadStallTimeoutScalesWithSize(t *testing.T) {
	cases := []struct {
		size int64
		want time.Duration
	}{
		{size: 0, want: defaultUploadStallTimeout},
		{size: 512, want: defaultUploadStallTimeout},
		{size: 1 << 20, want: defaultUploadStallTimeout + time.Second},
		{size: 10 << 30, want: defaultUploadStallTimeout + 10240*time.Second},
	}
	for _, tc := range cases {
		if got := uploadStallTimeout(tc.size); got != tc.want {
			t.Fatalf("uploadStallTimeout(%d) = %s, want %s", tc.size, got, tc.want)
		}
	}
}
