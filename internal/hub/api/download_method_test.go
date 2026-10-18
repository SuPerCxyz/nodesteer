package api

// Artifact 下载端点方法守卫 + API 侧连接关闭日志降级（OBS-20261018-SCALE-02 的 API 复用点）。

import (
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/google/uuid"
)

// TestArtifactDownloadMethodGuard /api/artifacts/{id}/download 仅接受 GET/HEAD：
// 此前任意方法都会经 http.ServeContent 返回文件内容（方法未拒绝）。
func TestArtifactDownloadMethodGuard(t *testing.T) {
	s := newStressServer(t)

	dir := s.server.artifacts.StoragePath()
	filePath := filepath.Join(dir, "download-guard.bin")
	content := []byte("artifact-download-guard-bytes")
	if err := os.WriteFile(filePath, content, 0o644); err != nil {
		t.Fatalf("write artifact file: %v", err)
	}
	artifact := &models.Artifact{
		ID: uuid.NewString(), Name: "dl-guard", Version: "1", Architecture: "amd64",
		Filename: "download-guard.bin", Size: int64(len(content)), SHA256: "x", StoragePath: filePath,
	}
	if err := s.store.CreateArtifact(t.Context(), artifact); err != nil {
		t.Fatalf("create artifact: %v", err)
	}
	path := "/api/artifacts/" + artifact.ID + "/download"

	// GET / HEAD 正常路径不变。
	if rec := s.do(t, http.MethodGet, path, ""); rec.Code != http.StatusOK || rec.Body.String() != string(content) {
		t.Fatalf("GET download = %d body=%q, want 200 content", rec.Code, rec.Body.String())
	}
	if rec := s.do(t, http.MethodHead, path, ""); rec.Code != http.StatusOK {
		t.Fatalf("HEAD download = %d, want 200", rec.Code)
	}

	// 其余方法 → 405 + Allow: GET, HEAD，且不返回文件内容。
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodDelete, http.MethodPatch} {
		rec := s.do(t, method, path, "")
		if rec.Code != http.StatusMethodNotAllowed {
			t.Fatalf("%s download = %d %s, want 405", method, rec.Code, rec.Body.String())
		}
		if allow := rec.Header().Get("Allow"); allow != "GET, HEAD" {
			t.Fatalf("%s download Allow = %q, want GET, HEAD", method, allow)
		}
		if strings.Contains(rec.Body.String(), string(content)) {
			t.Fatalf("%s download served file content: %s", method, rec.Body.String())
		}
	}

	// 不存在的 artifact：GET 仍 404 归一化文案。
	if rec := s.do(t, http.MethodGet, "/api/artifacts/"+uuid.NewString()+"/download", ""); rec.Code != http.StatusNotFound {
		t.Fatalf("GET missing download = %d %s, want 404", rec.Code, rec.Body.String())
	}
}

// TestWriteInternalErrorConnectionGoneNotError API 侧复用 IsConnectionGone：
// 连接已关闭/客户端取消 → Debug（无 ERROR）；真实错误 → ERROR 语义不变。
func TestWriteInternalErrorConnectionGoneNotError(t *testing.T) {
	s := newStressServer(t)

	for _, gone := range []error{net.ErrClosed, errors.New("use of closed network connection")} {
		s.logs.Reset()
		rec := httptest.NewRecorder()
		s.server.writeInternalError(rec, httptest.NewRequest(http.MethodGet, "/api/nodes", nil), gone)
		if rec.Code != http.StatusInternalServerError {
			t.Fatalf("connection-gone status = %d, want 500 (响应语义不变)", rec.Code)
		}
		if strings.Contains(s.logs.String(), "level=ERROR") {
			t.Fatalf("connection-gone logged as ERROR:\n%s", s.logs.String())
		}
	}

	s.logs.Reset()
	rec := httptest.NewRecorder()
	s.server.writeInternalError(rec, httptest.NewRequest(http.MethodGet, "/api/nodes", nil), errors.New("some real failure"))
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("real error status = %d, want 500", rec.Code)
	}
	if !strings.Contains(s.logs.String(), "level=ERROR") || !strings.Contains(s.logs.String(), "/api/nodes") {
		t.Fatalf("real error must stay ERROR with path:\n%s", s.logs.String())
	}
}
