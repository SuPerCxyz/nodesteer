package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/pem"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestArtifactCacheSHAFailure 校验失败不得安装
func TestArtifactCacheSHAFailure(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	st := newTestAgentStore(t)
	cache := NewArtifactCache(filepath.Join(dir, "artifacts"), st, logger, "")

	// 服务端返回错误内容（SHA 不匹配）
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("corrupted-content"))
	}))
	defer srv.Close()

	wantSHA := strings.Repeat("a", 64)
	err := cache.Prefetch(context.Background(), srv.URL, wantSHA)
	if err == nil {
		t.Fatalf("expected SHA mismatch error")
	}
	if !strings.Contains(err.Error(), "mismatch") {
		t.Fatalf("expected mismatch error, got: %v", err)
	}
	// 缓存文件不应存在
	if cache.Has(wantSHA) {
		t.Fatalf("corrupted artifact must not be cached")
	}
	// .tmp 应被清理
	matches, _ := filepath.Glob(filepath.Join(dir, "artifacts", "*.tmp"))
	if len(matches) > 0 {
		t.Fatalf("tmp files not cleaned: %v", matches)
	}
}

// TestArtifactCacheSuccess 成功下载并原子缓存
func TestArtifactCacheSuccess(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	st := newTestAgentStore(t)
	cache := NewArtifactCache(filepath.Join(dir, "artifacts"), st, logger, "")

	content := []byte("hello-artifact")
	h := sha256.Sum256(content)
	sha := hex.EncodeToString(h[:])

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(content)
	}))
	defer srv.Close()

	if err := cache.Prefetch(context.Background(), srv.URL, sha); err != nil {
		t.Fatalf("prefetch: %v", err)
	}
	if !cache.Has(sha) {
		t.Fatalf("artifact should be cached")
	}
	f, err := cache.Open(sha)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	defer f.Close()
	data, _ := io.ReadAll(f)
	if string(data) != string(content) {
		t.Fatalf("content mismatch")
	}
	// 再次 Prefetch 应命中缓存
	if err := cache.Prefetch(context.Background(), srv.URL, sha); err != nil {
		t.Fatalf("second prefetch: %v", err)
	}
}

func TestArtifactCacheRejectsHTTPError(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	st := newTestAgentStore(t)
	cache := NewArtifactCache(filepath.Join(dir, "artifacts"), st, logger, "")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "not found", http.StatusNotFound)
	}))
	defer srv.Close()
	if err := cache.Prefetch(context.Background(), srv.URL, strings.Repeat("b", 64)); err == nil {
		t.Fatal("expected non-200 download to fail")
	}
}

// TestArtifactCacheStalledBodyTimesOut 服务端发头后不给 body：
// 带超时的客户端必须在有限时间内失败，且 0B .tmp 被清理
// （缺陷 OBS-20261014-ARTIFACT-DOWNLOAD-NO-READ-TIMEOUT：无读超时时
// io.Copy 永久阻塞、0B .tmp 长期残留）。测试注入短超时客户端模拟生产
// connection.defaultHTTPTimeout 的兜底行为。
func TestArtifactCacheStalledBodyTimesOut(t *testing.T) {
	dir := t.TempDir()
	st := newTestAgentStore(t)
	cache := NewArtifactCache(filepath.Join(dir, "artifacts"), st, discardLogger(), "")
	cache.client = &http.Client{Timeout: 200 * time.Millisecond}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "1024")
		w.WriteHeader(http.StatusOK)
		if f, ok := w.(http.Flusher); ok {
			f.Flush() // 只发头，不给 body
		}
		// 客户端超时断开后请求上下文取消，handler 退出，srv.Close 不阻塞。
		<-r.Context().Done()
	}))
	defer srv.Close()

	sha := strings.Repeat("c", 64)
	start := time.Now()
	err := cache.Prefetch(context.Background(), srv.URL, sha)
	if err == nil {
		t.Fatal("stalled download must fail instead of blocking forever")
	}
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Fatalf("stalled download took %v, want bounded by client timeout", elapsed)
	}
	if cache.Has(sha) {
		t.Fatal("stalled artifact must not be cached")
	}
	if _, err := os.Stat(cache.Path(sha) + ".tmp"); !os.IsNotExist(err) {
		t.Fatalf("0B .tmp must be cleaned after timeout, stat err=%v", err)
	}
}

var _ = os.Getenv

func TestArtifactUsesAgentConfiguredCA(t *testing.T) {
	content := []byte("private-ca-artifact")
	sum := sha256.Sum256(content)
	sha := hex.EncodeToString(sum[:])
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-NodeSteer-Agent-ID") != "agent-ca" || r.Header.Get("X-NodeSteer-Agent-Token") != "credential-ca" {
			http.Error(w, "identity missing", http.StatusForbidden)
			return
		}
		w.Write(content)
	}))
	defer server.Close()
	caFile := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(caFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0o600); err != nil {
		t.Fatal(err)
	}
	for _, ca := range []string{"", caFile} {
		t.Run(map[bool]string{true: "configured", false: "missing"}[ca != ""], func(t *testing.T) {
			a := newTestAgent(t, Config{TLSCAFile: ca})
			a.artifact.SetIdentity("agent-ca", "credential-ca")
			err := a.artifact.Prefetch(t.Context(), server.URL, sha)
			if ca == "" {
				if err == nil || a.artifact.Has(sha) {
					t.Fatal("untrusted CA accepted")
				}
			} else if err != nil || !a.artifact.Has(sha) {
				t.Fatalf("configured CA failed: %v", err)
			}
			if _, err := os.Stat(a.artifact.Path(sha) + ".tmp"); !os.IsNotExist(err) {
				t.Fatalf("temporary download retained: %v", err)
			}
		})
	}
}
