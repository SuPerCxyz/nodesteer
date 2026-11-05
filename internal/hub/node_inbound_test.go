package hub

import (
	"context"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestPrepareEnrollmentPreservesOrResetsInboundIdentity(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))

	pending, err := nm.PrepareEnrollment(ctx, "inbound-node", "10.0.0.60", "10.0.0.60:18444", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("prepare enrollment: %v", err)
	}
	if pending.Endpoint != "10.0.0.60:18444" {
		t.Fatalf("endpoint = %q", pending.Endpoint)
	}
	if err := st.UpdateNodeInboundIdentity(ctx, pending.ID, true, "fingerprint", "serial", time.Now().Add(time.Hour), "token-enc"); err != nil {
		t.Fatalf("set inbound identity: %v", err)
	}
	if err := st.UpdateNodeLastConnectionMode(ctx, pending.ID, models.ConnectionModeInbound); err != nil {
		t.Fatalf("set connection mode: %v", err)
	}

	// 同 endpoint 重新纳管：hostname 更新，入站身份/令牌/最近连接方式保留。
	reused, err := nm.PrepareEnrollment(ctx, "inbound-node-renamed", "10.0.0.60", "10.0.0.60:18444", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("re-enroll same endpoint: %v", err)
	}
	if reused.ID != pending.ID || reused.Endpoint != "10.0.0.60:18444" ||
		!reused.InboundProvisioned || reused.InboundCertFingerprint != "fingerprint" ||
		reused.InboundTokenEnc != "token-enc" || reused.LastConnectionMode != models.ConnectionModeInbound {
		t.Fatalf("same-endpoint re-enrollment reset inbound identity: %+v", reused)
	}
	if reused.Hostname != "inbound-node-renamed" {
		t.Fatalf("hostname = %q", reused.Hostname)
	}

	// endpoint 变更：保存新地址并暂停入站身份，旧证书/令牌保留供轮换判定。
	changed, err := nm.PrepareEnrollment(ctx, "inbound-node-renamed", "10.0.0.60", "new.example.com:18444", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("re-enroll new endpoint: %v", err)
	}
	if changed.Endpoint != "new.example.com:18444" || changed.InboundProvisioned || !changed.InboundBundleStale {
		t.Fatalf("endpoint change did not suspend inbound identity: %+v", changed)
	}
	if changed.InboundCertFingerprint != "fingerprint" || changed.InboundTokenEnc != "token-enc" {
		t.Fatalf("endpoint change must keep old identity for rotation: %+v", changed)
	}
}

func TestPrepareEnrollmentRequiresEndpoint(t *testing.T) {
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))
	if _, err := nm.PrepareEnrollment(context.Background(), "no-endpoint", "10.0.0.62", "", models.DeploymentModeNative); err == nil {
		t.Fatal("PrepareEnrollment without endpoint must fail")
	}
}

func TestRecordConnectionModeValidation(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))
	node, _, err := nm.RegisterAgent(ctx, "mode-host", "10.0.0.61", "linux", "amd64", "v1", "native", false, nil)
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	if err := nm.RecordConnectionMode(ctx, node.ID, models.ConnectionModeInbound); err != nil {
		t.Fatalf("record inbound: %v", err)
	}
	if err := nm.RecordConnectionMode(ctx, node.ID, models.ConnectionModeOutbound); err != nil {
		t.Fatalf("record outbound: %v", err)
	}
	if err := nm.RecordConnectionMode(ctx, node.ID, "manual"); err == nil {
		t.Fatal("invalid connection mode must be rejected")
	}
}
