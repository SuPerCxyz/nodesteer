package hub

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/google/uuid"
)

const defaultMaxFileTransferBytes = int64(10 << 30)

// 交付停滞阈值：目标进入 DELIVERING 后超过该时限仍未上报结果，判定投递失败并终态化。
//
// 为什么必须有：Hub 可能把目标写成 DELIVERING 而指令实际未送达（websocket 写失败、
// 消息丢在发送缓冲区等），此时既没有目标回报也没有超时，传输永久停在非终态，
// DELETE /api/nodes/{id} 会被 409 has active file transfer 永久阻断
// （缺陷 E2E-20261017-XFER-REMOTE-TARGET-STUCK-DELIVERING）。
//
// 阈值取“基线 + 按文件大小线性放宽”，因为交付阶段是目标 Agent 通过 HTTP 下载整个文件，
// 没有进度上报，大文件在慢链路上的合法耗时可能很长：
// 基线 30 分钟，另按保守下限 1 MiB/s 折算下载时间（10 GiB 文件约 2.8 小时）。
// 仅作用于 DELIVERING（指令已下发、等待目标回报）；PENDING 表示“等源暂存/等节点重连”，
// 属正常可恢复状态，不在此终结。
const (
	defaultDeliveryStallTimeout = 30 * time.Minute
	deliveryStallBytesPerSecond = int64(1 << 20)
)

// deliveryStallTimeout 按文件大小计算交付停滞阈值。
func deliveryStallTimeout(size int64) time.Duration {
	if size <= 0 {
		return defaultDeliveryStallTimeout
	}
	return defaultDeliveryStallTimeout + time.Duration(size/deliveryStallBytesPerSecond)*time.Second
}

// 源上传停滞阈值：传输处于 UPLOADING 且超过该时限无任何上传进展时，判定源上传失败并终态化。
//
// 为什么必须有：源上传的唯一终态化路径是 Agent 上报失败结果或上传 HTTP 报错。
// 若当前尝试的失败结果丢失（进程被杀、网络中断且未触发回报），传输会永久停在
// UPLOADING，DELETE /api/nodes/{id} 被 409 has active file transfer 永久阻断
// （P15 评审 major-02；SOAK-20261018-01 修复后旧世代结果被忽略，不再有“误报兜底”）。
//
// 阈值与交付停滞同构：基线 30 分钟 + 按保守下限 1 MiB/s 折算上传时间。
// 进展信号取「part 文件 mtime」与「传输 UpdatedAt（最近一次下发/分片写入）」的较新者：
// 分片上传在持续写入 part 时 mtime 持续推进，重连重投会刷新 UpdatedAt（每次重投都给
// 源端一个新的完整窗口）。仅作用于 UPLOADING；PENDING 表示“等节点重连”，属正常
// 可恢复状态（离线持久化重试），不在此终结。
const (
	defaultUploadStallTimeout = 30 * time.Minute
	uploadStallBytesPerSecond = int64(1 << 20)
)

// uploadStallTimeout 按文件大小计算源上传停滞阈值。
func uploadStallTimeout(size int64) time.Duration {
	if size <= 0 {
		return defaultUploadStallTimeout
	}
	return defaultUploadStallTimeout + time.Duration(size/uploadStallBytesPerSecond)*time.Second
}

// ResolveGatewayBaseURL 返回 Agent 可访问的 Gateway HTTP 基址。
func ResolveGatewayBaseURL(configured, webBaseURL, gatewayAddr string) string {
	if configured != "" {
		return normalizeHTTPBaseURL(configured)
	}
	// 端口统一取自 gateway_addr：主路径（hostname 有效）与 fallback 共用同一来源，
	// 不再硬编码 8443 —— 单端口模式（gateway 与 web 同端口）下 fallback 才能正确。
	port := "8443"
	if _, configuredPort, splitErr := net.SplitHostPort(gatewayAddr); splitErr == nil && configuredPort != "" {
		port = configuredPort
	}
	u, err := url.Parse(webBaseURL)
	if err != nil || u.Hostname() == "" {
		return "http://localhost:" + port
	}
	scheme := "http"
	if u.Scheme == "https" {
		scheme = "https"
	}
	u.Host = net.JoinHostPort(u.Hostname(), port)
	u.Scheme = scheme
	u.Path, u.RawQuery, u.Fragment = "", "", ""
	return strings.TrimRight(u.String(), "/")
}

// FileTransferTargetRequest 创建传输时的目标描述。
type FileTransferTargetRequest struct {
	NodeID          string `json:"node_id"`
	DestinationPath string `json:"destination_path"`
	Mode            uint32 `json:"mode,omitempty"`
}

// FileTransferManager 管理 Hub 中继文件传输。
type FileTransferManager struct {
	store          store.Store
	storageDir     string
	sessions       *SessionManager
	nodes          *NodeManager
	logger         *slog.Logger
	gatewayBaseURL string
	maxBytes       int64
	mu             sync.Mutex
	// attemptMu 保护世代号表：dispatch（无 m.mu）与结果处理（持 m.mu）都会访问，
	// 因此不能挂在 m.mu 下。uploadAttempts / deliveryAttempts 记录最近一次下发给
	// Agent 的尝试世代号；只在内存中维护：Hub 重启后所有连接重建，ResumeNode 会
	// 重新下发并建立新世代，迟到的旧世代结果一律被忽略。
	attemptMu        sync.Mutex
	uploadAttempts   map[string]uint64
	deliveryAttempts map[string]uint64
}

