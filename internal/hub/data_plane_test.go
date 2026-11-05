package hub

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type fakeDataPlaneConn struct {
	nodeID    string
	available bool
	mu        sync.Mutex
	sent      []protocol.Envelope
	request   func(ctx context.Context, method, path string, body io.Reader, contentLength int64) (*http.Response, error)
}

func (c *fakeDataPlaneConn) NodeID() string { return c.nodeID }
func (c *fakeDataPlaneConn) Send(env protocol.Envelope) error {
	c.mu.Lock()
	c.sent = append(c.sent, env)
	c.mu.Unlock()
	return nil
}
func (c *fakeDataPlaneConn) Close() error   { return nil }
func (c *fakeDataPlaneConn) Origin() string { return models.ConnectionModeInbound }
func (c *fakeDataPlaneConn) DataPlaneAvailable() bool {
	return c.available
}
func (c *fakeDataPlaneConn) DataPlaneRequest(ctx context.Context, method, path string, body io.Reader, contentLength int64) (*http.Response, error) {
	if c.request == nil {
		return &http.Response{StatusCode: http.StatusNotFound, Body: io.NopCloser(strings.NewReader(`{"code":"not_found"}`))}, nil
	}
	return c.request(ctx, method, path, body, contentLength)
}

type outboundCaptureConn struct {
	nodeID string
	mu     sync.Mutex
	sent   []protocol.Envelope
}

func (c *outboundCaptureConn) NodeID() string { return c.nodeID }
func (c *outboundCaptureConn) Send(env protocol.Envelope) error {
	c.mu.Lock()
	c.sent = append(c.sent, env)
	c.mu.Unlock()
	return nil
}
func (c *outboundCaptureConn) Close() error   { return nil }
func (c *outboundCaptureConn) Origin() string { return models.ConnectionModeOutbound }
func (c *outboundCaptureConn) snapshot() []protocol.Envelope {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]protocol.Envelope(nil), c.sent...)
}

func waitDataPlane(t *testing.T, cond func() bool, message string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timeout waiting for %s", message)
}

func newDataPlaneTransferHarness(t *testing.T) (*store.SQLiteStore, *SessionManager, *NodeManager, *FileTransferManager) {
	t.Helper()
	st, err := store.OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	rm := NewRevisionManager(st)
	sessions := NewSessionManager()
	nodes := NewNodeManager(st, rm)
	for _, id := range []string{"source-node", "target-node"} {
		if err := st.UpsertNode(context.Background(), &models.Node{
			ID: id, AgentID: "agent-" + id, Hostname: id, Status: models.NodeStatusOnline,
		}); err != nil {
			t.Fatal(err)
		}
	}
	transfers, err := NewFileTransferManager(st, t.TempDir(), "http://hub.example", sessions, nodes, logger)
	if err != nil {
		t.Fatal(err)
	}
	return st, sessions, nodes, transfers
}

func TestInboundSourcePullTearsToStagingAndDispatchesTarget(t *testing.T) {
	st, sessions, _, transfers := newDataPlaneTransferHarness(t)
	payload := []byte("inbound source payload")
	sum := sha256.Sum256(payload)
	source := &fakeDataPlaneConn{nodeID: "source-node", available: true, request: func(_ context.Context, method, path string, _ io.Reader, _ int64) (*http.Response, error) {
		offset := int64(0)
		if i := strings.Index(path, "offset="); i >= 0 {
			offset, _ = strconv.ParseInt(path[i+7:], 10, 64)
		}
		header := http.Header{}
		header.Set("X-NodeSteer-File-Size", strconv.Itoa(len(payload)))
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     header,
			Body:       io.NopCloser(bytes.NewReader(payload[offset:])),
		}, nil
	}}
	target := &outboundCaptureConn{nodeID: "target-node"}
	sessions.Register(source)
	sessions.Register(target)

	transfer := &models.FileTransfer{
		ID: "transfer-pull", SourceNodeID: "source-node", SourcePath: "/tmp/source", Status: models.FileTransferPending,
		Targets: []*models.FileTransferTarget{{
			TransferID: "transfer-pull", NodeID: "target-node", DestinationPath: "/tmp/target", Status: models.FileTargetPending,
		}},
	}
	if err := st.CreateFileTransfer(context.Background(), transfer); err != nil {
		t.Fatal(err)
	}
	// Hub staging 已有 5 字节，验证从断点 offset 继续拉取。
	if err := os.WriteFile(transfers.partPath(transfer.ID), payload[:5], 0o600); err != nil {
		t.Fatal(err)
	}
	transfers.dispatchSource(context.Background(), transfer)
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		current, err := st.GetFileTransfer(context.Background(), transfer.ID)
		if err == nil && current.SHA256 != "" {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if current, _ := st.GetFileTransfer(context.Background(), transfer.ID); current == nil || current.SHA256 == "" {
		t.Fatalf("source staging not completed: %+v", current)
	}
	current, err := st.GetFileTransfer(context.Background(), transfer.ID)
	if err != nil {
		t.Fatal(err)
	}
	if current.Size != int64(len(payload)) || current.SHA256 != hex.EncodeToString(sum[:]) {
		t.Fatalf("staged transfer = size %d sha %q", current.Size, current.SHA256)
	}
	if current.Status != models.FileTransferDelivering {
		t.Fatalf("transfer status = %s, want DELIVERING", current.Status)
	}
	sent := target.snapshot()
	if len(sent) == 0 || sent[0].Type != protocol.MsgFileDeliveryRequest {
		t.Fatalf("target did not receive delivery request: %+v", sent)
	}
}

