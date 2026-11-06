package hub

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type busyNodeStore struct {
	store.Store
	mu             sync.Mutex
	heartbeatBusy  int
	statusBusy     int
	heartbeatCalls int
	statusCalls    int
}

func (s *busyNodeStore) UpdateNodeHeartbeat(ctx context.Context, id string, lastSeen time.Time) error {
	s.mu.Lock()
	s.heartbeatCalls++
	if s.heartbeatBusy > 0 {
		s.heartbeatBusy--
		s.mu.Unlock()
		return errors.New("database is locked (5) (SQLITE_BUSY)")
	}
	s.mu.Unlock()
	return s.Store.UpdateNodeHeartbeat(ctx, id, lastSeen)
}

func (s *busyNodeStore) UpdateNodeStatus(ctx context.Context, id, status string) error {
	s.mu.Lock()
	s.statusCalls++
	if s.statusBusy > 0 {
		s.statusBusy--
		s.mu.Unlock()
		return errors.New("database is locked (5) (SQLITE_BUSY)")
	}
	s.mu.Unlock()
	return s.Store.UpdateNodeStatus(ctx, id, status)
}

func (s *busyNodeStore) counters() (int, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.heartbeatCalls, s.statusCalls
}

func newBusyGateway(t *testing.T, st *busyNodeStore) (*Gateway, *NodeManager, *SessionManager) {
	t.Helper()
	revisions := NewRevisionManager(st.Store)
	nm := NewNodeManager(st, revisions)
	sm := NewSessionManager()
	nm.SetSessionManager(sm)
	g := NewGateway(GatewayConfig{RegistrationToken: "test-token"}, nm, sm, NewSyncManager(st.Store, revisions, sm, nm), nil, slog.New(slog.DiscardHandler))
	return g, nm, sm
}

func TestGatewayHeartbeatRetriesBusy(t *testing.T) {
	base, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { base.Close() })
	st := &busyNodeStore{Store: base, heartbeatBusy: 1}
	g, nm, _ := newBusyGateway(t, st)
	ctx := context.Background()
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-busy", "host", "10.0.0.30", "linux", "amd64", "1.0", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	conn := &wsConn{
		meta:    &connMeta{nodeID: node.ID},
		gateway: g,
		sendCh:  make(chan protocol.Envelope, 4),
		done:    make(chan struct{}),
	}
	env := protocol.NewEnvelope(protocol.MsgHeartbeat, "", protocol.HeartbeatPayload{NodeID: node.ID})
	if err := g.handleHeartbeat(ctx, conn, env); err != nil {
		t.Fatalf("heartbeat with transient busy failed: %v", err)
	}
	if calls, _ := st.counters(); calls != 2 {
		t.Fatalf("heartbeat calls = %d, want 2 (busy + retry)", calls)
	}
	select {
	case msg := <-conn.sendCh:
		if msg.Type != protocol.MsgHeartbeatAck {
			t.Fatalf("ack type = %q", msg.Type)
		}
	default:
		t.Fatal("missing heartbeat ack after retry")
	}
	persisted, err := base.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.Status != models.NodeStatusOnline {
		t.Fatalf("status = %q, want online", persisted.Status)
	}
}

func TestGatewayDisconnectRetriesBusyWithoutDroppingSessionSemantics(t *testing.T) {
	base, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { base.Close() })
	st := &busyNodeStore{Store: base, statusBusy: 1}
	g, nm, sm := newBusyGateway(t, st)
	ctx := context.Background()
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-busy", "host", "10.0.0.31", "linux", "amd64", "1.0", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	conn := &fakeAgentConn{nodeID: node.ID}
	sm.Register(conn)

	g.handleDisconnect(node.ID, conn)
	if _, ok := sm.Get(node.ID); ok {
		t.Fatal("session still registered after disconnect")
	}
	if _, calls := st.counters(); calls < 2 {
		t.Fatalf("status calls = %d, want retry", calls)
	}
	persisted, err := base.GetNode(ctx, node.ID)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.Status != models.NodeStatusOffline {
		t.Fatalf("status = %q, want offline after retry", persisted.Status)
	}
}