// NewFileTransferManager 创建文件传输管理器。
func NewFileTransferManager(st store.Store, storageDir, gatewayBaseURL string, sessions *SessionManager, nodes *NodeManager, logger *slog.Logger) (*FileTransferManager, error) {
	dir := filepath.Join(storageDir, "file-transfers")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	return &FileTransferManager{
		store: st, storageDir: dir, sessions: sessions, nodes: nodes,
		logger: logger, gatewayBaseURL: normalizeHTTPBaseURL(gatewayBaseURL), maxBytes: defaultMaxFileTransferBytes,
		uploadAttempts: map[string]uint64{}, deliveryAttempts: map[string]uint64{},
	}, nil
}

// 传输终态：进入后不可再被任何异步事件改写（缺陷 SOAK-20261018-02）。
// 唯一允许的离开路径是显式管理动作 Retry（FAILED → 重新上传/交付）。
func isTerminalTransferStatus(status string) bool {
	switch status {
	case models.FileTransferSuccess, models.FileTransferFailed, models.FileTransferCanceled:
		return true
	}
	return false
}

// 目标终态：与传输同构，目标进入终态后不得被迟到结果/重投改写（P15 评审 major-01）。
func isTerminalTargetStatus(status string) bool {
	switch status {
	case models.FileTargetSuccess, models.FileTargetFailed, models.FileTargetCanceled:
		return true
	}
	return false
}

// errTransferTerminal 表示写入因传输已进入终态被拒绝。
var errTransferTerminal = errors.New("transfer is in a terminal state")

func deliveryAttemptKey(transferID, nodeID string) string {
	return transferID + "\x00" + nodeID
}

// nextUploadAttempt / currentUploadAttempt 管理源上传世代号（见 attemptMu 注释）。
func (m *FileTransferManager) nextUploadAttempt(id string) uint64 {
	m.attemptMu.Lock()
	defer m.attemptMu.Unlock()
	m.uploadAttempts[id]++
	return m.uploadAttempts[id]
}

func (m *FileTransferManager) currentUploadAttempt(id string) uint64 {
	m.attemptMu.Lock()
	defer m.attemptMu.Unlock()
	return m.uploadAttempts[id]
}

func (m *FileTransferManager) nextDeliveryAttempt(key string) uint64 {
	m.attemptMu.Lock()
	defer m.attemptMu.Unlock()
	m.deliveryAttempts[key]++
	return m.deliveryAttempts[key]
}

func (m *FileTransferManager) currentDeliveryAttempt(key string) uint64 {
	m.attemptMu.Lock()
	defer m.attemptMu.Unlock()
	return m.deliveryAttempts[key]
}

// logTerminalWriteRejected 记录被终态冻结拒绝的写入，便于观测迟到的结果/上传。
func (m *FileTransferManager) logTerminalWriteRejected(t *models.FileTransfer, path string) {
	if m.logger == nil {
		return
	}
	m.logger.Warn("file transfer write rejected: terminal state frozen",
		"transfer", t.ID, "status", t.Status, "path", path)
}

// logStaleAttemptIgnored 记录被取代的旧世代结果。
func (m *FileTransferManager) logStaleAttemptIgnored(t *models.FileTransfer, path string, attempt, current uint64) {
	if m.logger == nil {
		return
	}
	m.logger.Warn("file transfer result ignored: superseded attempt",
		"transfer", t.ID, "path", path, "attempt", attempt, "current_attempt", current)
}

// logTerminalTargetWriteRejected 记录被目标级终态冻结拒绝的写入。
func (m *FileTransferManager) logTerminalTargetWriteRejected(t *models.FileTransfer, target *models.FileTransferTarget, path string) {
	if m.logger == nil {
		return
	}
	m.logger.Warn("file transfer target write rejected: terminal state frozen",
		"transfer", t.ID, "node", target.NodeID, "status", target.Status, "path", path)
}

// SetMaxBytes 设置单文件传输上限。
func (m *FileTransferManager) SetMaxBytes(n int64) {
	if n > 0 {
		m.maxBytes = n
	}
}

// StartDeliveryWatchdog 周期性终结停滞在 DELIVERING 的目标（见 deliveryStallTimeout）
// 与停滞在 UPLOADING 的源上传（见 uploadStallTimeout，P15 评审 major-02）。
func (m *FileTransferManager) StartDeliveryWatchdog(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = time.Minute
	}
	go func() {
		t := time.NewTicker(interval)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				if _, err := m.SweepStalledDeliveries(ctx, time.Now()); err != nil {
					m.logger.Warn("file delivery watchdog sweep failed", "error", err)
				}
				if _, err := m.SweepStalledUploads(ctx, time.Now()); err != nil {
					m.logger.Warn("file upload watchdog sweep failed", "error", err)
				}
			}
		}
	}()
}

