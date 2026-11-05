package hub

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func gatewayMetricValue(value float64) *float64 { return &value }

func gatewayMetricConn(g *Gateway, sm *SessionManager, nodeID string) *wsConn {
	conn := &wsConn{
		meta:    &connMeta{nodeID: nodeID},
		gateway: g,
		sendCh:  make(chan protocol.Envelope, 4),
		done:    make(chan struct{}),
	}
	sm.Register(conn)
	return conn
}

func gatewayMetricBatch(nodeID string, samples []protocol.NodeMetricSample) protocol.Envelope {
	return protocol.NewEnvelope(protocol.MsgNodeMetrics, "metrics", protocol.NodeMetricsPayload{
		NodeID:   nodeID,
		Sequence: 1,
		Samples:  samples,
	})
}

func countGatewayMetricSamples(t *testing.T, g *Gateway) int {
	t.Helper()
	st, ok := g.nodes.store.(*store.SQLiteStore)
	if !ok {
		t.Fatal("gateway store is not SQLite")
	}
	var count int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_samples`).Scan(&count); err != nil {
		t.Fatalf("count samples: %v", err)
	}
	return count
}

func TestGatewayNodeMetricsIngest(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-metrics", "host-m", "10.0.0.20", "linux", "amd64", "1.0", models.DeploymentModeNative, false, map[string]bool{models.CapNodeMetrics: true})
	if err != nil {
		t.Fatal(err)
	}
	conn := gatewayMetricConn(g, sm, node.ID)
	now := time.Now().UTC().Add(-time.Minute)
	batch := gatewayMetricBatch(node.ID, []protocol.NodeMetricSample{
		{Timestamp: now, Metric: "cpu.usage", Dimensions: map[string]string{"cpu": "all"}, Value: gatewayMetricValue(12.5), Quality: protocol.NodeMetricQualityValid},
		{Timestamp: now.Add(30 * time.Second), Metric: "memory.usage", Value: gatewayMetricValue(42), Quality: protocol.NodeMetricQualityValid},
		{Timestamp: now.Add(30 * time.Second), Metric: "disk.usage", Dimensions: map[string]string{"mount": "/"}, Quality: protocol.NodeMetricQualityMissing},
	})
	if err := g.handleNodeMetrics(ctx, conn, batch); err != nil {
		t.Fatalf("valid batch rejected: %v", err)
	}
	if count := countGatewayMetricSamples(t, g); count != 3 {
		t.Fatalf("sample count = %d, want 3", count)
	}
	// 同一批次重放必须幂等（主键 UPSERT），不能产生重复行。
	if err := g.handleNodeMetrics(ctx, conn, batch); err != nil {
		t.Fatalf("duplicate batch rejected: %v", err)
	}
	if count := countGatewayMetricSamples(t, g); count != 3 {
		t.Fatalf("sample count after duplicate = %d, want 3", count)
	}
}

func TestGatewayNodeMetricsRejectsMismatchedNode(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-metrics", "host-m", "10.0.0.21", "linux", "amd64", "1.0", models.DeploymentModeNative, false, map[string]bool{models.CapNodeMetrics: true})
	if err != nil {
		t.Fatal(err)
	}
	conn := gatewayMetricConn(g, sm, node.ID)
	batch := gatewayMetricBatch("another-node", []protocol.NodeMetricSample{
		{Timestamp: time.Now().UTC().Add(-time.Minute), Metric: "cpu.usage", Value: gatewayMetricValue(1), Quality: protocol.NodeMetricQualityValid},
	})
	if err := g.handleNodeMetrics(ctx, conn, batch); err == nil {
		t.Fatal("mismatched node id accepted")
	}
	if count := countGatewayMetricSamples(t, g); count != 0 {
		t.Fatalf("mismatched batch persisted %d samples", count)
	}
}

func TestGatewayNodeMetricsRejectsOversizedBatch(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-metrics", "host-m", "10.0.0.22", "linux", "amd64", "1.0", models.DeploymentModeNative, false, map[string]bool{models.CapNodeMetrics: true})
	if err != nil {
		t.Fatal(err)
	}
	conn := gatewayMetricConn(g, sm, node.ID)
	samples := make([]protocol.NodeMetricSample, store.NativeMaxBatchSamples+1)
	for index := range samples {
		samples[index] = protocol.NodeMetricSample{Timestamp: time.Now().UTC().Add(-time.Minute), Metric: "cpu.usage", Value: gatewayMetricValue(1), Quality: protocol.NodeMetricQualityValid}
	}
	if err := g.handleNodeMetrics(ctx, conn, gatewayMetricBatch(node.ID, samples)); err == nil {
		t.Fatal("oversized batch accepted")
	}
	if count := countGatewayMetricSamples(t, g); count != 0 {
		t.Fatalf("oversized batch persisted %d samples", count)
	}
}

func TestGatewayNodeMetricsRejectsOversizedPayload(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-metrics", "host-m", "10.0.0.23", "linux", "amd64", "1.0", models.DeploymentModeNative, false, map[string]bool{models.CapNodeMetrics: true})
	if err != nil {
		t.Fatal(err)
	}
	conn := gatewayMetricConn(g, sm, node.ID)
	env := protocol.Envelope{Type: protocol.MsgNodeMetrics, ID: "metrics", Payload: json.RawMessage(make([]byte, maxNodeMetricsPayloadBytes+1))}
	if err := g.handleNodeMetrics(ctx, conn, env); err == nil {
		t.Fatal("oversized payload accepted")
	}
}

func TestGatewayNodeMetricsRejectsAgentWithoutCapability(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-old", "host-old", "10.0.0.24", "linux", "amd64", "0.9", models.DeploymentModeNative, false, map[string]bool{models.CapScript: true})
	if err != nil {
		t.Fatal(err)
	}
	conn := gatewayMetricConn(g, sm, node.ID)
	batch := gatewayMetricBatch(node.ID, []protocol.NodeMetricSample{
		{Timestamp: time.Now().UTC().Add(-time.Minute), Metric: "cpu.usage", Value: gatewayMetricValue(1), Quality: protocol.NodeMetricQualityValid},
	})
	if err := g.handleNodeMetrics(ctx, conn, batch); err == nil {
		t.Fatal("old agent without node_metrics capability accepted")
	}
	if count := countGatewayMetricSamples(t, g); count != 0 {
		t.Fatalf("unsupported agent persisted %d samples", count)
	}
}

func TestGatewayNodeMetricsRejectsExpiredSession(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-metrics", "host-m", "10.0.0.25", "linux", "amd64", "1.0", models.DeploymentModeNative, false, map[string]bool{models.CapNodeMetrics: true})
	if err != nil {
		t.Fatal(err)
	}
	conn := gatewayMetricConn(g, sm, node.ID)
	sm.Unregister(node.ID)
	batch := gatewayMetricBatch(node.ID, []protocol.NodeMetricSample{
		{Timestamp: time.Now().UTC().Add(-time.Minute), Metric: "cpu.usage", Value: gatewayMetricValue(1), Quality: protocol.NodeMetricQualityValid},
	})
	err = g.handleNodeMetrics(ctx, conn, batch)
	if err == nil || err.Error() != "node metrics session expired" {
		t.Fatalf("expired session error = %v", err)
	}
}

func TestGatewayNodeMetricsRejectsInvalidQuality(t *testing.T) {
	ctx := context.Background()
	g, nm, sm := newTestGateway(t)
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-metrics", "host-m", "10.0.0.26", "linux", "amd64", "1.0", models.DeploymentModeNative, false, map[string]bool{models.CapNodeMetrics: true})
	if err != nil {
		t.Fatal(err)
	}
	conn := gatewayMetricConn(g, sm, node.ID)
	batch := gatewayMetricBatch(node.ID, []protocol.NodeMetricSample{
		{Timestamp: time.Now().UTC().Add(-time.Minute), Metric: "cpu.usage", Value: gatewayMetricValue(1), Quality: 9},
	})
	err = g.handleNodeMetrics(ctx, conn, batch)
	if err == nil || err.Error() != fmt.Sprintf("invalid node metric quality %d", 9) {
		t.Fatalf("invalid quality error = %v", err)
	}
}

func TestConvertInventoryHostIdentityAndBlockDevices(t *testing.T) {
	inv := convertInventory(&protocol.Inventory{Hostname: "real-host", BlockDevices: []protocol.InventoryBlockDevice{{Name: "vda", Kind: "disk"}, {Name: "vda2", Kind: "partition"}}})
	if inv.Hostname != "real-host" || len(inv.BlockDevices) != 2 || inv.BlockDevices[1].Kind != "partition" {
		t.Fatalf("inventory conversion: %+v", inv)
	}
}
