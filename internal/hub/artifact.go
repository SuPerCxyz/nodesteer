package hub

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/protocol"
	"github.com/cadentra/cadentra/internal/store"
	"github.com/google/uuid"
)

// ArtifactManager Artifact 管理
type ArtifactManager struct {
	store      store.Store
	storageDir string
	sessions   *SessionManager
	syncMgr    *SyncManager
}

// NewArtifactManager 创建 Artifact 管理器
func NewArtifactManager(st store.Store, storageDir string, sm *SessionManager, syncMgr *SyncManager) (*ArtifactManager, error) {
	if err := os.MkdirAll(storageDir, 0o755); err != nil {
		return nil, err
	}
	return &ArtifactManager{store: st, storageDir: storageDir, sessions: sm, syncMgr: syncMgr}, nil
}

// Upload 上传 Artifact 文件
func (am *ArtifactManager) Upload(ctx context.Context, name, version, arch, filename string, r io.Reader) (*models.Artifact, error) {
	// 写入临时文件并计算 SHA256
	tmp, err := os.CreateTemp(am.storageDir, "upload-*.tmp")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())

	h := sha256.New()
	if _, err := io.Copy(io.MultiWriter(tmp, h), r); err != nil {
		tmp.Close()
		return nil, err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return nil, err
	}
	sha := hex.EncodeToString(h.Sum(nil))

	// 最终路径
	finalPath := filepath.Join(am.storageDir, sha)
	if err := os.Rename(tmp.Name(), finalPath); err != nil {
		tmp.Close()
		return nil, err
	}
	tmp.Close()

	fi, err := os.Stat(finalPath)
	if err != nil {
		return nil, err
	}

	a := &models.Artifact{
		ID:           uuid.NewString(),
		Name:         name,
		Version:      version,
		Architecture: arch,
		Filename:     filename,
		Size:         fi.Size(),
		SHA256:       sha,
		StoragePath:  finalPath,
	}
	if err := am.store.CreateArtifact(ctx, a); err != nil {
		os.Remove(finalPath)
		return nil, err
	}
	return a, nil
}

// Open 打开 Artifact 文件流
func (am *ArtifactManager) Open(ctx context.Context, id string) (*models.Artifact, io.ReadCloser, error) {
	a, err := am.store.GetArtifact(ctx, id)
	if err != nil {
		return nil, nil, err
	}
	f, err := os.Open(a.StoragePath)
	if err != nil {
		return nil, nil, err
	}
	return a, f, nil
}

// Get 获取元数据
func (am *ArtifactManager) Get(ctx context.Context, id string) (*models.Artifact, error) {
	return am.store.GetArtifact(ctx, id)
}

// Delete 删除 Artifact（同时删除存储文件）
func (am *ArtifactManager) Delete(ctx context.Context, id string) error {
	a, err := am.store.GetArtifact(ctx, id)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		return err
	}
	apps, err := am.store.ListApplications(ctx)
	if err != nil {
		return err
	}
	for _, app := range apps {
		if app.ArtifactID == id {
			return fmt.Errorf("artifact %s is referenced by application %s", id, app.ID)
		}
	}
	if err := am.store.DeleteArtifact(ctx, id); err != nil {
		return err
	}
	if a.StoragePath != "" {
		os.Remove(a.StoragePath)
	}
	return nil
}

// List 列表
func (am *ArtifactManager) List(ctx context.Context) ([]*models.Artifact, error) {
	return am.store.ListArtifacts(ctx)
}

// StoragePath 返回存储路径
func (am *ArtifactManager) StoragePath() string { return am.storageDir }

// Prefetch 下发预取指令到节点
func (am *ArtifactManager) Prefetch(ctx context.Context, nodeID, artifactID, baseURL string) error {
	a, err := am.store.GetArtifact(ctx, artifactID)
	if err != nil {
		return err
	}
	url := fmt.Sprintf("%s/api/artifacts/%s/download", baseURL, a.ID)
	conn, ok := am.sessions.Get(nodeID)
	if !ok {
		return ErrAgentOffline
	}
	return conn.Send(protocol.NewEnvelope(protocol.MsgArtifactPrefetch, "", protocol.ArtifactPrefetchPayload{
		ArtifactID: a.ID,
		URL:        url,
		SHA256:     a.SHA256,
		Size:       a.Size,
	}))
}

// PrefetchToNodes 预取到多节点
func (am *ArtifactManager) PrefetchToNodes(ctx context.Context, nodeIDs []string, artifactID, baseURL string) {
	for _, nid := range nodeIDs {
		if err := am.Prefetch(ctx, nid, artifactID, baseURL); err != nil {
			// 忽略离线节点
		}
	}
}
