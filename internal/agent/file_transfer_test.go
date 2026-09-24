package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/agent/connection"
	"github.com/SuPerCxyz/nodesteer/internal/agent/host"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
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