// SweepStalledDeliveries 把超过交付时限仍未上报结果的 DELIVERING 目标终结为 FAILED，
// 并同步聚合传输状态，使传输回到终态（从而释放节点删除）。返回被终结的目标数。
// now 由调用方注入，便于测试确定性地跨过时限。
func (m *FileTransferManager) SweepStalledDeliveries(ctx context.Context, now time.Time) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	transfers, err := m.store.ListFileTransfers(ctx)
	if err != nil {
		return 0, err
	}
	stalled := 0
	for _, t := range transfers {
		// 未暂存（等源上传）、已取消、已终态的传输不涉及交付停滞。
		if t.SHA256 == "" || isTerminalTransferStatus(t.Status) {
			continue
		}
		changed := false
		for _, target := range t.Targets {
			if target.Status != models.FileTargetDelivering {
				continue
			}
			since := deliverySince(target, t)
			if since.IsZero() || now.Sub(since) < deliveryStallTimeout(t.Size) {
				continue
			}
			target.Status = models.FileTargetFailed
			target.Error = fmt.Sprintf("delivery timed out after %s without a result from the target agent",
				deliveryStallTimeout(t.Size).Truncate(time.Second))
			if err := m.store.UpdateFileTransferTarget(ctx, target); err != nil {
				return stalled, err
			}
			stalled++
			changed = true
			m.logger.Warn("file delivery timed out; target marked failed",
				"transfer", t.ID, "node", target.NodeID, "since", since)
		}
		if !changed {
			continue
		}
		if err := m.updateTransferAggregate(ctx, t); err != nil {
			return stalled, err
		}
	}
	return stalled, nil
}

// deliverySince 返回目标进入当前状态的参考时刻；全部时间缺失时返回零值（不判定停滞）。
func deliverySince(target *models.FileTransferTarget, t *models.FileTransfer) time.Time {
	if !target.UpdatedAt.IsZero() {
		return target.UpdatedAt
	}
	if !t.UpdatedAt.IsZero() {
		return t.UpdatedAt
	}
	return t.CreatedAt
}

// SweepStalledUploads 把超过上传停滞阈值仍无进展的 UPLOADING 传输终结为 FAILED，
// 并同步终结未暂存传输的全部非终态目标（释放节点删除）。返回被终结的传输数。
// now 由调用方注入，便于测试确定性地跨过时限。
//
// 覆盖场景（P15 评审 major-02）：当前上传尝试失败但结果丢失（Agent 进程被杀、
// 网络中断未触发回报），且没有下一次重连触发 ResumeNode 重投。被取代的旧尝试结果
// 仍按世代号忽略（不误报），真失败/无进展由本兜底确定性终态化。
func (m *FileTransferManager) SweepStalledUploads(ctx context.Context, now time.Time) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	transfers, err := m.store.ListFileTransfers(ctx)
	if err != nil {
		return 0, err
	}
	stalled := 0
	for _, t := range transfers {
		// 仅 UPLOADING（尝试已下发）：PENDING 是“等节点重连”的正常可恢复状态；
		// 已暂存/终态传输不涉及源上传停滞。
		if t.Status != models.FileTransferUploading || t.SHA256 != "" {
			continue
		}
		since := uploadProgressSince(t, m.partPath(t.ID))
		if since.IsZero() || now.Sub(since) < uploadStallTimeout(t.Size) {
			continue
		}
		message := fmt.Sprintf("source upload timed out after %s without progress from the source agent",
			uploadStallTimeout(t.Size).Truncate(time.Second))
		if err := m.failTransfer(ctx, t, message); err != nil {
			return stalled, err
		}
		stalled++
		m.logger.Warn("source upload timed out; transfer marked failed",
			"transfer", t.ID, "node", t.SourceNodeID, "since", since)
	}
	return stalled, nil
}

// uploadProgressSince 返回源上传最近一次进展的参考时刻：part 文件 mtime 与传输
// UpdatedAt（最近一次下发/分片写入/完成）取较新者；全部缺失时返回零值（不判定停滞）。
func uploadProgressSince(t *models.FileTransfer, partPath string) time.Time {
	ref := t.UpdatedAt
	if info, err := os.Stat(partPath); err == nil && info.Mode().IsRegular() && info.ModTime().After(ref) {
		ref = info.ModTime()
	}
	if ref.IsZero() {
		ref = t.CreatedAt
	}
	return ref
}

// Create 创建传输并尽快通知源 Agent。
func (m *FileTransferManager) Create(ctx context.Context, sourceNodeID, sourcePath string, targets []FileTransferTargetRequest) (*models.FileTransfer, error) {
	if err := validateTransferPath(sourcePath); err != nil {
		return nil, fmt.Errorf("source path: %w", err)
	}
	if sourceNodeID == "" {
		return nil, errors.New("source node is required")
	}
	if _, err := m.nodes.GetNode(ctx, sourceNodeID); err != nil {
		return nil, fmt.Errorf("source node not found")
	}
	if len(targets) == 0 {
		return nil, errors.New("at least one target is required")
	}
	seen := make(map[string]bool, len(targets))
	t := &models.FileTransfer{ID: uuid.NewString(), SourceNodeID: sourceNodeID, SourcePath: sourcePath}
	for _, req := range targets {
		if req.NodeID == "" || seen[req.NodeID] {
			return nil, errors.New("target node is empty or duplicated")
		}
		// 服务端自传拒绝：源与目标同节点没有中继意义，且会与源侧暂存/投递路径
		// 相互覆盖（缺陷 OBS-20261017-XFER-API-NO-SELF-TRANSFER-CHECK，此前仅前端裁剪）。
		if req.NodeID == sourceNodeID {
			return nil, fmt.Errorf("self transfer is not allowed: target node %s is the source node", req.NodeID)
		}
		if err := validateTransferPath(req.DestinationPath); err != nil {
			return nil, fmt.Errorf("destination path: %w", err)
		}
		if _, err := m.nodes.GetNode(ctx, req.NodeID); err != nil {
			return nil, fmt.Errorf("target node %s not found", req.NodeID)
		}
		seen[req.NodeID] = true
		t.Targets = append(t.Targets, &models.FileTransferTarget{
			TransferID: t.ID, NodeID: req.NodeID, DestinationPath: req.DestinationPath, Mode: req.Mode,
			Status: models.FileTargetPending,
		})
	}
	if err := m.store.CreateFileTransfer(ctx, t); err != nil {
		return nil, err
	}
	m.dispatchSource(ctx, t)
	return m.store.GetFileTransfer(ctx, t.ID)
}

