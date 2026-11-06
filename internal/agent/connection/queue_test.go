package connection

import (
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func TestAgentControlReadLimitIs2MiB(t *testing.T) {
	if agentControlReadLimit != 2<<20 {
		t.Fatalf("agentControlReadLimit = %d, want 2MiB", agentControlReadLimit)
	}
}

func TestSessionSendByteBudgetRejectsMetrics(t *testing.T) {
	session := newHubSession(nil, "outbound")
	payload := make([]byte, 100<<10)
	sent := 0
	for i := 0; i < 11; i++ {
		env := protocol.Envelope{Type: protocol.MsgNodeMetrics, Payload: payload}
		if session.send(env) {
			sent++
		}
	}
	if sent >= 11 {
		t.Fatalf("sent %d metrics frames, byte budget not enforced", sent)
	}
	depth, bytes := session.queueStats()
	if bytes > agentSendQueueBytes {
		t.Fatalf("queued bytes = %d, want <= %d", bytes, agentSendQueueBytes)
	}
	if depth > agentSendQueueFrames {
		t.Fatalf("queue depth = %d, want <= %d", depth, agentSendQueueFrames)
	}
}
