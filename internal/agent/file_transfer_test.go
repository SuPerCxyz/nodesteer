package agent

import (
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
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent/connection"
	"github.com/SuPerCxyz/nodesteer/internal/agent/host"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
)

func TestAgentFileUploadAndDownload(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "source"), 0o755); err != nil {
		t.Fatal(err)
	}
	content := []byte("agent-file-content")
	if err := os.WriteFile(filepath.Join(root, "source", "input.txt"), content, 0o640); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			body, err := io.ReadAll(r.Body)
			if err != nil || string(body) != string(content) {
				t.Fatalf("unexpected upload body: %v %q", err, body)
			}
			if r.Header.Get("X-NodeSteer-Agent-ID") != "agent-1" {
				t.Fatalf("missing agent id")
			}
			return
		}
		w.Write(content)
	}))
	defer server.Close()

	a := &Agent{
		host:    host.NewNativeHostAdapter(),
		conn:    connection.New("", "", "", nil, slog.Default(), nil),
		agentID: "agent-1", credential: "credential-1",
	}
	if err := a.uploadFile(context.Background(), protocol.FileUploadRequestPayload{
		TransferID: "transfer-1", SourcePath: filepath.Join(root, "source", "input.txt"), UploadURL: server.URL,
	}); err != nil {
		t.Fatalf("upload: %v", err)
	}
	hash := sha256.Sum256(content)
	sha := hex.EncodeToString(hash[:])
	if err := a.downloadFile(context.Background(), protocol.FileDeliveryRequestPayload{
		TransferID: "transfer-1", DownloadURL: server.URL, SHA256: sha, Size: int64(len(content)),
		DestinationPath: filepath.Join(root, "target", "output.txt"), Mode: 0o640,
	}); err != nil {
		t.Fatalf("download: %v", err)
	}
	got, err := os.ReadFile(filepath.Join(root, "target", "output.txt"))
	if err != nil || string(got) != string(content) {
		t.Fatalf("downloaded content mismatch: %v %q", err, got)
	}
}

func TestAgentFileDownloadChecksumFailureKeepsDestination(t *testing.T) {
	root := t.TempDir()
	destination := filepath.Join(root, "target.txt")
	if err := os.WriteFile(destination, []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("corrupt"))
	}))
	defer server.Close()
	a := &Agent{
		host:    host.NewNativeHostAdapter(),
		conn:    connection.New("", "", "", nil, slog.Default(), nil),
		agentID: "agent-1", credential: "credential-1",
	}
	err := a.downloadFile(context.Background(), protocol.FileDeliveryRequestPayload{
		TransferID: "transfer-2", DownloadURL: server.URL, SHA256: "bad", Size: 7, DestinationPath: destination, Mode: 0o644,
	})
	if err == nil {
		t.Fatal("expected checksum failure")
	}
	got, readErr := os.ReadFile(destination)
	if readErr != nil || string(got) != "old" {
		t.Fatalf("destination changed after checksum failure: %v %q", readErr, got)
	}
}

// TestAgentTransferSupersessionKeepsCurrentCancelable SOAK-20261018-01 同源问题：
// 新尝试取代旧尝试时旧尝试被取消；被取代尝试收尾不得清掉新尝试的取消登记，
// 否则后续 Hub 的取消指令无法命中仍在进行的尝试。
func TestAgentTransferSupersessionKeepsCurrentCancelable(t *testing.T) {
	a := &Agent{ctx: context.Background(), transferAttempts: map[string]*transferAttempt{}}

	ctx1, done1 := a.startFileTransfer("transfer-supersede")
	ctx2, done2 := a.startFileTransfer("transfer-supersede")
	if ctx1.Err() == nil {
		t.Fatal("superseded attempt must be canceled")
	}
	if ctx2.Err() != nil {
		t.Fatal("current attempt must stay live")
	}

	// 被取代尝试收尾：登记必须仍指向当前尝试。
	done1()
	a.OnFileTransferCancel(protocol.NewEnvelope(protocol.MsgFileTransferCancel, "transfer-supersede",
		protocol.FileTransferCancelPayload{TransferID: "transfer-supersede"}))
	if ctx2.Err() == nil {
		t.Fatal("cancel must reach the current attempt after superseded cleanup")
	}

	// 当前尝试收尾：登记清空；再次取消是安全的空操作。
	done2()
	if len(a.transferAttempts) != 0 {
		t.Fatalf("current attempt cleanup must release the registry: %+v", a.transferAttempts)
	}
	a.OnFileTransferCancel(protocol.NewEnvelope(protocol.MsgFileTransferCancel, "transfer-supersede",
		protocol.FileTransferCancelPayload{TransferID: "transfer-supersede"}))
}

