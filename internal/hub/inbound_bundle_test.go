package hub

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"log/slog"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestInboundBundleIssueRotateAndArchive(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	rm := NewRevisionManager(st)
	sessions := NewSessionManager()
	nodes := NewNodeManager(st, rm)
	authority, err := inbound.OpenAuthority(inbound.Paths{Dir: filepath.Join(t.TempDir(), "pki")})
	if err != nil {
		t.Fatal(err)
	}
	node := &models.Node{ID: "node-bundle", AgentID: "agent-bundle", Hostname: "bundle-host", Endpoint: "agent.example.com:18444", Status: models.NodeStatusOnline}
	if err := st.UpsertNode(ctx, node); err != nil {
		t.Fatal(err)
	}
	service := NewInboundBundleService(authority, nodes, sessions, "registration-token", "https://hub.example:8443", "https://hub.example", logger)
	bundle, err := service.Issue(ctx, node.ID, false)
	if err != nil {
		t.Fatalf("issue bundle: %v", err)
	}
	if len(bundle.Archive) == 0 || !strings.HasSuffix(bundle.Filename, ".tar.gz") {
		t.Fatalf("bundle = %+v", bundle)
	}
	files := readBundleFiles(t, bundle.Archive)
	for _, name := range []string{"ca.pem", "agent-server.pem", "agent-server-key.pem", "hub-client-fingerprint.txt", "inbound-token", "agent-inbound.yaml", "install-inbound.sh", "README.txt"} {
		if files[name] == nil {
			t.Fatalf("bundle missing %s", name)
		}
	}
	if !strings.Contains(string(files["agent-inbound.yaml"]), `endpoint: agent.example.com:18444`) ||
		!strings.Contains(string(files["agent-inbound.yaml"]), "connection_mode: auto") {
		t.Fatalf("agent config = %s", files["agent-inbound.yaml"])
	}
	issued, err := st.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !issued.InboundProvisioned || issued.InboundCertFingerprint == "" || issued.InboundTokenEnc == "" || issued.InboundCertNotAfter == "" {
		t.Fatalf("node inbound identity not persisted: %+v", issued)
	}
	firstToken := issued.InboundTokenEnc
	firstFingerprint := issued.InboundCertFingerprint
	if _, err := service.Issue(ctx, node.ID, false); !errors.Is(err, ErrInboundBundleAlreadyIssued) {
		t.Fatalf("second issue err = %v, want already issued", err)
	}

	// 轮换旧入站会话/身份并更新指纹与令牌密文。
	sessions.Register(&fakeAgentConn{nodeID: node.ID, origin: models.ConnectionModeInbound})
	rotated, err := service.Issue(ctx, node.ID, true)
	if err != nil {
		t.Fatalf("rotate bundle: %v", err)
	}
	if len(rotated.Archive) == 0 {
		t.Fatal("rotated bundle archive empty")
	}
	if _, ok := sessions.Get(node.ID); ok {
		t.Fatal("rotate must close current inbound session")
	}
	after, err := st.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatal(err)
	}
	if after.InboundTokenEnc == firstToken || after.InboundCertFingerprint == firstFingerprint {
		t.Fatal("rotate must replace token and certificate")
	}
}

func readBundleFiles(t *testing.T, archive []byte) map[string][]byte {
	t.Helper()
	gz, err := gzip.NewReader(bytes.NewReader(archive))
	if err != nil {
		t.Fatal(err)
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	files := map[string][]byte{}
	for {
		header, err := tr.Next()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		data, err := io.ReadAll(tr)
		if err != nil {
			t.Fatal(err)
		}
		files[header.Name] = data
		if strings.Contains(header.Name, "key") || header.Name == "inbound-token" {
			if header.Mode&0o077 != 0 {
				t.Fatalf("%s mode = %o, want private", header.Name, header.Mode)
			}
		}
	}
	return files
}
