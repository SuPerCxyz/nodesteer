package hub

import (
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// TestResolveRemoteStateRemainingTTL 锁定 Hub 侧剩余 TTL 语义：
// 实时分支窗口与剩余均为 60s；缓存命中的剩余量自判定时刻起向上取整
// （0 < 剩余 < 1s 视为 1s，避免截断为 0 被误判过期），窗口仍为观测窗口长度；
// 缓存过期回落实时分支。
func TestResolveRemoteStateRemainingTTL(t *testing.T) {
	ctx := t.Context()
	g, nm, _ := newTestGateway(t)
	node, err := nm.PrepareEnrollment(ctx, "remote-ttl", "10.0.0.87", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("prepare enrollment: %v", err)
	}
	st := g.nodes.store

	// 实时分支：无缓存，窗口与剩余一致（60s），observed_at 为本次 Hub now。
	_, observedAt, ttl, remaining := g.resolveRemoteState(ctx, protocol.RemoteStateReqPayload{
		TargetNodeID: node.ID, Property: "online",
	})
	if ttl != 60 || remaining != 60 {
		t.Fatalf("live branch ttl/remaining = %d/%d, want 60/60", ttl, remaining)
	}
	if observedAt.IsZero() {
		t.Fatal("live branch must report non-zero observed_at")
	}

	// 缓存分支：观测于 1.6s 前、TTL 2s → 仅剩约 0.4s，向上取整为 1s；窗口保持 2s。
	if err := st.SetRemoteState(ctx, node.ID, "online", "online", time.Now().Add(-1600*time.Millisecond), 2); err != nil {
		t.Fatalf("set remote state: %v", err)
	}
	value, _, ttl, remaining := g.resolveRemoteState(ctx, protocol.RemoteStateReqPayload{
		TargetNodeID: node.ID, Property: "online",
	})
	if value != "online" {
		t.Fatalf("cache value = %q, want online", value)
	}
	if ttl != 2 {
		t.Fatalf("cache window ttl = %d, want 2", ttl)
	}
	if remaining != 1 {
		t.Fatalf("cache remaining = %d, want ceil to 1", remaining)
	}

	// 缓存过期（观测于 3s 前、TTL 2s）→ 回落实时分支。
	if err := st.SetRemoteState(ctx, node.ID, "online", "stale-online", time.Now().Add(-3*time.Second), 2); err != nil {
		t.Fatalf("set stale remote state: %v", err)
	}
	value, _, ttl, remaining = g.resolveRemoteState(ctx, protocol.RemoteStateReqPayload{
		TargetNodeID: node.ID, Property: "online",
	})
	if value == "stale-online" {
		t.Fatal("expired cache must not be served")
	}
	if value != models.NodeStatusPending || ttl != 60 || remaining != 60 {
		t.Fatalf("expired cache fallback = %q ttl=%d remaining=%d", value, ttl, remaining)
	}
}
