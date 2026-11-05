package hubserver

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func inboundTestLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func TestPrepareInboundDisabledDoesNotGeneratePKI(t *testing.T) {
	dir := t.TempDir()
	runtime, err := prepareInbound(Config{DataDir: dir}, inboundTestLogger())
	if err != nil {
		t.Fatalf("prepare inbound: %v", err)
	}
	if runtime.enabled || runtime.authority != nil || runtime.policy == nil {
		t.Fatalf("disabled runtime = %+v", runtime)
	}
	if _, err := os.Stat(filepath.Join(dir, "inbound")); !os.IsNotExist(err) {
		t.Fatalf("disabled inbound must not create pki directory, err=%v", err)
	}
}

func TestPrepareInboundEnabledGeneratesPKI(t *testing.T) {
	dir := t.TempDir()
	runtime, err := prepareInbound(Config{
		DataDir:                dir,
		InboundEnabled:         true,
		InboundDialConcurrency: 4,
		InboundDialTimeout:     15 * time.Second,
		InboundBackoffInitial:  2 * time.Second,
		InboundBackoffMax:      30 * time.Second,
	}, inboundTestLogger())
	if err != nil {
		t.Fatalf("prepare inbound: %v", err)
	}
	if !runtime.enabled || runtime.authority == nil {
		t.Fatalf("enabled runtime = %+v", runtime)
	}
	if runtime.authority.CACertFingerprint() == "" || runtime.authority.HubClientFingerprint() == "" {
		t.Fatal("generated PKI must expose CA/client fingerprints")
	}
	for _, name := range []string{"ca.pem", "ca-key.pem", "client.pem", "client-key.pem"} {
		if _, err := os.Stat(filepath.Join(dir, "inbound", name)); err != nil {
			t.Fatalf("expected generated %s: %v", name, err)
		}
	}
}

func TestHubWiresInboundDialerWhenEnabled(t *testing.T) {
	dir := t.TempDir()
	h, err := New(Config{
		DataDir:                dir,
		ArtifactDir:            filepath.Join(dir, "artifacts"),
		InboundEnabled:         true,
		InboundDialConcurrency: 2,
		InboundAllowedPorts:    []int{18444},
	}, inboundTestLogger())
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	defer h.Close()
	if !h.InboundEnabled() || h.InboundAuthority() == nil || h.InboundDialer() == nil {
		t.Fatalf("inbound runtime not wired: enabled=%t authority=%v dialer=%v",
			h.InboundEnabled(), h.InboundAuthority(), h.InboundDialer())
	}
}

func TestPrepareInboundRejectsInvalidConfig(t *testing.T) {
	cases := []Config{
		{DataDir: t.TempDir(), InboundEnabled: true, InboundCAFile: "/tmp/ca.pem"}, // CA key missing
		{DataDir: t.TempDir(), InboundEnabled: true, InboundDialConcurrency: 300},
		{DataDir: t.TempDir(), InboundEnabled: true, InboundDeniedCIDRs: []string{"bad-cidr"}},
		{DataDir: t.TempDir(), InboundEnabled: true, InboundBackoffInitial: 30 * time.Second, InboundBackoffMax: 10 * time.Second},
	}
	for i, cfg := range cases {
		if _, err := prepareInbound(cfg, inboundTestLogger()); err == nil {
			t.Fatalf("case %d: expected config error", i)
		}
	}
}
