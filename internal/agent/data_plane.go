package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

const (
	inboundGrantTTL           = 5 * time.Minute
	maxInboundArtifactBytes   = int64(10 << 30)
	maxInboundAgentBinarySize = int64(256 << 20)
)

type inboundGrantKind string

const (
	inboundGrantSource inboundGrantKind = "source"
	inboundGrantTarget inboundGrantKind = "target"
)

type inboundGrant struct {
	kind      inboundGrantKind
	source    string
	dest      string
	size      int64
	mode      os.FileMode
	sha256    string
	attempt   uint64
	expiresAt time.Time
}

// inboundDataPlane Agent 入站 HTTPS 数据面业务处理。鉴权、mTLS、token、session_id
// 与 node_id 由 connection.InboundListener 完成；这里只处理已授权请求。
type inboundDataPlane struct {
	agent  *Agent
	mu     sync.Mutex
	grants map[string]*inboundGrant
}

func newInboundDataPlane(a *Agent) *inboundDataPlane {
	return &inboundDataPlane{agent: a, grants: map[string]*inboundGrant{}}
}

func (d *inboundDataPlane) close() {
	d.mu.Lock()
	d.grants = map[string]*inboundGrant{}
	d.mu.Unlock()
}

func (d *inboundDataPlane) registerSource(id, path string, size int64, attempt uint64) error {
	if !validInboundResourceID(id) {
		return errors.New("invalid transfer id")
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	d.grants[id] = &inboundGrant{kind: inboundGrantSource, source: path, size: size, attempt: attempt, expiresAt: time.Now().Add(inboundGrantTTL)}
	return nil
}

func (d *inboundDataPlane) registerTarget(id, dest string, size int64, mode os.FileMode, sha string, attempt uint64) error {
	if !validInboundResourceID(id) {
		return errors.New("invalid transfer id")
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	d.grants[id] = &inboundGrant{
		kind: inboundGrantTarget, dest: dest, size: size, mode: mode,
		sha256: strings.ToLower(sha), attempt: attempt, expiresAt: time.Now().Add(inboundGrantTTL),
	}
	return nil
}

func (d *inboundDataPlane) lookup(id string, kind inboundGrantKind) (*inboundGrant, bool) {
	d.mu.Lock()
	defer d.mu.Unlock()
	grant := d.grants[id]
	if grant == nil {
		return nil, false
	}
	if time.Now().After(grant.expiresAt) {
		delete(d.grants, id)
		return nil, false
	}
	if grant.kind != kind {
		return nil, false
	}
	return grant, true
}

func (d *inboundDataPlane) finish(id string) {
	d.mu.Lock()
	delete(d.grants, id)
	d.mu.Unlock()
}

func (d *inboundDataPlane) cancel(id string) {
	d.mu.Lock()
	delete(d.grants, id)
	d.mu.Unlock()
	_ = os.Remove(d.partPath(id))
}

func (d *inboundDataPlane) partPath(id string) string {
	return filepath.Join(d.agent.cfg.DataDir, "inbound-parts", id+".part")
}

func (d *inboundDataPlane) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/agent-data/v1/")
	parts := strings.Split(strings.Trim(path, "/"), "/")
	if len(parts) == 1 && parts[0] == "health" && r.Method == http.MethodGet {
		writeInboundData(w, http.StatusOK, map[string]any{"ok": true})
		return
	}
	if len(parts) == 3 && parts[0] == "transfers" && validInboundResourceID(parts[1]) {
		switch {
		case parts[2] == "source" && r.Method == http.MethodGet:
			d.handleSource(w, r, parts[1])
			return
		case parts[2] == "target" && r.Method == http.MethodPut:
			d.handleTarget(w, r, parts[1])
			return
		}
	}
	if len(parts) == 2 && parts[0] == "artifacts" && r.Method == http.MethodPut && validInboundResourceID(parts[1]) {
		d.handleArtifact(w, r)
		return
	}
	if len(parts) == 1 && parts[0] == "agent-binary" && r.Method == http.MethodPut {
		d.handleAgentBinary(w, r)
		return
	}
	writeInboundDataError(w, http.StatusNotFound, "not_found", "data plane endpoint not found")
}

func (d *inboundDataPlane) handleSource(w http.ResponseWriter, r *http.Request, id string) {
	grant, ok := d.lookup(id, inboundGrantSource)
	if !ok {
		writeInboundDataError(w, http.StatusNotFound, "transfer_not_found", "transfer source grant not found or expired")
		return
	}
	offset, err := parseNonNegativeInt(r.URL.Query().Get("offset"))
	if err != nil {
		writeInboundDataError(w, http.StatusBadRequest, "invalid_offset", err.Error())
		return
	}
	if offset > grant.size {
		writeInboundDataError(w, http.StatusConflict, "offset_mismatch", fmt.Sprintf("offset %d exceeds size %d", offset, grant.size))
		return
	}
	if err := d.agent.rejectSymlink(r.Context(), grant.source); err != nil {
		d.agent.sendFileUploadResult(id, grant.attempt, err)
		d.finish(id)
		writeInboundDataError(w, http.StatusUnprocessableEntity, "source_invalid", err.Error())
		return
	}
	f, info, err := d.agent.host.OpenRead(r.Context(), grant.source)
	if err != nil {
		d.agent.sendFileUploadResult(id, grant.attempt, err)
		d.finish(id)
		writeInboundDataError(w, http.StatusNotFound, "source_not_found", err.Error())
		return
	}
	defer f.Close()
	if !info.Mode().IsRegular() {
		writeInboundDataError(w, http.StatusUnprocessableEntity, "source_invalid", "source is not a regular file")
		return
	}
	if info.Size() != grant.size {
		d.agent.sendFileUploadResult(id, grant.attempt, fmt.Errorf("source size changed from %d to %d", grant.size, info.Size()))
		d.finish(id)
		writeInboundDataError(w, http.StatusConflict, "source_changed", "source file size changed")
		return
	}
	seeker, ok := f.(io.Seeker)
	if !ok {
		writeInboundDataError(w, http.StatusInternalServerError, "source_not_seekable", "source file is not seekable")
		return
	}
	if _, err := seeker.Seek(offset, io.SeekStart); err != nil {
		writeInboundDataError(w, http.StatusInternalServerError, "source_seek_failed", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(grant.size-offset, 10))
	w.Header().Set("Accept-Ranges", "bytes")
	w.Header().Set("X-NodeSteer-File-Size", strconv.FormatInt(grant.size, 10))
	w.WriteHeader(http.StatusOK)
	if _, err := io.CopyN(w, f, grant.size-offset); err != nil {
		// 客户端断开：保留 grant，Hub 可按 offset 重试。
		d.agent.logger.Warn("inbound source pull interrupted", "transfer", id, "offset", offset, "error", err)
		return
	}
	d.finish(id)
	d.agent.sendFileUploadResult(id, grant.attempt, nil)
	d.agent.logger.Info("inbound source pulled", "transfer", id, "size", grant.size)
}

func (d *inboundDataPlane) handleTarget(w http.ResponseWriter, r *http.Request, id string) {
	grant, ok := d.lookup(id, inboundGrantTarget)
	if !ok {
		writeInboundDataError(w, http.StatusNotFound, "transfer_not_found", "transfer target grant not found or expired")
		return
	}
	offset, err := parseNonNegativeInt(r.URL.Query().Get("offset"))
	if err != nil {
		writeInboundDataError(w, http.StatusBadRequest, "invalid_offset", err.Error())
		return
	}
	part := d.partPath(id)
	if err := os.MkdirAll(filepath.Dir(part), 0o700); err != nil {
		writeInboundStorageError(w, err)
		return
	}
	current := fileSize(part)
	if offset != current {
		writeInboundData(w, http.StatusConflict, map[string]any{"code": "offset_mismatch", "offset": current})
		return
	}
	remaining := grant.size - offset
	if remaining < 0 {
		writeInboundDataError(w, http.StatusConflict, "offset_mismatch", "offset exceeds expected size")
		return
	}
	if r.ContentLength >= 0 && r.ContentLength != remaining {
		writeInboundDataError(w, http.StatusBadRequest, "size_mismatch", "content length does not match remaining size")
		return
	}
	f, err := os.OpenFile(part, os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		writeInboundStorageError(w, err)
		return
	}
	if _, err := f.Seek(offset, io.SeekStart); err != nil {
		f.Close()
		writeInboundStorageError(w, err)
		return
	}
	body := http.MaxBytesReader(w, r.Body, remaining)
	n, copyErr := io.Copy(f, body)
	syncErr := f.Sync()
	closeErr := f.Close()
	if copyErr != nil {
		if isMaxBytesError(copyErr) {
			writeInboundDataError(w, http.StatusRequestEntityTooLarge, "size_mismatch", "request body exceeds expected size")
			return
		}
		writeInboundStorageError(w, copyErr)
		return
	}
	if syncErr != nil || closeErr != nil {
		writeInboundStorageError(w, errors.New("failed to persist target part"))
		return
	}
	next := offset + n
	if next < grant.size {
		writeInboundData(w, http.StatusConflict, map[string]any{"code": "offset_mismatch", "offset": next})
		return
	}
	partInfo, err := os.Stat(part)
	if err != nil || partInfo.Size() != grant.size {
		writeInboundDataError(w, http.StatusUnprocessableEntity, "size_mismatch", "target size verification failed")
		return
	}
	sha, err := fileSHA256(part)
	if err != nil {
		writeInboundStorageError(w, err)
		return
	}
	if grant.sha256 != "" && sha != grant.sha256 {
		_ = os.Remove(part)
		d.agent.sendFileDeliveryResult(id, grant.attempt, fmt.Errorf("sha256 mismatch: expected %s got %s", grant.sha256, sha))
		d.finish(id)
		writeInboundDataError(w, http.StatusUnprocessableEntity, "sha256_mismatch", "target sha256 verification failed")
		return
	}
	if err := d.agent.rejectSymlink(r.Context(), grant.dest); err != nil {
		_ = os.Remove(part)
		d.agent.sendFileDeliveryResult(id, grant.attempt, err)
		d.finish(id)
		writeInboundDataError(w, http.StatusUnprocessableEntity, "destination_invalid", err.Error())
		return
	}
	partReader, err := os.Open(part)
	if err != nil {
		writeInboundStorageError(w, err)
		return
	}
	replaceErr := d.agent.host.AtomicReplaceReader(r.Context(), grant.dest, partReader, grant.mode)
	partReader.Close()
	if replaceErr != nil {
		writeInboundStorageError(w, replaceErr)
		return
	}
	_ = os.Remove(part)
	d.finish(id)
	d.agent.sendFileDeliveryResult(id, grant.attempt, nil)
	d.agent.logger.Info("inbound target pushed", "transfer", id, "size", grant.size, "destination", grant.dest)
	writeInboundData(w, http.StatusOK, map[string]any{"ok": true, "size": grant.size, "sha256": sha})
}

func (d *inboundDataPlane) handleArtifact(w http.ResponseWriter, r *http.Request) {
	sha := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("sha256")))
	size, err := parseNonNegativeInt(r.URL.Query().Get("size"))
	if err != nil || !validSHA256(sha) || size > maxInboundArtifactBytes {
		writeInboundDataError(w, http.StatusBadRequest, "invalid_artifact", "sha256/size are invalid or exceed limit")
		return
	}
	if d.agent.artifact.Has(sha) {
		writeInboundData(w, http.StatusNoContent, nil)
		return
	}
	body := http.MaxBytesReader(w, r.Body, size)
	if err := d.agent.artifact.StoreReader(r.Context(), sha, size, body); err != nil {
		if isMaxBytesError(err) {
			writeInboundDataError(w, http.StatusRequestEntityTooLarge, "size_mismatch", "artifact exceeds declared size")
			return
		}
		if errors.Is(err, syscall.ENOSPC) || errors.Is(err, os.ErrPermission) {
			writeInboundStorageError(w, err)
			return
		}
		writeInboundDataError(w, http.StatusUnprocessableEntity, "artifact_invalid", err.Error())
		return
	}
	d.agent.logger.Info("inbound artifact pushed", "sha256", sha, "size", size)
	writeInboundData(w, http.StatusCreated, map[string]any{"ok": true, "sha256": sha, "size": size})
}

