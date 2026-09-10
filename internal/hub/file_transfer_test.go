package hub

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/protocol"
	"github.com/cadentra/cadentra/internal/store"
)

type transferTestConn struct {
	nodeID string
	sent   chan protocol.Envelope
}

func (c *transferTestConn) NodeID() string { return c.nodeID }
func (c *transferTestConn) Send(msg protocol.Envelope) error {
	c.sent <- msg
	return nil
}
func (c *transferTestConn) Close() error { return nil }

func TestFileTransferUploadAndDelivery(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	rm := NewRevisionManager(st)
	nm := NewNodeManager(st, rm)
	source, sourceCred, _, err := nm.RegisterOrUpdate(ctx, "source-agent", "source", "10.0.0.1", "linux", "amd64", "1", models.DeploymentModeNative, false, map[string]bool{models.CapScript: true})
	if err != nil {
		t.Fatal(err)
	}
	target, targetCred, _, err := nm.RegisterOrUpdate(ctx, "target-agent", "target", "10.0.0.2", "linux", "amd64", "1", models.DeploymentModeNative, false, map[string]bool{models.CapScript: true})
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionManager()
	sourceConn := &transferTestConn{nodeID: source.ID, sent: make(chan protocol.Envelope, 2)}
	targetConn := &transferTestConn{nodeID: target.ID, sent: make(chan protocol.Envelope, 2)}
	sessions.Register(sourceConn)
	sessions.Register(targetConn)
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	item, err := m.Create(ctx, source.ID, "/var/lib/cadentra/source.txt", []FileTransferTargetRequest{{NodeID: target.ID, DestinationPath: "/var/lib/cadentra/target.txt"}})
	if err != nil {
		t.Fatal(err)
	}
	var upload protocol.FileUploadRequestPayload
	msg := <-sourceConn.sent
	if msg.Type != protocol.MsgFileUploadRequest || json.Unmarshal(msg.Payload, &upload) != nil {
		t.Fatalf("expected upload request, got %s", msg.Type)
	}
	if upload.TransferID != item.ID || upload.Offset != 0 {
		t.Fatalf("unexpected upload request: %+v", upload)
	}

	body := []byte("cadentra-transfer")
	req := httptest.NewRequest(http.MethodPost, "/agent/transfers/"+item.ID+"/upload", io.NopCloser(bytesReader(body)))
	req.Header.Set("X-Cadentra-Agent-ID", source.AgentID)
	req.Header.Set("X-Cadentra-Agent-Token", sourceCred)
	req.Header.Set("X-Cadentra-File-Size", "17")
	req.Header.Set("X-Cadentra-File-Mode", "644")
	req.ContentLength = int64(len(body))
	rec := httptest.NewRecorder()
	m.HandleAgentHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("upload status %d: %s", rec.Code, rec.Body.String())
	}
	var delivery protocol.FileDeliveryRequestPayload
	msg = <-targetConn.sent
	if msg.Type != protocol.MsgFileDeliveryRequest || json.Unmarshal(msg.Payload, &delivery) != nil {
		t.Fatalf("expected delivery request, got %s", msg.Type)
	}
	if delivery.SHA256 == "" || delivery.Size != int64(len(body)) {
		t.Fatalf("missing delivery metadata: %+v", delivery)
	}

	download := httptest.NewRequest(http.MethodGet, "/agent/transfers/"+item.ID+"/download", nil)
	download.Header.Set("X-Cadentra-Agent-ID", target.AgentID)
	download.Header.Set("X-Cadentra-Agent-Token", targetCred)
	downloadRec := httptest.NewRecorder()
	m.HandleAgentHTTP(downloadRec, download)
	if downloadRec.Code != http.StatusOK || downloadRec.Body.String() != string(body) {
		t.Fatalf("download failed: status=%d body=%q", downloadRec.Code, downloadRec.Body.String())
	}
	if err := m.HandleDeliveryResult(ctx, target.ID, protocol.FileDeliveryResultPayload{TransferID: item.ID, OK: true}); err != nil {
		t.Fatal(err)
	}
	final, err := m.Get(ctx, item.ID)
	if err != nil {
		t.Fatal(err)
	}
	if final.Status != models.FileTransferSuccess || final.Targets[0].Status != models.FileTargetSuccess {
		t.Fatalf("unexpected final state: %+v", final)
	}
}

