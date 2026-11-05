package hub

import (
	"context"
	"strings"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

type probeConn struct{ id string }

func (c *probeConn) NodeID() string             { return c.id }
func (*probeConn) Send(protocol.Envelope) error { return nil }
func (*probeConn) Close() error                 { return nil }

func TestProbeResponseRequiresOriginalSession(t *testing.T) {
	m := NewMonitorProbes(NewSessionManager(), nil, nil)
	original, replacement := &probeConn{id: "node"}, &probeConn{id: "node"}
	wait := probeWait{nodeID: "node", conn: original, result: make(chan protocol.MonitorProbeResultPayload, 1)}
	m.waiting["id"] = wait
	m.Complete("node", "id", replacement, protocol.MonitorProbeResultPayload{StatusCode: 200})
	select {
	case <-wait.result:
		t.Fatal("replacement session response accepted")
	default:
	}
	m.Complete("other", "id", original, protocol.MonitorProbeResultPayload{StatusCode: 200})
	select {
	case <-wait.result:
		t.Fatal("different node response accepted")
	default:
	}
	m.Complete("node", "id", original, protocol.MonitorProbeResultPayload{StatusCode: 200})
	select {
	case <-wait.result:
	default:
		t.Fatal("original session response rejected")
	}
}

// An old Agent has no monitoring capability; ordinary sessions remain usable.
func TestLegacyAgentProbeReportsUnavailable(t *testing.T) {
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	if err := st.UpsertNode(ctx, &models.Node{ID: "old-node", AgentID: "old-agent", Status: models.NodeStatusOnline}); err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionManager()
	conn := &probeConn{id: "old-node"}
	sessions.Register(conn)
	probes := NewMonitorProbes(sessions, NewNodeManager(st, NewRevisionManager(st)), nil)
	_, err = probes.Probe(ctx, monitoring.Target{SourceNodeID: "old-node", TimeoutSec: 1})
	if err == nil || !strings.Contains(err.Error(), "capability unavailable") {
		t.Fatalf("error=%v", err)
	}
	if got, ok := sessions.Get("old-node"); !ok || got != conn {
		t.Fatal("legacy session changed")
	}
}