func TestRetryBusyDoesNotRetryNonBusy(t *testing.T) {
	g := &Gateway{logger: slog.New(slog.DiscardHandler)}
	calls := 0
	err := g.retryBusy(context.Background(), "test", func(context.Context) error {
		calls++
		return errors.New("invalid credential")
	})
	if err == nil || calls != 1 {
		t.Fatalf("calls = %d err = %v, want single non-busy failure", calls, err)
	}
}

func TestBusyRetryBudgetBelowAgentTimeout(t *testing.T) {
	if busyRetryBudget >= 10*time.Second {
		t.Fatalf("busyRetryBudget = %v, want < 10s", busyRetryBudget)
	}
}

func TestRetryBusyStopsAtBudget(t *testing.T) {
	oldBudget := busyRetryBudget
	busyRetryBudget = 300 * time.Millisecond
	defer func() { busyRetryBudget = oldBudget }()
	g := &Gateway{logger: slog.New(slog.DiscardHandler)}
	started := time.Now()
	err := g.retryBusy(context.Background(), "test", func(ctx context.Context) error {
		<-ctx.Done()
		return errors.New("database is locked (5) (SQLITE_BUSY)")
	})
	if err == nil {
		t.Fatal("expected busy error")
	}
	if elapsed := time.Since(started); elapsed > 2*time.Second {
		t.Fatalf("retryBusy took %v, want bounded by budget", elapsed)
	}
}

func TestRetryBusyStopsWhenSessionCloses(t *testing.T) {
	conn := &wsConn{done: make(chan struct{})}
	ctx, cancel := conn.sessionContext(context.Background())
	defer cancel()
	started := time.Now()
	done := make(chan error, 1)
	go func() {
		done <- (&Gateway{logger: slog.New(slog.DiscardHandler)}).retryBusy(ctx, "test", func(ctx context.Context) error {
			<-ctx.Done()
			return errors.New("database is locked (5) (SQLITE_BUSY)")
		})
	}()
	time.Sleep(20 * time.Millisecond)
	close(conn.done)
	select {
	case <-done:
		if elapsed := time.Since(started); elapsed > time.Second {
			t.Fatalf("retryBusy did not stop promptly on conn.done: %v", elapsed)
		}
	case <-time.After(time.Second):
		t.Fatal("retryBusy blocked after conn.done")
	}
}

func TestHelloAckDeclaresMetricsAckCapability(t *testing.T) {
	// 能力声明字段必须可序列化且旧 Agent 解析时不受影响。
	ack := protocol.HelloAckPayload{Accepted: true, Capabilities: map[string]bool{protocol.CapabilityNodeMetricsAck: true}}
	data, err := json.Marshal(ack)
	if err != nil {
		t.Fatal(err)
	}
	var decoded protocol.HelloAckPayload
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatal(err)
	}
	if !decoded.Capabilities[protocol.CapabilityNodeMetricsAck] {
		t.Fatalf("capabilities lost: %s", data)
	}
}

func TestValidateSyncResponseSizeRejectsOverAgentLimit(t *testing.T) {
	small := &protocol.SyncResponsePayload{Snapshot: true, Scripts: []protocol.ObjectEntry{{ID: "s", Data: []byte(`{"content":"ok"}`)}}}
	if err := validateSyncResponseSize(small); err != nil {
		t.Fatalf("small response rejected: %v", err)
	}
	large := &protocol.SyncResponsePayload{Snapshot: true, Scripts: []protocol.ObjectEntry{{ID: "s", Data: make([]byte, maxAgentControlPayloadBytes)}}}
	if err := validateSyncResponseSize(large); err == nil {
		t.Fatal("oversized sync response accepted")
	}
}
