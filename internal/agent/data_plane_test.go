package agent

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"syscall"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/agent/host"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func newDataPlaneTestAgent(t *testing.T) *Agent {
	t.Helper()
	dir := t.TempDir()
	st, err := OpenLocalStore(filepath.Join(dir, "state"))
	if err != nil {
		t.Fatalf("open local store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	a := &Agent{
		cfg:      Config{DataDir: dir},
		store:    st,
		host:     host.DefaultHostAdapter(models.DeploymentModeNative),
		artifact: NewArtifactCache(filepath.Join(dir, "artifacts"), st, logger, ""),
		logger:   logger,
	}
	a.dataPlane = newInboundDataPlane(a)
	return a
}

func TestInboundDataPlaneSourcePullAndResume(t *testing.T) {
	a := newDataPlaneTestAgent(t)
	path := filepath.Join(t.TempDir(), "source.txt")
	payload := []byte("hello inbound source")
	if err := os.WriteFile(path, payload, 0o644); err != nil {
		t.Fatal(err)
	}
	a.registerInboundSourceGrant(protocol.FileUploadRequestPayload{
		TransferID: "transfer-pull", SourcePath: path, Attempt: 3, Transport: protocol.TransportInboundPull,
	})
	req := httptest.NewRequest(http.MethodGet, "/agent-data/v1/transfers/transfer-pull/source?offset=6", nil)
	rec := httptest.NewRecorder()
	a.dataPlane.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("source pull status %d: %s", rec.Code, rec.Body.String())
	}
	if !bytes.Equal(rec.Body.Bytes(), payload[6:]) {
		t.Fatalf("source body = %q, want %q", rec.Body.Bytes(), payload[6:])
	}
	if rec.Header().Get("X-NodeSteer-File-Size") != "20" {
		t.Fatalf("source size header = %q", rec.Header().Get("X-NodeSteer-File-Size"))
	}
	if _, ok := a.dataPlane.lookup("transfer-pull", inboundGrantSource); ok {
		t.Fatal("source grant must be removed after successful pull")
	}
}

func TestInboundDataPlaneTargetPushAndResume(t *testing.T) {
	a := newDataPlaneTestAgent(t)
	dest := filepath.Join(t.TempDir(), "target.txt")
	payload := []byte("target payload")
	sum := sha256.Sum256(payload)
	sha := hex.EncodeToString(sum[:])
	a.registerInboundTargetGrant(protocol.FileDeliveryRequestPayload{
		TransferID: "transfer-push", DestinationPath: dest, Size: int64(len(payload)),
		Mode: 0o640, SHA256: sha, Attempt: 1, Transport: protocol.TransportInboundPush,
	})

	// 模拟已收到前 6 字节后的断点续传。
	part := a.dataPlane.partPath("transfer-push")
	if err := os.MkdirAll(filepath.Dir(part), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(part, payload[:6], 0o600); err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPut, "/agent-data/v1/transfers/transfer-push/target?offset=6", bytes.NewReader(payload[6:]))
	rec := httptest.NewRecorder()
	a.dataPlane.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("target push status %d: %s", rec.Code, rec.Body.String())
	}
	got, err := os.ReadFile(dest)
	if err != nil || !bytes.Equal(got, payload) {
		t.Fatalf("target content = %q err=%v", got, err)
	}
	info, err := os.Stat(dest)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o640 {
		t.Fatalf("target mode = %v, want 0640", info.Mode().Perm())
	}
	if _, err := os.Stat(part); !os.IsNotExist(err) {
		t.Fatalf("part file must be removed, err=%v", err)
	}
}

func TestInboundDataPlaneTargetSHAAndPathFailures(t *testing.T) {
	a := newDataPlaneTestAgent(t)
	dest := filepath.Join(t.TempDir(), "sha.txt")
	payload := []byte("bad sha payload")
	a.registerInboundTargetGrant(protocol.FileDeliveryRequestPayload{
		TransferID: "transfer-sha", DestinationPath: dest, Size: int64(len(payload)),
		Mode: 0o644, SHA256: "0000000000000000000000000000000000000000000000000000000000000000", Attempt: 1,
		Transport: protocol.TransportInboundPush,
	})
	req := httptest.NewRequest(http.MethodPut, "/agent-data/v1/transfers/transfer-sha/target?offset=0", bytes.NewReader(payload))
	rec := httptest.NewRecorder()
	a.dataPlane.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("sha mismatch status = %d body=%s", rec.Code, rec.Body.String())
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("destination must not exist after sha mismatch: %v", err)
	}
	if _, err := os.Stat(a.dataPlane.partPath("transfer-sha")); !os.IsNotExist(err) {
		t.Fatalf("part must be removed after sha mismatch: %v", err)
	}

	// 已存在符号链接目标：注册阶段拒绝。
	symlink := filepath.Join(t.TempDir(), "symlink")
	realTarget := filepath.Join(t.TempDir(), "real")
	if err := os.WriteFile(realTarget, []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(realTarget, symlink); err != nil {
		t.Fatal(err)
	}
	a.registerInboundTargetGrant(protocol.FileDeliveryRequestPayload{
		TransferID: "transfer-symlink", DestinationPath: symlink, Size: 3, Mode: 0o644, Attempt: 1,
	})
	if _, ok := a.dataPlane.lookup("transfer-symlink", inboundGrantTarget); ok {
		t.Fatal("symlink destination must not get a data plane grant")
	}
	// 路径穿越由 host.ValidatePath 拒绝。
	a.registerInboundTargetGrant(protocol.FileDeliveryRequestPayload{
		TransferID: "transfer-traversal", DestinationPath: "/tmp/../etc/passwd", Size: 3, Mode: 0o644, Attempt: 1,
	})
	if _, ok := a.dataPlane.lookup("transfer-traversal", inboundGrantTarget); ok {
		t.Fatal("path traversal must not get a data plane grant")
	}
}

