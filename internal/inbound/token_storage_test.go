package inbound

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// TestEncryptedNodeTokenPersistsWithoutPlaintext 覆盖 v27 inbound_token_enc：
// Hub 只落库 AES-256-GCM 密文，读取后可解密回原令牌。
func TestEncryptedNodeTokenPersistsWithoutPlaintext(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	defer st.Close()
	authority, err := OpenAuthority(Paths{Dir: t.TempDir()})
	if err != nil {
		t.Fatalf("open authority: %v", err)
	}
	node := &models.Node{ID: "node-token", AgentID: "agent-token", Hostname: "token-host", Endpoint: "agent.example.com:18444", Status: models.NodeStatusPending}
	if err := st.UpsertNode(ctx, node); err != nil {
		t.Fatalf("insert node: %v", err)
	}
	token, err := NewNodeToken()
	if err != nil {
		t.Fatalf("new token: %v", err)
	}
	encrypted, err := authority.EncryptToken(token)
	if err != nil {
		t.Fatalf("encrypt token: %v", err)
	}
	if strings.Contains(encrypted, string(token)) {
		t.Fatal("stored ciphertext must not contain plaintext token")
	}
	issued, err := authority.CreateServerCertificate(node.ID, node.Endpoint)
	if err != nil {
		t.Fatalf("issue certificate: %v", err)
	}
	if err := st.UpdateNodeInboundIdentity(ctx, node.ID, true, issued.Fingerprint, issued.Serial, issued.NotAfter, encrypted); err != nil {
		t.Fatalf("persist inbound identity: %v", err)
	}
	got, err := st.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatalf("read node: %v", err)
	}
	if got.InboundTokenEnc != encrypted || strings.Contains(got.InboundTokenEnc, string(token)) {
		t.Fatalf("inbound token column = %q", got.InboundTokenEnc)
	}
	if !got.InboundProvisioned || got.InboundCertFingerprint != issued.Fingerprint || got.InboundCertSerial != issued.Serial {
		t.Fatalf("inbound identity = %+v", got)
	}
	decrypted, err := authority.DecryptToken(got.InboundTokenEnc)
	if err != nil {
		t.Fatalf("decrypt stored token: %v", err)
	}
	if string(decrypted) != string(token) {
		t.Fatal("decrypted stored token differs")
	}
	if got.Inbound.CertNotAfter != issued.NotAfter.UTC().Format(time.RFC3339Nano) {
		t.Fatalf("cert_not_after = %q", got.Inbound.CertNotAfter)
	}
}
