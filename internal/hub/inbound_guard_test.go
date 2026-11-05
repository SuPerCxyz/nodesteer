package hub

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestInboundDataPlaneGuards(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	defer st.Close()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	rm := NewRevisionManager(st)
	sessions := NewSessionManager()
	nodes := NewNodeManager(st, rm)
	syncMgr := NewSyncManager(st, rm, sessions, nodes)
	execMgr := NewExecutionManager(st, sessions, nodes, logger)
	artifacts, err := NewArtifactManager(st, t.TempDir(), sessions, syncMgr)
	if err != nil {
		t.Fatal(err)
	}
	apps := NewAppManager(st, rm, sessions, artifacts, execMgr, "http://hub.example")
	transfers, err := NewFileTransferManager(st, t.TempDir(), "http://hub.example", sessions, nodes, logger)
	if err != nil {
		t.Fatal(err)
	}

	node := &models.Node{
		ID: "node-inbound", AgentID: "agent-inbound", Hostname: "inbound",
		Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative,
		Capabilities: map[string]bool{
			models.CapInboundConnection: true,
			models.CapApplicationDeploy: true,
		},
	}
	if err := st.UpsertNode(ctx, node); err != nil {
		t.Fatal(err)
	}
	conn := &fakeAgentConn{nodeID: node.ID, origin: models.ConnectionModeInbound}
	sessions.Register(conn)

	// 文件传输：源端为 inbound 时创建即拒绝，不留 PENDING/DELIVERING。
	if _, err := transfers.Create(ctx, node.ID, "/tmp/source", []FileTransferTargetRequest{{NodeID: "other", DestinationPath: "/tmp/dest"}}); !errors.Is(err, ErrInboundDataPlaneUnavailable) {
		t.Fatalf("transfer create err = %v, want inbound data plane unavailable", err)
	}

	// Artifact 预取：显式不可用，不下发控制指令。
	if err := st.CreateArtifact(ctx, &models.Artifact{ID: "artifact-1", Name: "a", Version: "1", Architecture: "amd64", Filename: "a", Size: 1, SHA256: "x"}); err != nil {
		t.Fatal(err)
	}
	if err := artifacts.Prefetch(ctx, node.ID, "artifact-1", "http://hub.example"); !errors.Is(err, ErrInboundDataPlaneUnavailable) {
		t.Fatalf("artifact prefetch err = %v, want inbound data plane unavailable", err)
	}

	// 带 Artifact 的部署必须 BLOCKED，不尝试不可达的 Hub URL。
	app := &models.Application{ID: "app-1", ArtifactID: "artifact-1"}
	ex := &models.Execution{ID: "exec-1", NodeID: node.ID, Status: models.ExecStatusPending}
	if err := apps.dispatchDeploy(ctx, ex, app, node.ID, "deploy", ExecutionStrategy{}); !errors.Is(err, ErrInboundDataPlaneUnavailable) {
		t.Fatalf("deploy dispatch err = %v, want inbound data plane unavailable", err)
	}

	// 自升级需要 Hub 推送二进制；Phase 2 直接 BLOCKED。
	upgradeResults, err := execMgr.UpgradeNodes(ctx, []string{node.ID}, "")
	if err != nil {
		t.Fatalf("upgrade nodes: %v", err)
	}
	if len(upgradeResults) != 1 || upgradeResults[0].Status != models.ExecStatusBlocked ||
		upgradeResults[0].BlockReason != "inbound_data_plane_unavailable" {
		t.Fatalf("upgrade result = %+v", upgradeResults)
	}
	upgradeExec, err := st.GetExecution(ctx, upgradeResults[0].ExecutionID)
	if err != nil {
		t.Fatal(err)
	}
	if upgradeExec.Status != models.ExecStatusBlocked || upgradeExec.BlockReason != "inbound_data_plane_unavailable" {
		t.Fatalf("upgrade execution = %+v", upgradeExec)
	}
}