func TestFileTransferAllTargetsSuccess(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	nm := NewNodeManager(st, NewRevisionManager(st))
	source, sourceCred, _, err := nm.RegisterOrUpdate(ctx, "source-agent", "source", "10.0.0.1", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	targetA, _, _, err := nm.RegisterOrUpdate(ctx, "target-a", "target-a", "10.0.0.2", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	targetB, _, _, err := nm.RegisterOrUpdate(ctx, "target-b", "target-b", "10.0.0.3", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	sessions := NewSessionManager()
	sourceConn := &transferTestConn{nodeID: source.ID, sent: make(chan protocol.Envelope, 2)}
	targetAConn := &transferTestConn{nodeID: targetA.ID, sent: make(chan protocol.Envelope, 1)}
	targetBConn := &transferTestConn{nodeID: targetB.ID, sent: make(chan protocol.Envelope, 1)}
	sessions.Register(sourceConn)
	sessions.Register(targetAConn)
	sessions.Register(targetBConn)
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	item, err := m.Create(ctx, source.ID, "/source", []FileTransferTargetRequest{
		{NodeID: targetA.ID, DestinationPath: "/target-a"},
		{NodeID: targetB.ID, DestinationPath: "/target-b"},
	})
	if err != nil {
		t.Fatal(err)
	}
	<-sourceConn.sent

	body := []byte("relay")
	req := httptest.NewRequest(http.MethodPost, "/agent/transfers/"+item.ID+"/upload", bytesReader(body))
	req.Header.Set("X-Cadentra-Agent-ID", source.AgentID)
	req.Header.Set("X-Cadentra-Agent-Token", sourceCred)
	req.Header.Set("X-Cadentra-File-Size", "5")
	req.ContentLength = int64(len(body))
	rec := httptest.NewRecorder()
	m.HandleAgentHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("upload status %d: %s", rec.Code, rec.Body.String())
	}
	<-targetAConn.sent
	<-targetBConn.sent

	var wg sync.WaitGroup
	errs := make(chan error, 2)
	for _, result := range []struct {
		nodeID string
		result protocol.FileDeliveryResultPayload
	}{
		{nodeID: targetA.ID, result: protocol.FileDeliveryResultPayload{TransferID: item.ID, OK: true}},
		{nodeID: targetB.ID, result: protocol.FileDeliveryResultPayload{TransferID: item.ID, OK: true}},
	} {
		wg.Add(1)
		go func(nodeID string, result protocol.FileDeliveryResultPayload) {
			defer wg.Done()
			errs <- m.HandleDeliveryResult(ctx, nodeID, result)
		}(result.nodeID, result.result)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	state, err := m.Get(ctx, item.ID)
	if err != nil || state.Status != models.FileTransferSuccess {
		t.Fatalf("expected all-success aggregate, err=%v state=%+v", err, state)
	}
	if targetStatus(state, targetA.ID) != models.FileTargetSuccess || targetStatus(state, targetB.ID) != models.FileTargetSuccess {
		t.Fatalf("expected both targets successful, state=%+v", state)
	}
}

func TestFileTransferRejectsWrongAgentAndPath(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	rm := NewRevisionManager(st)
	nm := NewNodeManager(st, rm)
	source, sourceCred, _, err := nm.RegisterOrUpdate(ctx, "source-agent", "source", "10.0.0.1", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	target, _, _, err := nm.RegisterOrUpdate(ctx, "target-agent", "target", "10.0.0.2", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	m, err := NewFileTransferManager(st, filepath.Join(t.TempDir(), "hub"), "http://hub:8443", NewSessionManager(), nm, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	item, err := m.Create(ctx, source.ID, "/source", []FileTransferTargetRequest{{NodeID: target.ID, DestinationPath: "/target"}})
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(http.MethodPost, "/agent/transfers/"+item.ID+"/upload", bytesReader([]byte("x")))
	req.Header.Set("X-Cadentra-Agent-ID", target.AgentID)
	req.Header.Set("X-Cadentra-Agent-Token", sourceCred)
	rec := httptest.NewRecorder()
	m.HandleAgentHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected wrong credential rejection, got %d", rec.Code)
	}
	if _, err := m.Create(ctx, source.ID, "relative", []FileTransferTargetRequest{{NodeID: target.ID, DestinationPath: "/target"}}); err == nil {
		t.Fatal("expected relative source path rejection")
	}
}

func TestFileTransferTargetFailureIsolatedAndRetryable(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))
	source, sourceCred, _, err := nm.RegisterOrUpdate(ctx, "source-agent", "source", "10.0.0.1", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	targetA, _, _, err := nm.RegisterOrUpdate(ctx, "target-a", "target-a", "10.0.0.2", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	targetB, _, _, err := nm.RegisterOrUpdate(ctx, "target-b", "target-b", "10.0.0.3", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionManager()
	sourceConn := &transferTestConn{nodeID: source.ID, sent: make(chan protocol.Envelope, 2)}
	targetConn := &transferTestConn{nodeID: targetA.ID, sent: make(chan protocol.Envelope, 2)}
	sessions.Register(sourceConn)
	sessions.Register(targetConn)
	m, err := NewFileTransferManager(st, t.TempDir(), "http://hub:8443", sessions, nm, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	item, err := m.Create(ctx, source.ID, "/source", []FileTransferTargetRequest{
		{NodeID: targetA.ID, DestinationPath: "/target-a"},
		{NodeID: targetB.ID, DestinationPath: "/target-b"},
	})
	if err != nil {
		t.Fatal(err)
	}
	<-sourceConn.sent
	body := []byte("relay")
	req := httptest.NewRequest(http.MethodPost, "/agent/transfers/"+item.ID+"/upload", bytesReader(body))
	req.Header.Set("X-Cadentra-Agent-ID", source.AgentID)
	req.Header.Set("X-Cadentra-Agent-Token", sourceCred)
	req.Header.Set("X-Cadentra-File-Size", "5")
	req.ContentLength = int64(len(body))
	rec := httptest.NewRecorder()
	m.HandleAgentHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("upload status %d", rec.Code)
	}
	<-targetConn.sent
	state, err := m.Get(ctx, item.ID)
	if err != nil || targetStatus(state, targetB.ID) != models.FileTargetPending {
		t.Fatalf("offline target should remain pending: %v %+v", err, state)
	}
	if err := m.HandleDeliveryResult(ctx, targetA.ID, protocol.FileDeliveryResultPayload{TransferID: item.ID, OK: true}); err != nil {
		t.Fatal(err)
	}
	if err := m.HandleDeliveryResult(ctx, targetB.ID, protocol.FileDeliveryResultPayload{TransferID: item.ID, OK: false, Error: "offline"}); err != nil {
		t.Fatal(err)
	}
	state, err = m.Get(ctx, item.ID)
	if err != nil || state.Status != models.FileTransferFailed || targetStatus(state, targetA.ID) != models.FileTargetSuccess {
		t.Fatalf("target failure was not isolated: %v %+v", err, state)
	}
	targetBConn := &transferTestConn{nodeID: targetB.ID, sent: make(chan protocol.Envelope, 2)}
	sessions.Register(targetBConn)
	if _, err := m.Retry(ctx, item.ID, targetB.ID); err != nil {
		t.Fatal(err)
	}
	select {
	case <-targetBConn.sent:
	case <-time.After(time.Second):
		t.Fatal("retry did not dispatch to target")
	}
}

func TestFileTransferCancelAndPersistence(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	st, err := store.OpenSQLite(filepath.Join(dir, "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	nm := NewNodeManager(st, NewRevisionManager(st))
	source, _, _, err := nm.RegisterOrUpdate(ctx, "source-agent", "source", "10.0.0.1", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	target, _, _, err := nm.RegisterOrUpdate(ctx, "target-agent", "target", "10.0.0.2", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionManager()
	sourceConn := &transferTestConn{nodeID: source.ID, sent: make(chan protocol.Envelope, 4)}
	targetConn := &transferTestConn{nodeID: target.ID, sent: make(chan protocol.Envelope, 4)}
	sessions.Register(sourceConn)
	sessions.Register(targetConn)
	m, err := NewFileTransferManager(st, dir, "http://hub:8443", sessions, nm, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	item, err := m.Create(ctx, source.ID, "/source", []FileTransferTargetRequest{{NodeID: target.ID, DestinationPath: "/target"}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := m.Cancel(ctx, item.ID); err != nil {
		t.Fatal(err)
	}
	canceled, err := m.Get(ctx, item.ID)
	if err != nil || canceled.Status != models.FileTransferCanceled || targetStatus(canceled, target.ID) != models.FileTargetCanceled {
		t.Fatalf("unexpected canceled state: %v %+v", err, canceled)
	}
	select {
	case <-sourceConn.sent:
	case <-time.After(time.Second):
		t.Fatal("cancel was not sent to source")
	}
	if err := st.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := store.OpenSQLite(filepath.Join(dir, "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	persisted, err := reopened.GetFileTransfer(ctx, item.ID)
	if err != nil || persisted.Status != models.FileTransferCanceled {
		t.Fatalf("transfer state was not persisted: %v %+v", err, persisted)
	}
}

func targetStatus(t *models.FileTransfer, nodeID string) string {
	for _, target := range t.Targets {
		if target.NodeID == nodeID {
			return target.Status
		}
	}
	return ""
}

func bytesReader(b []byte) io.Reader { return &testBytesReader{data: b} }

type testBytesReader struct {
	data []byte
	pos  int
}

func (r *testBytesReader) Read(p []byte) (int, error) {
	if r.pos == len(r.data) {
		return 0, io.EOF
	}
	n := copy(p, r.data[r.pos:])
	r.pos += n
	return n, nil
}

var _ = os.FileMode(0)