func (d *inboundDataPlane) handleAgentBinary(w http.ResponseWriter, r *http.Request) {
	sha := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("sha256")))
	arch := strings.TrimSpace(r.URL.Query().Get("arch"))
	size, err := parseNonNegativeInt(r.URL.Query().Get("size"))
	if err != nil || !validSHA256(sha) || arch != osArch() || size <= 0 || size > maxInboundAgentBinarySize {
		writeInboundDataError(w, http.StatusBadRequest, "invalid_agent_binary", "sha256/arch/size are invalid or exceed limit")
		return
	}
	staging := d.agent.agentBinaryStagingPath()
	if info, err := os.Stat(staging); err == nil && info.Mode().IsRegular() {
		if existing, err := fileSHA256(staging); err == nil && existing == sha {
			writeInboundData(w, http.StatusNoContent, nil)
			return
		}
	}
	dir := filepath.Dir(staging)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		writeInboundStorageError(w, err)
		return
	}
	tmp := staging + ".part"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		writeInboundStorageError(w, err)
		return
	}
	body := http.MaxBytesReader(w, r.Body, size)
	h := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(f, h), body)
	syncErr := f.Sync()
	closeErr := f.Close()
	if copyErr != nil {
		_ = os.Remove(tmp)
		if isMaxBytesError(copyErr) {
			writeInboundDataError(w, http.StatusRequestEntityTooLarge, "size_mismatch", "agent binary exceeds declared size")
			return
		}
		writeInboundStorageError(w, copyErr)
		return
	}
	if syncErr != nil || closeErr != nil || n != size || hex.EncodeToString(h.Sum(nil)) != sha {
		_ = os.Remove(tmp)
		writeInboundDataError(w, http.StatusUnprocessableEntity, "agent_binary_invalid", "agent binary size/sha256 verification failed")
		return
	}
	if err := os.Chmod(tmp, 0o755); err != nil {
		_ = os.Remove(tmp)
		writeInboundStorageError(w, err)
		return
	}
	if err := os.Rename(tmp, staging); err != nil {
		_ = os.Remove(tmp)
		writeInboundStorageError(w, err)
		return
	}
	d.agent.logger.Info("inbound agent binary staged", "sha256", sha, "size", size)
	writeInboundData(w, http.StatusCreated, map[string]any{"ok": true, "sha256": sha, "size": size})
}