// Get 获取传输详情。
func (m *FileTransferManager) Get(ctx context.Context, id string) (*models.FileTransfer, error) {
	return m.store.GetFileTransfer(ctx, id)
}

// List 获取传输列表。
func (m *FileTransferManager) List(ctx context.Context) ([]*models.FileTransfer, error) {
	return m.store.ListFileTransfers(ctx)
}

// dispatchSource 下发源上传指令。
//
// 不持有 m.mu：dispatch 由 Agent 读循环（ResumeNode 回调）触发，而 handleUpload
// 在整个上传请求期间持有 m.mu；若在此抢锁，慢速大文件上传会把 Agent 读循环
// （心跳/结果处理）阻塞到上传结束。终态保护改用「下发前以库内最新状态为准」：
// 调用方（ResumeNode 等）可能先读取再下发，期间 Cancel 等已把传输写入终态，
// 陈旧实例不得复活终态（CANCELED 之后不得复活）。
func (m *FileTransferManager) dispatchSource(ctx context.Context, t *models.FileTransfer) {
	fresh, err := m.store.GetFileTransfer(ctx, t.ID)
	if err != nil {
		return
	}
	t = fresh
	if isTerminalTransferStatus(t.Status) || t.SHA256 != "" {
		return
	}
	// 暂停拦截：maintenance/disabled 节点不下发传输指令（settings/变更通知不受影响）
	if node, err := m.store.GetNode(ctx, t.SourceNodeID); err == nil && !isDispatchableStatus(node.Status) {
		if m.logger != nil {
			m.logger.Info("file transfer upload dispatch skipped: node paused",
				"transfer", t.ID, "node", t.SourceNodeID, "status", node.Status)
		}
		return
	}
	conn, ok := m.sessions.Get(t.SourceNodeID)
	if !ok {
		return
	}
	offset := m.partSize(t.ID)
	t.Status = models.FileTransferUploading
	t.Error = ""
	if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
		return
	}
	// 每次实际下发分配新世代号：被取代的旧尝试回执因世代不匹配被忽略。
	attempt := m.nextUploadAttempt(t.ID)
	if err := conn.Send(protocol.NewEnvelope(protocol.MsgFileUploadRequest, t.ID, protocol.FileUploadRequestPayload{
		TransferID: t.ID, SourcePath: t.SourcePath,
		UploadURL: m.dataURL("/agent/transfers/" + t.ID + "/upload"), Offset: offset, MaxBytes: m.maxBytes,
		Attempt: attempt,
	})); err != nil {
		t.Status = models.FileTransferPending
		t.Error = err.Error()
		_ = m.store.UpdateFileTransfer(ctx, t)
	}
}

// ResumeNode 在 Agent 重连后恢复源上传和目标交付。
func (m *FileTransferManager) ResumeNode(ctx context.Context, nodeID string) {
	transfers, err := m.store.ListFileTransfers(ctx)
	if err != nil {
		return
	}
	for _, t := range transfers {
		if t.SourceNodeID == nodeID && (t.Status == models.FileTransferPending || t.Status == models.FileTransferUploading) {
			m.dispatchSource(ctx, t)
		}
		// 终态（含 FAILED）不再自动重投：恢复路径只能是显式 Retry（终态冻结）。
		if t.SHA256 == "" || isTerminalTransferStatus(t.Status) {
			continue
		}
		for _, target := range t.Targets {
			if target.NodeID == nodeID && (target.Status == models.FileTargetPending || target.Status == models.FileTargetDelivering) {
				m.dispatchTarget(ctx, t, target)
			}
		}
	}
}

// dispatchTarget 下发目标交付指令。不持有 m.mu（同 dispatchSource 的理由），
// 以库内最新传输/目标状态为准，终态或已成功/已取消的目标不重投。
func (m *FileTransferManager) dispatchTarget(ctx context.Context, t *models.FileTransfer, target *models.FileTransferTarget) {
	fresh, err := m.store.GetFileTransfer(ctx, t.ID)
	if err != nil {
		return
	}
	t = fresh
	if isTerminalTransferStatus(t.Status) || t.SHA256 == "" {
		return
	}
	var current *models.FileTransferTarget
	for _, item := range t.Targets {
		if item.NodeID == target.NodeID {
			current = item
			break
		}
	}
	if current == nil || isTerminalTargetStatus(current.Status) {
		return
	}
	target = current
	// 暂停拦截：maintenance/disabled 节点不下发交付指令
	if node, err := m.store.GetNode(ctx, target.NodeID); err == nil && !isDispatchableStatus(node.Status) {
		if m.logger != nil {
			m.logger.Info("file transfer delivery dispatch skipped: node paused",
				"transfer", t.ID, "node", target.NodeID, "status", node.Status)
		}
		return
	}
	conn, ok := m.sessions.Get(target.NodeID)
	if !ok {
		return
	}
	target.Status = models.FileTargetDelivering
	target.Error = ""
	if err := m.store.UpdateFileTransferTarget(ctx, target); err != nil {
		return
	}
	mode := target.Mode
	if mode == 0 {
		mode = t.SourceMode
	}
	if mode == 0 {
		mode = 0o644
	}
	// 每次实际下发分配新世代号：被取代的旧下载尝试回执因世代不匹配被忽略。
	attempt := m.nextDeliveryAttempt(deliveryAttemptKey(t.ID, target.NodeID))
	if err := conn.Send(protocol.NewEnvelope(protocol.MsgFileDeliveryRequest, t.ID, protocol.FileDeliveryRequestPayload{
		TransferID: t.ID, DownloadURL: m.dataURL("/agent/transfers/" + t.ID + "/download"),
		SHA256: t.SHA256, Size: t.Size, DestinationPath: target.DestinationPath, Mode: mode,
		Attempt: attempt,
	})); err != nil {
		target.Status = models.FileTargetPending
		target.Error = err.Error()
		_ = m.store.UpdateFileTransferTarget(ctx, target)
	}
}