// TestAgentTransferResultEchoesAttempt 结果回执必须原样回传 Hub 下发的世代号，
// Hub 侧才能忽略被取代尝试的结果（缺陷 SOAK-20261018-01 的 Agent 半边）。
func TestAgentTransferResultEchoesAttempt(t *testing.T) {
	root := t.TempDir()
	content := []byte("attempt-echo-content")
	sourcePath := filepath.Join(root, "source.bin")
	if err := os.WriteFile(sourcePath, content, 0o640); err != nil {
		t.Fatal(err)
	}
	upload := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer upload.Close()
	hash := sha256.Sum256(content)
	download := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write(content)
	}))
	defer download.Close()

	received := make(chan protocol.Envelope, 8)
	hub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"nodesteer"}})
		if err != nil {
			return
		}
		defer c.CloseNow()
		for {
			typ, data, err := c.Read(r.Context())
			if err != nil {
				return
			}
			if typ != websocket.MessageText {
				continue
			}
			var env protocol.Envelope
			if json.Unmarshal(data, &env) != nil {
				continue
			}
			if env.Type == protocol.MsgHello {
				ack, _ := json.Marshal(protocol.NewEnvelope(protocol.MsgHelloAck, "", protocol.HelloAckPayload{Accepted: true}))
				if err := c.Write(r.Context(), websocket.MessageText, ack); err != nil {
					return
				}
				continue
			}
			received <- env
		}
	}))
	defer hub.Close()

	logger := slog.New(slog.DiscardHandler)
	conn := connection.New("ws://"+hub.Listener.Addr().String(), "", "agent-1", nil, logger, nil)
	ctx, cancel := context.WithCancel(t.Context())
	defer cancel()
	go conn.Run(ctx, func() *protocol.HelloPayload { return &protocol.HelloPayload{} })
	deadline := time.Now().Add(5 * time.Second)
	for !conn.IsConnected() {
		if time.Now().After(deadline) {
			t.Fatal("agent connection timeout")
		}
		time.Sleep(10 * time.Millisecond)
	}

	a := &Agent{
		ctx: ctx, host: host.NewNativeHostAdapter(), conn: conn,
		agentID: "agent-1", credential: "credential-1", logger: logger,
		transferAttempts: map[string]*transferAttempt{},
	}

	a.OnFileUploadRequest(protocol.NewEnvelope(protocol.MsgFileUploadRequest, "t-echo", protocol.FileUploadRequestPayload{
		TransferID: "t-echo", SourcePath: sourcePath, UploadURL: upload.URL, Attempt: 7,
	}))
	select {
	case env := <-received:
		if env.Type != protocol.MsgFileUploadResult {
			t.Fatalf("expected upload result, got %s", env.Type)
		}
		var p protocol.FileUploadResultPayload
		if err := json.Unmarshal(env.Payload, &p); err != nil {
			t.Fatal(err)
		}
		if !p.OK || p.Attempt != 7 {
			t.Fatalf("upload result must echo attempt 7: %+v", p)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("upload result not sent")
	}

	a.OnFileDeliveryRequest(protocol.NewEnvelope(protocol.MsgFileDeliveryRequest, "t-echo-2", protocol.FileDeliveryRequestPayload{
		TransferID: "t-echo-2", DownloadURL: download.URL, SHA256: hex.EncodeToString(hash[:]),
		Size: int64(len(content)), DestinationPath: filepath.Join(root, "out.bin"), Mode: 0o644, Attempt: 9,
	}))
	select {
	case env := <-received:
		if env.Type != protocol.MsgFileDeliveryResult {
			t.Fatalf("expected delivery result, got %s", env.Type)
		}
		var p protocol.FileDeliveryResultPayload
		if err := json.Unmarshal(env.Payload, &p); err != nil {
			t.Fatal(err)
		}
		if !p.OK || p.Attempt != 9 {
			t.Fatalf("delivery result must echo attempt 9: %+v", p)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("delivery result not sent")
	}
}