func (a *Agent) agentBinaryStagingPath() string {
	return filepath.Join(a.cfg.DataDir, "agent-binaries", "nodesteer-agent-"+osArch())
}

func (a *Agent) rejectSymlink(ctx context.Context, path string) error {
	if checker, ok := a.host.(interface {
		Lstat(context.Context, string) (os.FileInfo, error)
	}); ok {
		info, err := checker.Lstat(ctx, path)
		if err != nil {
			if os.IsNotExist(err) {
				return nil
			}
			return err
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("path is a symlink: %s", path)
		}
	}
	return nil
}

func validInboundResourceID(value string) bool {
	if value == "" || len(value) > 128 {
		return false
	}
	for _, ch := range value {
		if (ch < 'a' || ch > 'z') && (ch < 'A' || ch > 'Z') && (ch < '0' || ch > '9') && ch != '-' && ch != '_' && ch != '.' {
			return false
		}
	}
	return true
}

func validSHA256(value string) bool {
	if len(value) != 64 {
		return false
	}
	_, err := hex.DecodeString(value)
	return err == nil
}

func parseNonNegativeInt(value string) (int64, error) {
	n, err := strconv.ParseInt(value, 10, 64)
	if err != nil || n < 0 {
		return 0, errors.New("must be a non-negative integer")
	}
	return n, nil
}