// HandleAgentHTTP 处理 Agent Gateway 上的文件数据请求。
func (m *FileTransferManager) HandleAgentHTTP(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/agent/transfers/")
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) != 2 || parts[0] == "" {
		writeTransferJSON(w, http.StatusNotFound, map[string]string{"error": "transfer endpoint not found"})
		return
	}
	id, action := parts[0], parts[1]
	agentID := r.Header.Get("X-NodeSteer-Agent-ID")
	token := r.Header.Get("X-NodeSteer-Agent-Token")
	if agentID == "" || token == "" {
		writeTransferJSON(w, http.StatusUnauthorized, map[string]string{"error": "agent authentication required"})
		return
	}
	node, err := m.nodes.store.GetNodeByAgentID(r.Context(), agentID)
	if err != nil || !m.nodes.AuthenticateAgent(r.Context(), node.ID, token) {
		writeTransferJSON(w, http.StatusUnauthorized, map[string]string{"error": "invalid agent credential"})
		return
	}
	switch action {
	case "upload":
		if r.Method != http.MethodPost {
			// 405 必须携带 Allow（与 Hub 侧 methodNotAllowed 风格一致；
			// 缺陷 P6 范围外观察：agent 文件传输端点 405 缺 Allow）。
			w.Header().Set("Allow", "POST")
			writeTransferJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
			return
		}
		m.handleUpload(w, r, id, node.ID)
	case "download":
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", "GET")
			writeTransferJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
			return
		}
		m.handleDownload(w, r, id, node.ID)
	default:
		writeTransferJSON(w, http.StatusNotFound, map[string]string{"error": "transfer endpoint not found"})
	}
}

