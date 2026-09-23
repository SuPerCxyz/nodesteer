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

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/protocol"
	"github.com/cadentra/cadentra/internal/store"
	"github.com/google/uuid"
)

const defaultMaxFileTransferBytes = int64(10 << 30)

// ResolveGatewayBaseURL 返回 Agent 可访问的 Gateway HTTP 基址。
func ResolveGatewayBaseURL(configured, webBaseURL, gatewayAddr string) string {
	if configured != "" {
		return normalizeHTTPBaseURL(configured)
	}
	u, err := url.Parse(webBaseURL)
	if err != nil || u.Hostname() == "" {
		return "http://localhost:8443"
	}
	port := "8443"
	if _, configuredPort, splitErr := net.SplitHostPort(gatewayAddr); splitErr == nil && configuredPort != "" {
		port = configuredPort
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
	}, nil
}

// SetMaxBytes 设置单文件传输上限。
func (m *FileTransferManager) SetMaxBytes(n int64) {
	if n > 0 {
		m.maxBytes = n
	}
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

func (m *FileTransferManager) dispatchSource(ctx context.Context, t *models.FileTransfer) {
	if t.Status == models.FileTransferSuccess || t.Status == models.FileTransferFailed || t.Status == models.FileTransferCanceled || t.SHA256 != "" {
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
	if err := conn.Send(protocol.NewEnvelope(protocol.MsgFileUploadRequest, t.ID, protocol.FileUploadRequestPayload{
		TransferID: t.ID, SourcePath: t.SourcePath,
		UploadURL: m.dataURL("/agent/transfers/" + t.ID + "/upload"), Offset: offset, MaxBytes: m.maxBytes,
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
		if t.SHA256 == "" || t.Status == models.FileTransferCanceled || t.Status == models.FileTransferSuccess {
			continue
		}
		for _, target := range t.Targets {
			if target.NodeID == nodeID && (target.Status == models.FileTargetPending || target.Status == models.FileTargetDelivering) {
				m.dispatchTarget(ctx, t, target)
			}
		}
	}
}

func (m *FileTransferManager) dispatchTarget(ctx context.Context, t *models.FileTransfer, target *models.FileTransferTarget) {
	if t.SHA256 == "" || target.Status == models.FileTargetSuccess || target.Status == models.FileTargetCanceled || t.Status == models.FileTransferCanceled {
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
	if err := conn.Send(protocol.NewEnvelope(protocol.MsgFileDeliveryRequest, t.ID, protocol.FileDeliveryRequestPayload{
		TransferID: t.ID, DownloadURL: m.dataURL("/agent/transfers/" + t.ID + "/download"),
		SHA256: t.SHA256, Size: t.Size, DestinationPath: target.DestinationPath, Mode: mode,
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
	agentID := r.Header.Get("X-Cadentra-Agent-ID")
	token := r.Header.Get("X-Cadentra-Agent-Token")
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
			writeTransferJSON(w, http.StatusMethodNotAllowed, map[string]string{"error": "method not allowed"})
			return
		}
		m.handleUpload(w, r, id, node.ID)
	case "download":
		if r.Method != http.MethodGet {
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
	if t.SHA256 != "" {
		writeTransferJSON(w, http.StatusOK, map[string]any{"status": t.Status, "size": t.Size, "sha256": t.SHA256})
		return
	}
	total, err := strconv.ParseInt(r.Header.Get("X-Cadentra-File-Size"), 10, 64)
	if err != nil || total < 0 || total > m.maxBytes {
		writeTransferJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid file size"})
		return
	}
	if value := r.Header.Get("X-Cadentra-File-Mode"); value != "" {
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
		t.Status = models.FileTransferUploading
		_ = m.store.UpdateFileTransfer(r.Context(), t)
		w.Header().Set("X-Cadentra-Next-Offset", strconv.FormatInt(next, 10))
		writeTransferJSON(w, http.StatusConflict, map[string]any{"status": t.Status, "next_offset": next})
		return
	}
	if err := m.completeUpload(r.Context(), t, total); err != nil {
		writeTransferJSON(w, http.StatusUnprocessableEntity, map[string]string{"error": err.Error()})
		return
	}
	writeTransferJSON(w, http.StatusOK, map[string]any{"status": t.Status, "size": t.Size, "sha256": t.SHA256})
}

func (m *FileTransferManager) completeUpload(ctx context.Context, t *models.FileTransfer, total int64) error {
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
	w.Header().Set("X-Cadentra-File-SHA256", t.SHA256)
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
	// 已取消、已成功或源文件已暂存的传输忽略迟到的失败结果，避免回退终态。
	if t.SourceNodeID != nodeID || t.Status == models.FileTransferCanceled || t.Status == models.FileTransferSuccess || t.SHA256 != "" {
		return nil
	}
	message := sourceUploadErrorMessage(p.Error)
	t.Status, t.Error = models.FileTransferFailed, message
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
		switch target.Status {
		case models.FileTargetSuccess, models.FileTargetFailed, models.FileTargetCanceled:
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
	if target == nil || t.Status == models.FileTransferCanceled || t.SHA256 == "" {
		// 源文件未暂存（例如源上传失败）时不可能有合法交付，忽略迟到的目标结果。
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
	allSuccess, allTerminal, anyFailed := true, true, false
	for _, item := range t.Targets {
		if item.NodeID == target.NodeID {
			item.Status, item.Error = target.Status, target.Error
		}
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
	if t.SHA256 == "" {
		t.Status, t.Error = models.FileTransferPending, ""
		if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
			return nil, err
		}
		m.dispatchSource(ctx, t)
		return m.store.GetFileTransfer(ctx, id)
	}
	restarted := false
	for _, target := range t.Targets {
		if targetID == "" || target.NodeID == targetID {
			if target.Status == models.FileTargetFailed {
				target.Status, target.Error = models.FileTargetPending, ""
				if err := m.store.UpdateFileTransferTarget(ctx, target); err != nil {
					return nil, err
				}
				m.dispatchTarget(ctx, t, target)
				restarted = true
			}
		}
	}
	if !restarted {
		// 没有目标可重启（例如误重试已成功的传输）时保持原状态，避免把已终结的传输推进为 DELIVERING 后永久悬挂。
		return m.store.GetFileTransfer(ctx, id)
	}
	t.Status, t.Error = models.FileTransferDelivering, ""
	if err := m.store.UpdateFileTransfer(ctx, t); err != nil {
		return nil, err
	}
	return m.store.GetFileTransfer(ctx, id)
}

// Cancel cancels pending work and notifies active Agents.
func (m *FileTransferManager) Cancel(ctx context.Context, id string) (*models.FileTransfer, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	t, err := m.store.GetFileTransfer(ctx, id)
	if err != nil {
		return nil, err
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