func TestInboundDataPlaneArtifactAndAgentBinary(t *testing.T) {
	a := newDataPlaneTestAgent(t)
	payload := []byte("artifact payload")
	sum := sha256.Sum256(payload)
	sha := hex.EncodeToString(sum[:])
	req := httptest.NewRequest(http.MethodPut,
		"/agent-data/v1/artifacts/artifact-1?sha256="+sha+"&size=16", bytes.NewReader(payload))
	rec := httptest.NewRecorder()
	a.dataPlane.ServeHTTP(rec, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("artifact push status = %d body=%s", rec.Code, rec.Body.String())
	}
	if !a.artifact.Has(sha) {
		t.Fatal("artifact not cached")
	}

	binaryPayload := []byte("agent binary payload")
	binSum := sha256.Sum256(binaryPayload)
	binSHA := hex.EncodeToString(binSum[:])
	req = httptest.NewRequest(http.MethodPut,
		"/agent-data/v1/agent-binary?sha256="+binSHA+"&arch="+osArch()+"&size=20", bytes.NewReader(binaryPayload))
	rec = httptest.NewRecorder()
	a.dataPlane.ServeHTTP(rec, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("agent binary push status = %d body=%s", rec.Code, rec.Body.String())
	}
	got, err := os.ReadFile(a.agentBinaryStagingPath())
	if err != nil || !bytes.Equal(got, binaryPayload) {
		t.Fatalf("staged agent binary = %q err=%v", got, err)
	}
}

func TestInboundDataPlaneArtifactOversizeAndConcurrent(t *testing.T) {
	a := newDataPlaneTestAgent(t)
	payload := []byte("artifact")
	sum := sha256.Sum256(payload)
	sha := hex.EncodeToString(sum[:])
	// 声明 size=3，实际 body 更长 → 413，不缓存。
	req := httptest.NewRequest(http.MethodPut, "/agent-data/v1/artifacts/a?sha256="+sha+"&size=3", bytes.NewReader(payload))
	rec := httptest.NewRecorder()
	a.dataPlane.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversize artifact status = %d body=%s", rec.Code, rec.Body.String())
	}
	if a.artifact.Has(sha) {
		t.Fatal("oversize artifact must not be cached")
	}

	// 并发相同 SHA 推送必须都成功（各自临时文件，不互相覆盖）。
	done := make(chan int, 2)
	for i := 0; i < 2; i++ {
		go func() {
			req := httptest.NewRequest(http.MethodPut, "/agent-data/v1/artifacts/a?sha256="+sha+"&size="+strconv.Itoa(len(payload)), bytes.NewReader(payload))
			rec := httptest.NewRecorder()
			a.dataPlane.ServeHTTP(rec, req)
			done <- rec.Code
		}()
	}
	for i := 0; i < 2; i++ {
		if code := <-done; code != http.StatusCreated && code != http.StatusNoContent {
			t.Fatalf("concurrent artifact status = %d", code)
		}
	}
	if !a.artifact.Has(sha) {
		t.Fatal("concurrent artifact not cached")
	}
}

func TestInboundDataPlaneStorageErrorMapping(t *testing.T) {
	rec := httptest.NewRecorder()
	writeInboundStorageError(rec, syscall.ENOSPC)
	if rec.Code != http.StatusInsufficientStorage {
		t.Fatalf("ENOSPC status = %d, want 507", rec.Code)
	}
}

func TestInboundDataPlaneArtifactConflictAndIdempotency(t *testing.T) {
	a := newDataPlaneTestAgent(t)
	payload := []byte("artifact conflict payload")
	sum := sha256.Sum256(payload)
	sha := hex.EncodeToString(sum[:])

	// SHA 声明与实际内容冲突：422，不写缓存。
	body := []byte("different bytes")
	req := httptest.NewRequest(http.MethodPut, "/agent-data/v1/artifacts/a?sha256="+sha+"&size="+strconv.Itoa(len(body)), bytes.NewReader(body))
	rec := httptest.NewRecorder()
	a.dataPlane.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnprocessableEntity || a.artifact.Has(sha) {
		t.Fatalf("artifact conflict status=%d body=%s cached=%t", rec.Code, rec.Body.String(), a.artifact.Has(sha))
	}

	// 正确内容首次 201，重复推送 204（幂等，不重复写）。
	for i, want := range []int{http.StatusCreated, http.StatusNoContent} {
		req := httptest.NewRequest(http.MethodPut, "/agent-data/v1/artifacts/a?sha256="+sha+"&size="+strconv.Itoa(len(payload)), bytes.NewReader(payload))
		rec := httptest.NewRecorder()
		a.dataPlane.ServeHTTP(rec, req)
		if rec.Code != want {
			t.Fatalf("artifact push %d status=%d body=%s", i, rec.Code, rec.Body.String())
		}
	}
	if !a.artifact.Has(sha) {
		t.Fatal("artifact was not cached")
	}
}
