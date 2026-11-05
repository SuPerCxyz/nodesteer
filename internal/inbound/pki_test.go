package inbound

import (
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestOpenAuthorityGeneratesAndReloadsPKI(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "inbound")
	first, err := OpenAuthority(Paths{Dir: dir})
	if err != nil {
		t.Fatalf("open authority: %v", err)
	}
	if !first.Generated() {
		t.Fatal("first open must report generated PKI")
	}
	if len(first.CACertPEM()) == 0 || len(first.HubClientCertPEM()) == 0 {
		t.Fatal("CA/client certificate PEM must not be empty")
	}
	firstFingerprint := first.HubClientFingerprint()
	if len(firstFingerprint) != 64 {
		t.Fatalf("client fingerprint = %q, want 64 hex chars", firstFingerprint)
	}
	for _, name := range []string{"ca.pem", "ca-key.pem", "client.pem", "client-key.pem"} {
		info, err := os.Stat(filepath.Join(dir, name))
		if err != nil {
			t.Fatalf("stat %s: %v", name, err)
		}
		if strings.Contains(name, "key") && info.Mode().Perm()&0o077 != 0 {
			t.Fatalf("%s permissions = %o, want 0600", name, info.Mode().Perm())
		}
	}

	second, err := OpenAuthority(Paths{Dir: dir})
	if err != nil {
		t.Fatalf("reload authority: %v", err)
	}
	if second.Generated() {
		t.Fatal("reload must not regenerate PKI")
	}
	if second.HubClientFingerprint() != firstFingerprint {
		t.Fatal("reload must preserve Hub client certificate")
	}
	if second.CACertFingerprint() != first.CACertFingerprint() {
		t.Fatal("reload must preserve CA certificate")
	}
}

func TestOpenAuthorityRejectsInsecureKeyPermissions(t *testing.T) {
	dir := t.TempDir()
	if _, err := OpenAuthority(Paths{Dir: dir}); err != nil {
		t.Fatalf("generate PKI: %v", err)
	}
	keyPath := filepath.Join(dir, "ca-key.pem")
	if err := os.Chmod(keyPath, 0o644); err != nil {
		t.Fatalf("chmod: %v", err)
	}
	if _, err := OpenAuthority(Paths{Dir: dir}); err == nil ||
		!strings.Contains(err.Error(), "group or others") {
		t.Fatalf("insecure key permissions err = %v", err)
	}
}

func TestOpenAuthorityRejectsPartialExplicitConfig(t *testing.T) {
	dir := t.TempDir()
	if _, err := OpenAuthority(Paths{Dir: dir, CACert: filepath.Join(dir, "ca.pem")}); err == nil {
		t.Fatal("CA cert without key must be rejected")
	}
	if _, err := OpenAuthority(Paths{Dir: dir, ClientCert: filepath.Join(dir, "client.pem")}); err == nil {
		t.Fatal("client cert without key must be rejected")
	}
	if _, err := OpenAuthority(Paths{
		Dir:    dir,
		CACert: filepath.Join(dir, "missing-ca.pem"),
		CAKey:  filepath.Join(dir, "missing-ca-key.pem"),
	}); err == nil || !strings.Contains(err.Error(), "configured inbound CA files") {
		t.Fatalf("explicit missing CA err = %v", err)
	}
}

func TestOpenAuthorityLoadsExplicitPKI(t *testing.T) {
	dir := t.TempDir()
	if _, err := OpenAuthority(Paths{Dir: dir}); err != nil {
		t.Fatalf("generate PKI: %v", err)
	}
	explicit := Paths{
		Dir:        t.TempDir(),
		CACert:     filepath.Join(dir, "ca.pem"),
		CAKey:      filepath.Join(dir, "ca-key.pem"),
		ClientCert: filepath.Join(dir, "client.pem"),
		ClientKey:  filepath.Join(dir, "client-key.pem"),
	}
	authority, err := OpenAuthority(explicit)
	if err != nil {
		t.Fatalf("load explicit PKI: %v", err)
	}
	if authority.Generated() {
		t.Fatal("explicit PKI must not be regenerated")
	}
}

func TestCreateServerCertificateSANAndEKU(t *testing.T) {
	authority, err := OpenAuthority(Paths{Dir: t.TempDir()})
	if err != nil {
		t.Fatalf("open authority: %v", err)
	}

	for _, tc := range []struct {
		name     string
		endpoint string
		dnsName  string
	}{
		{name: "dns", endpoint: "agent.example.com:18444", dnsName: "agent.example.com"},
		{name: "ip", endpoint: "203.0.113.10:18444", dnsName: "203.0.113.10"},
		{name: "ipv6", endpoint: "[2001:db8::10]:18444", dnsName: "2001:db8::10"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			issued, err := authority.CreateServerCertificate("node-1", tc.endpoint)
			if err != nil {
				t.Fatalf("issue certificate: %v", err)
			}
			if len(issued.CertPEM) == 0 || len(issued.KeyPEM) == 0 || len(issued.Fingerprint) != 64 {
				t.Fatalf("issued certificate incomplete: %+v", issued)
			}
			block, _ := pem.Decode(issued.CertPEM)
			if block == nil {
				t.Fatal("issued certificate is not PEM")
			}
			cert, err := x509.ParseCertificate(block.Bytes)
			if err != nil {
				t.Fatalf("parse issued certificate: %v", err)
			}
			roots := x509.NewCertPool()
			roots.AppendCertsFromPEM(authority.CACertPEM())
			if _, err := cert.Verify(x509.VerifyOptions{
				Roots:     roots,
				DNSName:   tc.dnsName,
				KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
			}); err != nil {
				t.Fatalf("verify issued certificate: %v", err)
			}
			if cert.SerialNumber.Text(16) != issued.Serial {
				t.Fatalf("serial = %s, want %s", cert.SerialNumber.Text(16), issued.Serial)
			}
			if !cert.NotAfter.Equal(issued.NotAfter) {
				t.Fatalf("not_after = %v, want %v", cert.NotAfter, issued.NotAfter)
			}
		})
	}
}

func TestNodeTokenEncryptionRoundTrip(t *testing.T) {
	authority, err := OpenAuthority(Paths{Dir: t.TempDir()})
	if err != nil {
		t.Fatalf("open authority: %v", err)
	}
	token, err := NewNodeToken()
	if err != nil {
		t.Fatalf("new token: %v", err)
	}
	if len(token) != 32 {
		t.Fatalf("token length = %d, want 32", len(token))
	}
	encrypted, err := authority.EncryptToken(token)
	if err != nil {
		t.Fatalf("encrypt token: %v", err)
	}
	if strings.Contains(encrypted, string(token)) {
		t.Fatal("ciphertext must not contain plaintext token")
	}
	decrypted, err := authority.DecryptToken(encrypted)
	if err != nil {
		t.Fatalf("decrypt token: %v", err)
	}
	if string(decrypted) != string(token) {
		t.Fatal("decrypted token differs from plaintext")
	}
	if _, err := authority.DecryptToken("not-base64"); err == nil {
		t.Fatal("invalid ciphertext must be rejected")
	}
}