func (m *FileTransferManager) handleUpload(w http.ResponseWriter, r *http.Request, id, nodeID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	t, err := m.store.GetFileTransfer(r.Context(), id)
	if err != nil {
		writeTransferJSON(w, http.StatusNotFound, map[string]string{"error": "transfer not found"})
		return
	}
	if t.SourceNodeID != nodeID {
		writeTransferJSON(w, http.StatusForbidden, map[string]string{"error": "agent is not the transfer source"})
		return
	}
	if t.Status == models.FileTransferCanceled {
		writeTransferJSON(w, http.StatusConflict, map[string]string{"error": "transfer canceled"})
		return
	}
	// 终态冻结（先于“已暂存”早退）：SUCCESS/FAILED 一律拒绝续传，
	// 不依赖 SHA256 是否残留——failTransfer 会清空暂存标记，若把终态判断放在
	// SHA 早退之后，FAILED 传输仍可能接受分片并写回 UPLOADING（P15 评审 major-01(1)）。
	if isTerminalTransferStatus(t.Status) {
		m.logTerminalWriteRejected(t, "upload_http")
		writeTransferJSON(w, http.StatusGone, map[string]string{"error": "transfer is in terminal state " + t.Status})
		return
	}
	if t.SHA256 != "" {
		writeTransferJSON(w, http.StatusOK, map[string]any{"status": t.Status, "size": t.Size, "sha256": t.SHA256})
		return
	}
	total, err := strconv.ParseInt(r.Header.Get("X-NodeSteer-File-Size"), 10, 64)
	if err != nil || total < 0 || total > m.maxBytes {
		writeTransferJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid file size"})
		return
	}
	if value := r.Header.Get("X-NodeSteer-File-Mode"); value != "" {
		mode, parseErr := strconv.ParseUint(value, 8, 32)
		if parseErr != nil {
			writeTransferJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid file mode"})
			return
		}
		t.SourceMode = uint32(mode)
	}
	offset, err := parseUploadOffset(r.Header.Get("Content-Range"))
	if err != nil {
		writeTransferJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	part := m.partPath(id)
	current := m.partSize(id)
	if current != offset {
		writeTransferJSON(w, http.StatusConflict, map[string]any{"error": "upload offset mismatch", "next_offset": current})
		return
	}
	expected := total - offset
	if r.ContentLength >= 0 && r.ContentLength != expected {
		writeTransferJSON(w, http.StatusBadRequest, map[string]string{"error": "content length mismatch"})
		return
	}
	if err := os.MkdirAll(filepath.Dir(part), 0o700); err != nil {
		writeTransferJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	f, err := os.OpenFile(part, os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		writeTransferJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	if _, err := f.Seek(offset, io.SeekStart); err != nil {
		f.Close()
		writeTransferJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	n, copyErr := io.CopyN(f, r.Body, expected)
	syncErr := f.Sync()
	closeErr := f.Close()
	if copyErr != nil && !errors.Is(copyErr, io.EOF) {
		writeTransferJSON(w, http.StatusInternalServerError, map[string]string{"error": copyErr.Error()})
		return
	}
	if syncErr != nil || closeErr != nil {
		writeTransferJSON(w, http.StatusInternalServerError, map[string]string{"error": "failed to persist upload"})
		return
	}
	next := offset + n
	if next < total {
		// 分片续传写回 UPLOADING 前重新做终态判定（纵深防御：终态不得被写回进行中）。
		if !m.markUploading(r.Context(), t) {
			writeTransferJSON(w, http.StatusGone, map[string]string{"error": "transfer is in terminal state " + t.Status})
			return
		}
		w.Header().Set("X-NodeSteer-Next-Offset", strconv.FormatInt(next, 10))
		writeTransferJSON(w, http.StatusConflict, map[string]any{"status": t.Status, "next_offset": next})
		return
	}
	if err := m.completeUpload(r.Context(), t, total); err != nil {
		if errors.Is(err, errTransferTerminal) {
			m.logTerminalWriteRejected(t, "complete_upload")
			writeTransferJSON(w, http.StatusGone, map[string]string{"error": "transfer is in terminal state " + t.Status})
			return
		}
		// 校验/暂存失败必须终态化：否则传输停留 UPLOADING、目标停留 PENDING，
		// 源/目标节点删除会被 409 has active file transfer 永久阻断（缺陷 FAIL-M-001）。
		message := sourceUploadErrorMessage(err.Error())
		if failErr := m.failTransfer(r.Context(), t, message); failErr != nil {
			m.logger.Warn("persist terminal transfer failed", "transfer", t.ID, "error", failErr)
		}
		writeTransferJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": message})
		return
	}
	writeTransferJSON(w, http.StatusOK, map[string]any{"status": t.Status, "size": t.Size, "sha256": t.SHA256})
}

// markUploading 在非终态前提下把传输写回 UPLOADING（分片续传进度落库）。
// 终态返回 false 且不写入：failTransfer 会清空 SHA256，分片路径不得据此绕过终态冻结
// （P15 评审 major-01(1)）。
func (m *FileTransferManager) markUploading(ctx context.Context, t *models.FileTransfer) bool {
	if isTerminalTransferStatus(t.Status) {
		m.logTerminalWriteRejected(t, "upload_partial")
		return false
	}
	t.Status = models.FileTransferUploading
	_ = m.store.UpdateFileTransfer(ctx, t)
	return true
}

func (m *FileTransferManager) completeUpload(ctx context.Context, t *models.FileTransfer, total int64) error {
	// 终态冻结（纵深防御）：终态传输不得被续传完成复活为 DELIVERING。
	// 校验必须先于任何落盘副作用（part→blob rename），避免为终态传输暂存文件。
	if isTerminalTransferStatus(t.Status) {
		m.logTerminalWriteRejected(t, "complete_upload")
		return errTransferTerminal
	}
	part := m.partPath(t.ID)
	info, err := os.Stat(part)
	if err != nil || !info.Mode().IsRegular() || info.Size() != total {
		return errors.New("uploaded file size verification failed")
	}
	sha, err := hashFile(part)
	if err != nil {
		return err
	}
	blob := m.blobPath(t.ID)
	if err := os.Rename(part, blob); err != nil {
		return err
	}
	t.Size, t.SHA256, t.Status, t.Error = total, sha, models.FileTransferDelivering, ""
	if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
		_ = os.Rename(blob, part)
		return err
	}
	for _, target := range t.Targets {
		m.dispatchTarget(ctx, t, target)
	}
	return nil
}

func (m *FileTransferManager) handleDownload(w http.ResponseWriter, r *http.Request, id, nodeID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	t, err := m.store.GetFileTransfer(r.Context(), id)
	if err != nil || t.SHA256 == "" {
		writeTransferJSON(w, http.StatusNotFound, map[string]string{"error": "staged transfer not found"})
		return
	}
	allowed := false
	for _, target := range t.Targets {
		if target.NodeID == nodeID && target.Status != models.FileTargetCanceled {
			allowed = true
			break
		}
	}
	if !allowed {
		writeTransferJSON(w, http.StatusForbidden, map[string]string{"error": "agent is not a transfer target"})
		return
	}
	f, err := os.Open(m.blobPath(id))
	if err != nil {
		writeTransferJSON(w, http.StatusNotFound, map[string]string{"error": "staged file not found"})
		return
	}
	defer f.Close()
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("X-NodeSteer-File-SHA256", t.SHA256)
	w.Header().Set("Content-Length", strconv.FormatInt(t.Size, 10))
	http.ServeContent(w, r, filepath.Base(t.SourcePath), t.UpdatedAt, f)
}

// HandleUploadResult records a source Agent error when the HTTP upload failed.
func (m *FileTransferManager) HandleUploadResult(ctx context.Context, nodeID string, p protocol.FileUploadResultPayload) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if p.OK {
		return nil
	}
	t, err := m.store.GetFileTransfer(ctx, p.TransferID)
	if err != nil {
		return err
	}
	if t.SourceNodeID != nodeID {
		return nil
	}
	// 终态冻结：已取消/已成功/已失败或源文件已暂存的传输忽略迟到的失败结果。
	if isTerminalTransferStatus(t.Status) {
		m.logTerminalWriteRejected(t, "upload_result")
		return nil
	}
	if t.SHA256 != "" {
		return nil
	}
	// 世代匹配：只接受最近一次下发尝试的结果。断连重连时旧尝试被取消
	// （context canceled），其失败回执不得把仍在续传的传输误判为 FAILED（缺陷 SOAK-20261018-01）。
	if current := m.currentUploadAttempt(t.ID); p.Attempt != current {
		m.logStaleAttemptIgnored(t, "upload_result", p.Attempt, current)
		return nil
	}
	return m.failTransfer(ctx, t, sourceUploadErrorMessage(p.Error))
}

// failTransfer 将传输终结为 FAILED 并同步终结所有非终态目标，返回首个持久化错误。
func (m *FileTransferManager) failTransfer(ctx context.Context, t *models.FileTransfer, message string) error {
	// 终态冻结（纵深防御）：终态不可回退，迟到失败不得改写既有终态。
	if isTerminalTransferStatus(t.Status) {
		m.logTerminalWriteRejected(t, "fail_transfer")
		return nil
	}
	// completeUpload 可能在持久化失败前已把暂存元数据写进内存；
	// 终态传输不得携带已回滚的 blob 信息，否则交付/重试路径会按“已暂存”处理。
	t.Status, t.Error, t.SHA256 = models.FileTransferFailed, message, ""
	if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
		return err
	}
	// 源文件未暂存时不会再有交付，必须终结所有非终态目标，否则目标悬挂在 PENDING。
	return m.failTargets(ctx, t, message)
}

// failTargets 将传输中所有非终态目标置为 FAILED 并持久化，返回首个持久化错误。
func (m *FileTransferManager) failTargets(ctx context.Context, t *models.FileTransfer, message string) error {
	var firstErr error
	for _, target := range t.Targets {
		if isTerminalTargetStatus(target.Status) {
			continue
		}
		target.Status, target.Error = models.FileTargetFailed, message
		if err := m.store.UpdateFileTransferTarget(ctx, target); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

func sourceUploadErrorMessage(reason string) string {
	reason = strings.TrimSpace(reason)
	if reason == "" {
		return "source upload failed"
	}
	return "source upload failed: " + reason
}

// HandleDeliveryResult records one target result and updates aggregate state.
func (m *FileTransferManager) HandleDeliveryResult(ctx context.Context, nodeID string, p protocol.FileDeliveryResultPayload) error {
	m.mu.Lock()
	defer m.mu.Unlock()

	t, err := m.store.GetFileTransfer(ctx, p.TransferID)
	if err != nil {
		return err
	}
	var target *models.FileTransferTarget
	for _, item := range t.Targets {
		if item.NodeID == nodeID {
			target = item
			break
		}
	}
	if target == nil {
		return nil
	}
	// 目标级终态冻结：已成功/已失败/已取消的目标不得被迟到结果改写（含同世代重复回执）。
	if isTerminalTargetStatus(target.Status) {
		m.logTerminalTargetWriteRejected(t, target, "delivery_result")
		return nil
	}
	// 传输级终态冻结：已取消/已成功/已失败（含源失败未暂存）的传输忽略迟到的目标结果。
	if isTerminalTransferStatus(t.Status) {
		m.logTerminalWriteRejected(t, "delivery_result")
		return nil
	}
	if t.SHA256 == "" {
		// 源文件未暂存（例如源上传失败）时不可能有合法交付，忽略迟到的目标结果。
		return nil
	}
	// 世代匹配：只接受最近一次下发交付尝试的结果，被取代的旧下载尝试
	// （断连重连被 cancel）的失败回执不得把目标/传输误判为 FAILED（SOAK-20261018-01 同源）。
	key := deliveryAttemptKey(t.ID, nodeID)
	if current := m.currentDeliveryAttempt(key); p.Attempt != current {
		m.logStaleAttemptIgnored(t, "delivery_result", p.Attempt, current)
		return nil
	}
	if p.OK {
		target.Status, target.Error = models.FileTargetSuccess, ""
	} else {
		target.Status, target.Error = models.FileTargetFailed, p.Error
	}
	if err := m.store.UpdateFileTransferTarget(ctx, target); err != nil {
		return err
	}
	// 内存中的目标对象与切片元素可能不是同一实例，聚合前先同步。
	for _, item := range t.Targets {
		if item.NodeID == target.NodeID {
			item.Status, item.Error = target.Status, target.Error
		}
	}
	return m.updateTransferAggregate(ctx, t)
}

// updateTransferAggregate 依据各目标状态推进传输聚合状态并持久化：
// 全部成功 → SUCCESS；全部终态且存在失败 → FAILED；否则仍为 DELIVERING。
func (m *FileTransferManager) updateTransferAggregate(ctx context.Context, t *models.FileTransfer) error {
	// 终态冻结（纵深防御）：聚合不得改写既有终态。
	if isTerminalTransferStatus(t.Status) {
		m.logTerminalWriteRejected(t, "aggregate")
		return nil
	}
	allSuccess, allTerminal, anyFailed := true, true, false
	for _, item := range t.Targets {
		switch item.Status {
		case models.FileTargetSuccess:
		case models.FileTargetFailed, models.FileTargetCanceled:
			allSuccess, anyFailed = false, true
		default:
			allSuccess, allTerminal = false, false
		}
	}
	if allSuccess {
		t.Status, t.Error = models.FileTransferSuccess, ""
	} else if allTerminal && anyFailed {
		t.Status = models.FileTransferFailed
	} else {
		t.Status = models.FileTransferDelivering
	}
	if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
		return err
	}
	if t.Status == models.FileTransferSuccess {
		m.cleanupFiles(t.ID)
	}
	return nil
}

// Retry resets failed targets, or restarts an incomplete source upload.
// Retry 是终态冻结的唯一显式恢复路径：FAILED 可被重试，CANCELED 不可。
func (m *FileTransferManager) Retry(ctx context.Context, id, targetID string) (*models.FileTransfer, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	t, err := m.store.GetFileTransfer(ctx, id)
	if err != nil {
		return nil, err
	}
	if t.Status == models.FileTransferCanceled {
		return nil, errors.New("transfer canceled")
	}
	if t.Status == models.FileTransferSuccess {
		// 成功是终态：重试不得把它拉回上传/交付（无目标可重启时保持原状态）。
		return t, nil
	}
	if t.SHA256 == "" {
		t.Status, t.Error = models.FileTransferPending, ""
		if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
			return nil, err
		}
		m.dispatchSource(ctx, t)
		return m.store.GetFileTransfer(ctx, id)
	}
	// 先重置并持久化目标与传输状态，再下发指令：dispatch 会以库内状态为准，
	// 避免在传输仍是 FAILED 时下发交付（终态冻结会拒绝）。
	var restarted []*models.FileTransferTarget
	for _, target := range t.Targets {
		if targetID == "" || target.NodeID == targetID {
			if target.Status == models.FileTargetFailed {
				target.Status, target.Error = models.FileTargetPending, ""
				if err := m.store.UpdateFileTransferTarget(ctx, target); err != nil {
					return nil, err
				}
				restarted = append(restarted, target)
			}
		}
	}
	if len(restarted) == 0 {
		// 没有目标可重启（例如误重试已成功的传输）时保持原状态，避免把已终结的传输推进为 DELIVERING 后永久悬挂。
		return m.store.GetFileTransfer(ctx, id)
	}
	t.Status, t.Error = models.FileTransferDelivering, ""
	if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
		return nil, err
	}
	for _, target := range restarted {
		m.dispatchTarget(ctx, t, target)
	}
	return m.store.GetFileTransfer(ctx, id)
}

