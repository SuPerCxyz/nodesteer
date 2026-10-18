package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sync"
)

// ArtifactCache Agent 内容寻址 Artifact 缓存
type ArtifactCache struct {
	dir     string
	store   *LocalStore
	logger  *slog.Logger
	token   string
	agentID string
	client  *http.Client
	mu      sync.RWMutex
}

func (c *ArtifactCache) SetIdentity(agentID, token string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.agentID = agentID
	if token != "" {
		c.token = token
	}
}

// NewArtifactCache 创建缓存
func NewArtifactCache(dir string, st *LocalStore, logger *slog.Logger, token string) *ArtifactCache {
	os.MkdirAll(dir, 0o755)
	return &ArtifactCache{dir: dir, store: st, logger: logger, token: token, client: http.DefaultClient}
}

// Path 返回 sha 对应缓存路径
func (c *ArtifactCache) Path(sha string) string {
	return filepath.Join(c.dir, sha)
}

// Has 是否已缓存且完整
func (c *ArtifactCache) Has(sha string) bool {
	if !c.store.ArtifactExists(sha) {
		return false
	}
	f, err := os.Open(c.Path(sha))
	if err != nil {
		return false
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return false
	}
	return hex.EncodeToString(h.Sum(nil)) == sha
}

// Prefetch 下载并校验 Artifact
func (c *ArtifactCache) Prefetch(ctx context.Context, url, sha string) error {
	if sha == "" {
		return fmt.Errorf("artifact sha256 is required")
	}
	if c.Has(sha) {
		return nil
	}
	// 下载到 .tmp
	tmpPath := filepath.Join(c.dir, sha+".tmp")
	f, err := os.Create(tmpPath)
	if err != nil {
		return err
	}
	defer os.Remove(tmpPath)

	c.mu.RLock()
	token, agentID := c.token, c.agentID
	c.mu.RUnlock()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		f.Close()
		return err
	}
	req.Header.Set("X-NodeSteer-Agent-Token", token)
	req.Header.Set("X-NodeSteer-Agent-ID", agentID)
	resp, err := c.client.Do(req)
	if err != nil {
		f.Close()
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		f.Close()
		return fmt.Errorf("download status %d", resp.StatusCode)
	}

	h := sha256.New()
	if _, err := io.Copy(io.MultiWriter(f, h), resp.Body); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	f.Close()

	got := hex.EncodeToString(h.Sum(nil))
	if sha != "" && got != sha {
		return fmt.Errorf("sha256 mismatch: expected %s got %s", sha, got)
	}

	// Atomic Rename
	if err := os.Rename(tmpPath, c.Path(sha)); err != nil {
		return err
	}
	if err := c.store.RegisterArtifact(sha, c.Path(sha)); err != nil {
		return err
	}
	c.logger.Info("artifact cached", "sha", sha)
	return nil
}

// Open 打开缓存文件
func (c *ArtifactCache) Open(sha string) (*os.File, error) {
	if !c.Has(sha) {
		return nil, os.ErrNotExist
	}
	return os.Open(c.Path(sha))
}
