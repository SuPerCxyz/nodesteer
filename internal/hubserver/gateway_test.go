package hubserver

import (
	"context"
	"encoding/json"
	"log/slog"
	"net"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

// TestGatewayHelloRegister 通过真实 WebSocket 测试 Agent 注册与心跳流程
func TestGatewayHelloRegister(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(Config{
		WebAddr:           "127.0.0.1:0",
		GatewayAddr:       "127.0.0.1:0",
		RegistrationToken: "test-token",
		DataDir:           dir,
		ArtifactDir:       dir + "/artifacts",
		BaseURL:           "http://127.0.0.1:18080",
		HeartbeatTimeout:  30 * time.Second,
		AdminUsername:     "admin",
		AdminPassword:     "admin123",
		SessionTTL:        time.Hour,
		RevisionCheckSec:  5,
		ChangelogWindow:   5000,
	}, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })

	// 真实监听 Gateway
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	go http.Serve(ln, h.GatewayHandler())
	url := "ws://" + ln.Addr().String()

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, url, nil)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")

	// HELLO（首次注册）
	hello := protocol.NewEnvelope(protocol.MsgHello, "req1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		AgentVersion:    "test-1.0",
		DeploymentMode:  models.DeploymentModeNative,
		Capabilities:    map[string]bool{"script": true},
		Hostname:        "test-node",
		RegistrationKey: "test-token",
		LocalGlobalRev:  0,
	})
	if err := wsjson.Write(ctx, conn, hello); err != nil {
		t.Fatalf("write hello: %v", err)
	}

	var ack protocol.Envelope
	if err := wsjson.Read(ctx, conn, &ack); err != nil {
		t.Fatalf("read ack: %v", err)
	}
	if ack.Type != protocol.MsgHelloAck {
		t.Fatalf("expected HELLO_ACK, got %s", ack.Type)
	}
	var helloAck protocol.HelloAckPayload
	json.Unmarshal(ack.Payload, &helloAck)
	if !helloAck.Accepted {
		t.Fatalf("hello rejected: %s", helloAck.Message)
	}
	if helloAck.NodeID == "" {
		t.Fatalf("expected node id in ack")
	}

	node, err := h.Nodes().GetNode(context.Background(), helloAck.NodeID)
	if err != nil {
		t.Fatalf("node not registered: %v", err)
	}
	if node.Hostname != "test-node" {
		t.Fatalf("hostname mismatch: %s", node.Hostname)
	}

	// Heartbeat
	hb := protocol.NewEnvelope(protocol.MsgHeartbeat, "req2", protocol.HeartbeatPayload{
		NodeID: helloAck.NodeID, GlobalRev: 0,
	})
	if err := wsjson.Write(ctx, conn, hb); err != nil {
		t.Fatalf("write heartbeat: %v", err)
	}
	var hbAck protocol.Envelope
	if err := wsjson.Read(ctx, conn, &hbAck); err != nil {
		t.Fatalf("read hb ack: %v", err)
	}
	if hbAck.Type != protocol.MsgHeartbeatAck {
		t.Fatalf("expected HEARTBEAT_ACK, got %s", hbAck.Type)
	}

	// 无效 Token 注册应被拒绝
	conn2, _, err := websocket.Dial(ctx, url, nil)
	if err != nil {
		t.Fatalf("dial2: %v", err)
	}
	defer conn2.Close(websocket.StatusNormalClosure, "done")
	bad := protocol.NewEnvelope(protocol.MsgHello, "req3", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		RegistrationKey: "wrong-token",
		Hostname:        "bad-node",
	})
	wsjson.Write(ctx, conn2, bad)
	var badAck protocol.Envelope
	if err := wsjson.Read(ctx, conn2, &badAck); err != nil {
		t.Fatalf("read bad ack: %v", err)
	}
	var badPayload protocol.HelloAckPayload
	json.Unmarshal(badAck.Payload, &badPayload)
	if badPayload.Accepted {
		t.Fatalf("expected rejection for wrong token")
	}
}

func TestGatewayCredentialReconnectAndRevoke(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(Config{RegistrationToken: "token", DataDir: dir, ArtifactDir: dir + "/artifacts", BaseURL: "http://127.0.0.1:18080", HeartbeatTimeout: time.Minute}, logger)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { h.Close() })
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go http.Serve(ln, h.GatewayHandler())
	url := "ws://" + ln.Addr().String()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	connect := func(hello protocol.HelloPayload) (protocol.HelloAckPayload, *websocket.Conn) {
		conn, _, err := websocket.Dial(ctx, url, nil)
		if err != nil {
			t.Fatal(err)
		}
		if err := wsjson.Write(ctx, conn, protocol.NewEnvelope(protocol.MsgHello, "hello", hello)); err != nil {
			t.Fatal(err)
		}
		var env protocol.Envelope
		if err := wsjson.Read(ctx, conn, &env); err != nil {
			t.Fatal(err)
		}
		var ack protocol.HelloAckPayload
		if err := json.Unmarshal(env.Payload, &ack); err != nil {
			t.Fatal(err)
		}
		return ack, conn
	}
	first, conn := connect(protocol.HelloPayload{ProtocolVersion: protocol.ProtocolVersion, RegistrationKey: "token", Hostname: "credential-node"})
	if !first.Accepted || first.AgentCredential == "" {
		t.Fatalf("expected issued credential: %+v", first)
	}
	conn.Close(websocket.StatusNormalClosure, "done")
	second, conn := connect(protocol.HelloPayload{ProtocolVersion: protocol.ProtocolVersion, AgentID: first.AgentID, AgentCredential: first.AgentCredential, Hostname: "credential-node"})
	if !second.Accepted {
		t.Fatalf("credential reconnect rejected: %+v", second)
	}
	conn.Close(websocket.StatusNormalClosure, "done")
	if err := h.Nodes().RevokeCredential(context.Background(), first.NodeID); err != nil {
		t.Fatal(err)
	}
	third, conn := connect(protocol.HelloPayload{ProtocolVersion: protocol.ProtocolVersion, AgentID: first.AgentID, AgentCredential: first.AgentCredential, Hostname: "credential-node"})
	conn.Close(websocket.StatusNormalClosure, "done")
	if third.Accepted {
		t.Fatal("revoked credential was accepted")
	}
}