// Cancel cancels pending work and notifies active Agents.
//
// 终态语义（P15 评审 major-01(2)）：终态不可被取消改写——CANCELED 幂等返回
// （不重复下发取消、不重复清理）；SUCCESS/FAILED 返回业务错误（已结束的传输不允许
// 改写为取消，恢复路径是显式 Retry）。仅非终态传输可进入 CANCELED。
func (m *FileTransferManager) Cancel(ctx context.Context, id string) (*models.FileTransfer, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	t, err := m.store.GetFileTransfer(ctx, id)
	if err != nil {
		return nil, err
	}
	switch t.Status {
	case models.FileTransferCanceled:
		return t, nil
	case models.FileTransferSuccess, models.FileTransferFailed:
		m.logTerminalWriteRejected(t, "cancel")
		return nil, errors.New("transfer already finished: " + t.Status)
	}
	t.Status, t.Error = models.FileTransferCanceled, ""
	if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
		return nil, err
	}
	for _, target := range t.Targets {
		if target.Status != models.FileTargetSuccess {
			target.Status = models.FileTargetCanceled
			_ = m.store.UpdateFileTransferTarget(ctx, target)
		}
		if conn, ok := m.sessions.Get(target.NodeID); ok {
			_ = conn.Send(protocol.NewEnvelope(protocol.MsgFileTransferCancel, id, protocol.FileTransferCancelPayload{TransferID: id}))
		}
	}
	if conn, ok := m.sessions.Get(t.SourceNodeID); ok {
		_ = conn.Send(protocol.NewEnvelope(protocol.MsgFileTransferCancel, id, protocol.FileTransferCancelPayload{TransferID: id}))
	}
	m.cleanupFiles(id)
	return m.store.GetFileTransfer(ctx, id)
}

