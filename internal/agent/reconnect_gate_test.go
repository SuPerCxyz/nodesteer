package agent

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
)

// waitAgentCond 轮询等待条件成立（3s 上限）。
func waitAgentCond(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

// TestSchedulerGateUsesHandshakeNotTransport 重连窗口回归
// （缺陷 OBS-20261016-G23-RECONNECT-WINDOW）：Dial 成功但 HELLO_ACK accepted
// 尚未到达时，hub_online_required 门禁必须保持关闭（此前用 IsConnected，
// 窗口内会被误放行）；accepted 后才放行。
func TestSchedulerGateUsesHandshakeNotTransport(t *testing.T) {
	releaseAck := make(chan struct{})
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.CloseNow()
		// Accept hijack 后请求上下文取消，读写用独立上下文。
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		if _, _, err := c.Read(ctx); err != nil { // HELLO
			return
		}
		<-releaseAck
		env, _ := json.Marshal(protocol.NewEnvelope(protocol.MsgHelloAck, "", protocol.HelloAckPayload{Accepted: true}))
		if err := c.Write(ctx, websocket.MessageText, env); err != nil {
			return
		}
		for {
			if _, _, err := c.Read(ctx); err != nil {
				return
			}
		}
	}))
	defer ts.Close()

	a := newTestAgent(t, Config{HubURL: "ws://" + ts.Listener.Addr().String()})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go a.conn.Run(ctx, a.helloFn)

	// 重连窗口：传输已连、HELLO_ACK 未回
	waitAgentCond(t, "transport connected", a.conn.IsConnected)
	if a.sch.Online() {
		t.Fatal("hub_online_required gate must stay closed in the reconnect window")
	}

	// accepted 后放行
	close(releaseAck)
	waitAgentCond(t, "handshake complete", a.conn.HandshakeComplete)
	if !a.sch.Online() {
		t.Fatal("gate must open after accepted HELLO_ACK")
	}
}