// TestGatewaySync 验证同步协议
func TestGatewaySync(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(Config{
		WebAddr:           "127.0.0.1:0",
		GatewayAddr:       "127.0.0.1:0",
		RegistrationToken: "test-token",
		DataDir:           dir,
		ArtifactDir:       dir + "/artifacts",
		BaseURL:           "http://127.0.0.1:18080",
		HeartbeatTimeout:  30 * time.Second,
		AdminUsername:     "admin",
		AdminPassword:     "admin123",
		SessionTTL:        time.Hour,
		RevisionCheckSec:  5,
		ChangelogWindow:   5000,
	}, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	go http.Serve(ln, h.GatewayHandler())
	url := "ws://" + ln.Addr().String()

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, url, nil)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")

	hello := protocol.NewEnvelope(protocol.MsgHello, "req1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		RegistrationKey: "test-token",
		Hostname:        "sync-node",
	})
	wsjson.Write(ctx, conn, hello)
	var ack protocol.Envelope
	wsjson.Read(ctx, conn, &ack)
	var helloAck protocol.HelloAckPayload
	json.Unmarshal(ack.Payload, &helloAck)
	if !helloAck.Accepted {
		t.Fatalf("hello rejected")
	}

	// 创建 Script + Task，然后 Sync
	st := h.Store()
	sc := &models.Script{Name: "s1", Interpreter: "shell", Content: "echo hi", Enabled: true}
	if err := st.CreateScript(ctx, sc); err != nil {
		t.Fatalf("create script: %v", err)
	}
	task := &models.Task{
		Name: "t1", Type: "script", ScriptID: sc.ID, Enabled: true,
		Target: models.Target{Type: "node", NodeIDs: []string{helloAck.NodeID}},
	}
	if err := st.CreateTask(ctx, task); err != nil {
		t.Fatalf("create task: %v", err)
	}

	sync := protocol.NewEnvelope(protocol.MsgSyncRequest, "req2", protocol.SyncRequestPayload{Since: 0})
	wsjson.Write(ctx, conn, sync)
	var syncResp protocol.Envelope
	wsjson.Read(ctx, conn, &syncResp)
	if syncResp.Type != protocol.MsgSyncResponse {
		t.Fatalf("expected SYNC_RESPONSE, got %s", syncResp.Type)
	}
	var sr protocol.SyncResponsePayload
	json.Unmarshal(syncResp.Payload, &sr)
	if len(sr.Scripts) != 1 {
		t.Fatalf("expected 1 script in sync, got %d", len(sr.Scripts))
	}
	if len(sr.Tasks) != 1 {
		t.Fatalf("expected 1 task in sync, got %d", len(sr.Tasks))
	}
}

// TestGatewayAcceptsNodesteerSubprotocol 实测 Agent 侧 `Subprotocols: []string{"nodesteer"}`
// 对当前 Hub 的握手结果：Hub 未声明子协议协商时不得拒绝连接，且 HELLO 注册链路可用。
func TestGatewayAcceptsNodesteerSubprotocol(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(Config{
		RegistrationToken: "test-token",
		DataDir:           dir,
		ArtifactDir:       dir + "/artifacts",
		BaseURL:           "http://127.0.0.1:18080",
		HeartbeatTimeout:  30 * time.Second,
	}, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	go http.Serve(ln, h.GatewayHandler())
	url := "ws://" + ln.Addr().String()

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	conn, resp, err := websocket.Dial(ctx, url, &websocket.DialOptions{
		Subprotocols: []string{"nodesteer"},
	})
	if err != nil {
		t.Fatalf("dial with nodesteer subprotocol: %v", err)
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")
	if resp == nil {
		t.Fatal("expected handshake response")
	}
	if got := resp.Header.Get("Sec-WebSocket-Protocol"); got != "" && !strings.EqualFold(got, "nodesteer") {
		t.Fatalf("server echoed unexpected subprotocol %q", got)
	}

	// 握手后的业务链路必须真实可用：HELLO 注册被接受。
	hello := protocol.NewEnvelope(protocol.MsgHello, "subproto-1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		RegistrationKey: "test-token",
		Hostname:        "subproto-node",
	})
	if err := wsjson.Write(ctx, conn, hello); err != nil {
		t.Fatalf("write hello: %v", err)
	}
	var ack protocol.Envelope
	if err := wsjson.Read(ctx, conn, &ack); err != nil {
		t.Fatalf("read ack: %v", err)
	}
	if ack.Type != protocol.MsgHelloAck {
		t.Fatalf("expected HELLO_ACK, got %s", ack.Type)
	}
	var helloAck protocol.HelloAckPayload
	if err := json.Unmarshal(ack.Payload, &helloAck); err != nil {
		t.Fatalf("decode ack: %v", err)
	}
	if !helloAck.Accepted {
		t.Fatalf("hello rejected: %s", helloAck.Message)
	}
	if helloAck.NodeID == "" {
		t.Fatal("expected node id in ack")
	}
}
