package connection

import (
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

type probeTestHandler struct {
	Handler
	calls int
}

func (h *probeTestHandler) OnMonitorProbe(protocol.Envelope) { h.calls++ }

func TestMonitorProbeRequiresAcceptedHandshake(t *testing.T) {
	handler := &probeTestHandler{}
	manager := New("ws://example.test", "", "", handler, nil, nil)
	session := newHubSession(nil, models.ConnectionModeOutbound)
	message := protocol.NewEnvelope(protocol.MsgMonitorProbe, "request", nil)
	manager.dispatch(session, message)
	if handler.calls != 0 {
		t.Fatal("probe before handshake was delivered")
	}
	manager.outbound = session
	manager.acceptSession(session)
	manager.dispatch(session, message)
	if handler.calls != 1 {
		t.Fatal("probe after accepted handshake was dropped")
	}
	// A new transport resets the handshake before a rejected HELLO_ACK.
	manager.mu.Lock()
	session.accepted = false
	manager.mu.Unlock()
	manager.dispatch(session, message)
	if handler.calls != 1 {
		t.Fatal("probe after rejected handshake was delivered")
	}
}