func TestInboundTargetPushAndFailure(t *testing.T) {
	st, sessions, _, transfers := newDataPlaneTransferHarness(t)
	payload := []byte("push payload")
	sum := sha256.Sum256(payload)
	transfer := &models.FileTransfer{
		ID: "transfer-push", SourceNodeID: "source-node", SourcePath: "/tmp/source",
		Size: int64(len(payload)), SHA256: hex.EncodeToString(sum[:]), Status: models.FileTransferDelivering,
		Targets: []*models.FileTransferTarget{{
			TransferID: "transfer-push", NodeID: "target-node", DestinationPath: "/tmp/target", Mode: 0o644,
			Status: models.FileTargetPending,
		}},
	}
	if err := st.CreateFileTransfer(context.Background(), transfer); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(transfers.blobPath(transfer.ID), payload, 0o600); err != nil {
		t.Fatal(err)
	}
	var got []byte
	var gotPath string
	target := &fakeDataPlaneConn{nodeID: "target-node", available: true, request: func(_ context.Context, method, path string, body io.Reader, size int64) (*http.Response, error) {
		gotPath = path
		got, _ = io.ReadAll(body)
		if int64(len(got)) != size {
			t.Errorf("push body size = %d, content length = %d", len(got), size)
		}
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{"ok":true}`))}, nil
	}}
	sessions.Register(target)
	transfers.dispatchTarget(context.Background(), transfer, transfer.Targets[0])
	waitDataPlane(t, func() bool {
		current, err := st.GetFileTransfer(context.Background(), transfer.ID)
		return err == nil && current.Targets[0].Status == models.FileTargetSuccess
	}, "inbound target push success")
	if !bytes.Equal(got, payload) || !strings.Contains(gotPath, "/agent-data/v1/transfers/transfer-push/target?offset=0") {
		t.Fatalf("target push path=%q body=%q", gotPath, got)
	}

	// SHA/协议失败必须落目标 FAILED 终态，不等待超时。
	failing := &models.FileTransferTarget{
		TransferID: "transfer-push-fail", NodeID: "target-node", DestinationPath: "/tmp/target2", Status: models.FileTargetPending,
	}
	failTransfer := &models.FileTransfer{
		ID: "transfer-push-fail", SourceNodeID: "source-node", SourcePath: "/tmp/source",
		Size: int64(len(payload)), SHA256: hex.EncodeToString(sum[:]), Status: models.FileTransferDelivering,
		Targets: []*models.FileTransferTarget{failing},
	}
	if err := st.CreateFileTransfer(context.Background(), failTransfer); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(transfers.blobPath(failTransfer.ID), payload, 0o600); err != nil {
		t.Fatal(err)
	}
	sessions.Unregister("target-node")
	failConn := &fakeDataPlaneConn{nodeID: "target-node", available: true, request: func(context.Context, string, string, io.Reader, int64) (*http.Response, error) {
		return &http.Response{StatusCode: http.StatusUnprocessableEntity, Body: io.NopCloser(strings.NewReader(`{"code":"sha256_mismatch"}`))}, nil
	}}
	sessions.Register(failConn)
	transfers.dispatchTarget(context.Background(), failTransfer, failing)
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		current, err := st.GetFileTransfer(context.Background(), failTransfer.ID)
		if err == nil && current.Targets[0].Status == models.FileTargetFailed {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	currentFail, _ := st.GetFileTransfer(context.Background(), failTransfer.ID)
	if currentFail == nil || currentFail.Targets[0].Status != models.FileTargetFailed {
		t.Fatalf("inbound target push failure not terminal: %+v", currentFail)
	}
}

func TestInboundArtifactPushAndUpgradePush(t *testing.T) {
	st, sessions, nodes, _ := newDataPlaneTransferHarness(t)
	// ArtifactManager 使用独立的存储目录；写入实际文件后推送到 inbound fake。
	am, err := NewArtifactManager(st, t.TempDir(), sessions, NewSyncManager(st, NewRevisionManager(st), sessions, nodes))
	if err != nil {
		t.Fatal(err)
	}
	payload := []byte("artifact bytes")
	sum := sha256.Sum256(payload)
	artifactPath := filepath.Join(t.TempDir(), "artifact.bin")
	if err := os.WriteFile(artifactPath, payload, 0o644); err != nil {
		t.Fatal(err)
	}
	art := &models.Artifact{ID: "artifact-1", Name: "a", Version: "1", Architecture: "amd64", Filename: "a", Size: int64(len(payload)), SHA256: hex.EncodeToString(sum[:]), StoragePath: artifactPath}
	if err := st.CreateArtifact(context.Background(), art); err != nil {
		t.Fatal(err)
	}
	var artifactBody, binaryBody []byte
	conn := &fakeDataPlaneConn{nodeID: "target-node", available: true, request: func(_ context.Context, method, path string, body io.Reader, _ int64) (*http.Response, error) {
		data, _ := io.ReadAll(body)
		switch {
		case strings.Contains(path, "/agent-data/v1/artifacts/artifact-1"):
			artifactBody = data
		case strings.Contains(path, "/agent-data/v1/agent-binary"):
			binaryBody = data
		default:
			t.Errorf("unexpected data plane request = %s %s", method, path)
		}
		return &http.Response{StatusCode: http.StatusCreated, Body: io.NopCloser(strings.NewReader(`{"ok":true}`))}, nil
	}}
	sessions.Register(conn)
	if err := am.PushInboundByID(context.Background(), "target-node", art.ID); err != nil {
		t.Fatalf("push artifact: %v", err)
	}
	if !bytes.Equal(artifactBody, payload) {
		t.Fatalf("artifact body = %q", artifactBody)
	}

	// 自升级：先 PUT 二进制，再发 RUN_EXECUTION(staged=true)。
	binary := []byte("agent binary")
	binarySum := sha256.Sum256(binary)
	execMgr := NewExecutionManager(st, sessions, nodes, slog.New(slog.NewTextHandler(io.Discard, nil)))
	execMgr.SetAgentBinaryProvider(func(arch string) ([]byte, string, error) {
		if arch != "amd64" {
			t.Fatalf("arch = %s", arch)
		}
		return binary, hex.EncodeToString(binarySum[:]), nil
	})
	node := &models.Node{ID: "target-node", AgentID: "agent-target-node", Hostname: "target-node", Status: models.NodeStatusOnline, DeploymentMode: models.DeploymentModeNative, Arch: "amd64"}
	if err := st.UpsertNode(context.Background(), node); err != nil {
		t.Fatal(err)
	}
	ex := &models.Execution{ID: "upgrade-1", NodeID: node.ID, Status: models.ExecStatusPending}
	if err := execMgr.dispatchUpgrade(ex, node, ""); err != nil {
		t.Fatalf("dispatch upgrade: %v", err)
	}
	conn.mu.Lock()
	sent := append([]protocol.Envelope(nil), conn.sent...)
	conn.mu.Unlock()
	var run protocol.RunExecutionPayload
	found := false
	for _, env := range sent {
		if env.Type == protocol.MsgRunExecution {
			if err := json.Unmarshal(env.Payload, &run); err != nil {
				t.Fatal(err)
			}
			found = true
		}
	}
	if !found || !run.AgentBinaryStaged || run.AgentBinarySHA256 != hex.EncodeToString(binarySum[:]) || run.AgentBinarySize != int64(len(binary)) {
		t.Fatalf("upgrade run payload = %+v found=%t", run, found)
	}
	if !bytes.Equal(artifactBody, payload) || !bytes.Equal(binaryBody, binary) {
		t.Fatalf("captured artifact=%q binary=%q", artifactBody, binaryBody)
	}
}

// TestOutboundSourceInboundTargetCombination 覆盖 8.8 的跨方向组合：
// 源节点走既有出站上传路径，目标节点走入站数据面 push，最终单一终态 SUCCESS。
func TestOutboundSourceInboundTargetCombination(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))
	source, sourceCred, _, err := nm.RegisterOrUpdate(ctx, "combo-source-agent", "combo-source", "10.0.0.1",
		"linux", "amd64", "1", models.DeploymentModeNative, false, map[string]bool{models.CapInboundDataPlane: true})
	if err != nil {
		t.Fatal(err)
	}
	target, _, _, err := nm.RegisterOrUpdate(ctx, "combo-target-agent", "combo-target", "10.0.0.2",
		"linux", "amd64", "1", models.DeploymentModeNative, false, map[string]bool{models.CapInboundDataPlane: true})
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionManager()
	sourceConn := &transferTestConn{nodeID: source.ID, sent: make(chan protocol.Envelope, 2)}
	var pushMu sync.Mutex
	var pushed []byte
	var pushedPath string
	var pushedSize int64
	var pushCalls int
	targetConn := &fakeDataPlaneConn{nodeID: target.ID, available: true, request: func(_ context.Context, method, path string, body io.Reader, size int64) (*http.Response, error) {
		if method != http.MethodPut {
			t.Errorf("inbound target method = %s, want PUT", method)
		}
		data, _ := io.ReadAll(body)
		pushMu.Lock()
		pushedPath = path
		pushed = data
		pushedSize = size
		pushCalls++
		pushMu.Unlock()
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(`{"ok":true}`))}, nil
	}}
	sessions.Register(sourceConn)
	sessions.Register(targetConn)
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	item, err := m.Create(ctx, source.ID, "/var/lib/nodesteer/source.txt",
		[]FileTransferTargetRequest{{NodeID: target.ID, DestinationPath: "/var/lib/nodesteer/target.txt"}})
	if err != nil {
		t.Fatal(err)
	}
	var upload protocol.FileUploadRequestPayload
	select {
	case msg := <-sourceConn.sent:
		if msg.Type != protocol.MsgFileUploadRequest || json.Unmarshal(msg.Payload, &upload) != nil {
			t.Fatalf("expected outbound upload request, got %s", msg.Type)
		}
	case <-time.After(time.Second):
		t.Fatal("source did not receive upload request")
	}
	payload := []byte("cross-direction payload")
	req := httptest.NewRequest(http.MethodPost, "/agent/transfers/"+item.ID+"/upload", io.NopCloser(bytes.NewReader(payload)))
	req.Header.Set("X-NodeSteer-Agent-ID", source.AgentID)
	req.Header.Set("X-NodeSteer-Agent-Token", sourceCred)
	req.Header.Set("X-NodeSteer-File-Size", strconv.Itoa(len(payload)))
	req.ContentLength = int64(len(payload))
	rec := httptest.NewRecorder()
	m.HandleAgentHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("upload status %d: %s", rec.Code, rec.Body.String())
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		current, err := st.GetFileTransfer(ctx, item.ID)
		if err == nil && current.Status == models.FileTransferSuccess &&
			len(current.Targets) == 1 && current.Targets[0].Status == models.FileTargetSuccess {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if current, _ := st.GetFileTransfer(ctx, item.ID); current != nil &&
		(current.Status != models.FileTransferSuccess || len(current.Targets) != 1 ||
			current.Targets[0].Status != models.FileTargetSuccess) {
		t.Fatalf("transfer = %+v", current)
	}
	pushMu.Lock()
	gotBody := append([]byte(nil), pushed...)
	gotPath := pushedPath
	gotSize := pushedSize
	gotCalls := pushCalls
	pushMu.Unlock()
	if !bytes.Equal(gotBody, payload) {
		current, _ := st.GetFileTransfer(ctx, item.ID)
		t.Fatalf("inbound target body = %q path=%q size=%d calls=%d transfer=%+v", gotBody, gotPath, gotSize, gotCalls, current)
	}
	if !strings.Contains(gotPath, "/agent-data/v1/transfers/"+item.ID+"/target?offset=0") {
		t.Fatalf("inbound target path = %q", gotPath)
	}
}