func fileSize(path string) int64 {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return 0
	}
	return info.Size()
}

func fileSHA256(path string) (string, error) {
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

func isMaxBytesError(err error) bool {
	var maxErr *http.MaxBytesError
	return errors.As(err, &maxErr)
}

func writeInboundData(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if body != nil {
		_ = json.NewEncoder(w).Encode(body)
	}
}

func writeInboundStorageError(w http.ResponseWriter, err error) {
	if errors.Is(err, syscall.ENOSPC) {
		writeInboundDataError(w, http.StatusInsufficientStorage, "storage_full", "agent disk is full")
		return
	}
	if errors.Is(err, os.ErrPermission) {
		writeInboundDataError(w, http.StatusForbidden, "permission_denied", "agent storage permission denied")
		return
	}
	writeInboundDataError(w, http.StatusInternalServerError, "storage_failed", err.Error())
}

func writeInboundDataError(w http.ResponseWriter, status int, code, message string) {
	writeInboundData(w, status, map[string]string{"code": code, "message": message})
}

// registerInboundSourceGrant 由 FILE_UPLOAD_REQUEST(transport=inbound_pull) 调用。
func (a *Agent) registerInboundSourceGrant(p protocol.FileUploadRequestPayload) {
	if a.dataPlane == nil {
		a.sendFileUploadResult(p.TransferID, p.Attempt, errors.New("inbound data plane is not configured"))
		return
	}
	if err := a.validateFileTransferPath(p.SourcePath); err != nil {
		a.sendFileUploadResult(p.TransferID, p.Attempt, fmt.Errorf("source path: %w", err))
		return
	}
	if err := a.rejectSymlink(a.ctx, p.SourcePath); err != nil {
		a.sendFileUploadResult(p.TransferID, p.Attempt, err)
		return
	}
	f, info, err := a.host.OpenRead(a.ctx, p.SourcePath)
	if err != nil {
		a.sendFileUploadResult(p.TransferID, p.Attempt, err)
		return
	}
	f.Close()
	if !info.Mode().IsRegular() {
		a.sendFileUploadResult(p.TransferID, p.Attempt, errors.New("source is not a regular file"))
		return
	}
	if err := a.dataPlane.registerSource(p.TransferID, p.SourcePath, info.Size(), p.Attempt); err != nil {
		a.sendFileUploadResult(p.TransferID, p.Attempt, err)
		return
	}
	a.logger.Info("inbound source grant registered", "transfer", p.TransferID, "size", info.Size())
}

// registerInboundTargetGrant 由 FILE_DELIVERY_REQUEST(transport=inbound_push) 调用。
func (a *Agent) registerInboundTargetGrant(p protocol.FileDeliveryRequestPayload) {
	if a.dataPlane == nil {
		a.sendFileDeliveryResult(p.TransferID, p.Attempt, errors.New("inbound data plane is not configured"))
		return
	}
	if err := a.validateFileTransferPath(p.DestinationPath); err != nil {
		a.sendFileDeliveryResult(p.TransferID, p.Attempt, fmt.Errorf("destination path: %w", err))
		return
	}
	if err := a.rejectSymlink(a.ctx, p.DestinationPath); err != nil {
		a.sendFileDeliveryResult(p.TransferID, p.Attempt, err)
		return
	}
	mode := os.FileMode(p.Mode & 0o777)
	if mode == 0 {
		mode = 0o644
	}
	if p.Size < 0 || p.Size > a.dataPlaneSizeLimit() {
		a.sendFileDeliveryResult(p.TransferID, p.Attempt, errors.New("target size exceeds limit"))
		return
	}
	if err := a.dataPlane.registerTarget(p.TransferID, p.DestinationPath, p.Size, mode, p.SHA256, p.Attempt); err != nil {
		a.sendFileDeliveryResult(p.TransferID, p.Attempt, err)
		return
	}
	a.logger.Info("inbound target grant registered", "transfer", p.TransferID, "size", p.Size)
}

func (a *Agent) dataPlaneSizeLimit() int64 {
	return maxInboundArtifactBytes
}