func (m *FileTransferManager) cleanupFiles(id string) {
	_ = os.Remove(m.partPath(id))
	_ = os.Remove(m.blobPath(id))
}

func (m *FileTransferManager) partPath(id string) string {
	return filepath.Join(m.storageDir, id+".part")
}
func (m *FileTransferManager) blobPath(id string) string {
	return filepath.Join(m.storageDir, id+".blob")
}
func (m *FileTransferManager) partSize(id string) int64 {
	info, err := os.Stat(m.partPath(id))
	if err != nil || !info.Mode().IsRegular() {
		return 0
	}
	return info.Size()
}
func (m *FileTransferManager) dataURL(path string) string {
	return strings.TrimRight(m.gatewayBaseURL, "/") + path
}

func validateTransferPath(path string) error {
	if path == "" || !filepath.IsAbs(path) || strings.ContainsRune(path, 0) {
		return errors.New("absolute path required")
	}
	if filepath.Clean(path) != path && strings.Contains(path, "..") {
		return errors.New("path traversal is not allowed")
	}
	return nil
}

func parseUploadOffset(value string) (int64, error) {
	if value == "" {
		return 0, nil
	}
	var start, end, total int64
	if _, err := fmt.Sscanf(value, "bytes %d-%d/%d", &start, &end, &total); err != nil || start < 0 || end < start || total < 0 {
		return 0, errors.New("invalid content range")
	}
	if end >= total && total != 0 {
		return 0, errors.New("content range exceeds file size")
	}
	return start, nil
}

func hashFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func normalizeHTTPBaseURL(value string) string {
	value = strings.TrimRight(value, "/")
	value = strings.TrimPrefix(value, "ws://")
	if strings.HasPrefix(value, "wss://") {
		value = "https://" + strings.TrimPrefix(value, "wss://")
	} else if !strings.HasPrefix(value, "http://") && !strings.HasPrefix(value, "https://") {
		value = "http://" + value
	}
	return value
}

func writeTransferJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
