package hubserver

import (
	"context"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent"
	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func TestFileTransferWithRealAgents(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: nil}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	h, err := New(Config{
		GatewayAddr:       ":8443",
		GatewayBaseURL:    "http://" + ln.Addr().String(),
		RegistrationToken: "transfer-token",
		DataDir:           t.TempDir(),
		ArtifactDir:       filepath.Join(t.TempDir(), "artifacts"),
		BaseURL:           "http://127.0.0.1:8080",
		HeartbeatTimeout:  10 * time.Second,
		SessionTTL:        time.Hour,
	}, logger)
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	server.Handler = h.GatewayHandler()
	go server.Serve(ln)
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	newAgent := func() *agent.Agent {
		a, createErr := agent.New(agent.Config{
			HubURL:            "ws://" + ln.Addr().String(),
			RegistrationToken: "transfer-token",
			DeploymentMode:    models.DeploymentModeNative,
			DataDir:           t.TempDir(),
			AgentVersion:      "test",
		}, logger)
		if createErr != nil {
			t.Fatal(createErr)
		}
		go a.Run(ctx)
		return a
	}
	sourceAgent := newAgent()
	targetAgent := newAgent()
	defer sourceAgent.Close()
	defer targetAgent.Close()

	deadline := time.Now().Add(10 * time.Second)
	for h.Sessions().Count() < 2 && time.Now().Before(deadline) {
		time.Sleep(50 * time.Millisecond)
	}
	if h.Sessions().Count() != 2 {
		t.Fatal("agents did not connect")
	}
	nodes, err := h.Nodes().ListNodes(ctx)
	if err != nil || len(nodes) != 2 {
		t.Fatalf("list nodes: %v (%d)", err, len(nodes))
	}
	sourcePath := filepath.Join(t.TempDir(), "source.txt")
	targetPath := filepath.Join(t.TempDir(), "target.txt")
	content := []byte("real agent relay")
	if err := os.WriteFile(sourcePath, content, 0o640); err != nil {
		t.Fatal(err)
	}
	transfer, err := h.Transfers().Create(ctx, nodes[0].ID, sourcePath, []hub.FileTransferTargetRequest{{NodeID: nodes[1].ID, DestinationPath: targetPath}})
	if err != nil {
		t.Fatal(err)
	}
	deadline = time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		current, getErr := h.Transfers().Get(ctx, transfer.ID)
		if getErr == nil && current.Status == models.FileTransferSuccess {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	current, err := h.Transfers().Get(ctx, transfer.ID)
	if err != nil || current.Status != models.FileTransferSuccess {
		t.Fatalf("transfer did not finish: err=%v state=%+v", err, current)
	}
	got, err := os.ReadFile(targetPath)
	if err != nil || string(got) != string(content) {
		t.Fatalf("target content mismatch: %v %q", err, got)
	}
}
