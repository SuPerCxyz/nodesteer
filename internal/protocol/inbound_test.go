package protocol

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestInboundHandshakePayloadsEmbedHelloAck(t *testing.T) {
	if ProtocolVersion != 3 || InboundProtocolVersion != 1 {
		t.Fatalf("protocol versions = %d/%d", ProtocolVersion, InboundProtocolVersion)
	}
	issuedAt := time.Date(2026, 11, 4, 10, 0, 0, 0, time.UTC)
	payload := HubHelloPayload{
		HelloAckPayload: HelloAckPayload{
			Accepted:         true,
			NodeID:           "node-1",
			AgentID:          "agent-1",
			DesiredGlobalRev: 7,
			Settings:         map[string]string{"heartbeat_sec": "30"},
			Message:          "hello",
			NodeStatus:       "pending",
		},
		HubID:      "hub-1",
		HubVersion: "v1",
		Nonce:      "hub-nonce",
		IssuedAt:   issuedAt,
		DataPlane:  true,
		Test:       true,
	}
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal hub hello: %v", err)
	}
	for _, field := range []string{
		`"accepted":true`, `"node_id":"node-1"`, `"agent_id":"agent-1"`,
		`"desired_global_rev":7`, `"hub_id":"hub-1"`, `"nonce":"hub-nonce"`, `"test":true`,
	} {
		if !bytes.Contains(data, []byte(field)) {
			t.Fatalf("hub hello missing %s: %s", field, data)
		}
	}
	// 嵌入字段必须能被既有 HelloAckPayload 直接解析（JSON 平铺）。
	var ack HelloAckPayload
	if err := json.Unmarshal(data, &ack); err != nil {
		t.Fatalf("unmarshal embedded hello ack: %v", err)
	}
	if !ack.Accepted || ack.NodeID != "node-1" || ack.DesiredGlobalRev != 7 || ack.NodeStatus != "pending" {
		t.Fatalf("embedded hello ack = %+v", ack)
	}

	ackPayload := HubHelloAckPayload{
		HelloAckPayload: HelloAckPayload{Accepted: true, NodeID: "node-1"},
		Nonce:           "hub-nonce",
		AgentNonce:      "agent-nonce",
		TokenProof:      "proof",
		DataPlane:       true,
		SessionID:       "session-1",
	}
	ackData, err := json.Marshal(ackPayload)
	if err != nil {
		t.Fatalf("marshal hub hello ack: %v", err)
	}
	for _, field := range []string{`"accepted":true`, `"nonce":"hub-nonce"`, `"token_proof":"proof"`, `"session_id":"session-1"`} {
		if !bytes.Contains(ackData, []byte(field)) {
			t.Fatalf("hub hello ack missing %s: %s", field, ackData)
		}
	}
	if MsgHubHello != "HUB_HELLO" || MsgHubHelloAck != "HUB_HELLO_ACK" {
		t.Fatalf("message constants = %q/%q", MsgHubHello, MsgHubHelloAck)
	}
}

func TestInboundTokenProof(t *testing.T) {
	token := []byte("0123456789abcdef0123456789abcdef")
	proof := InboundTokenProof(token, "hub", "agent", "node-1")
	if len(proof) != 64 {
		t.Fatalf("proof = %q", proof)
	}
	if !VerifyInboundTokenProof(token, "hub", "agent", "node-1", proof) {
		t.Fatal("valid proof rejected")
	}
	for _, mutated := range []struct {
		token, hub, agent, node, proof string
	}{
		{"", "hub", "agent", "node-1", proof},
		{string(token), "other", "agent", "node-1", proof},
		{string(token), "hub", "other", "node-1", proof},
		{string(token), "hub", "agent", "node-2", proof},
		{string(token), "hub", "agent", "node-1", ""},
	} {
		if VerifyInboundTokenProof([]byte(mutated.token), mutated.hub, mutated.agent, mutated.node, mutated.proof) {
			t.Fatalf("mutated proof accepted: %+v", mutated)
		}
	}
}

func TestInboundCompatibilityFieldsAreOptional(t *testing.T) {
	// 旧 Agent/Hub 的既有载荷不含新字段：反序列化必须保持零值语义。
	var upload FileUploadRequestPayload
	if err := json.Unmarshal([]byte(`{"transfer_id":"t1","source_path":"/tmp/a","upload_url":"https://hub/x","offset":0,"max_bytes":10,"attempt":2}`), &upload); err != nil {
		t.Fatalf("unmarshal old upload request: %v", err)
	}
	if upload.Transport != "" || upload.TransferID != "t1" {
		t.Fatalf("old upload request = %+v", upload)
	}
	var delivery FileDeliveryRequestPayload
	if err := json.Unmarshal([]byte(`{"transfer_id":"t1","download_url":"https://hub/x","sha256":"abc","size":10,"destination_path":"/tmp/b","attempt":1}`), &delivery); err != nil {
		t.Fatalf("unmarshal old delivery request: %v", err)
	}
	if delivery.Transport != "" {
		t.Fatalf("old delivery transport = %q", delivery.Transport)
	}
	var exec RunExecutionPayload
	if err := json.Unmarshal([]byte(`{"execution_id":"e1","task_id":"t1","type":"command","command":"true","trigger_type":"manual"}`), &exec); err != nil {
		t.Fatalf("unmarshal old execution: %v", err)
	}
	if exec.ArtifactTransport != "" || exec.AgentBinaryStaged || exec.AgentBinarySHA256 != "" {
		t.Fatalf("old execution new fields = %+v", exec)
	}
}

func TestInboundCompatibilityFieldsRoundTrip(t *testing.T) {
	payload := RunExecutionPayload{
		ExecutionID:       "e1",
		ArtifactTransport: TransportInboundPush,
		AgentBinaryStaged: true,
		AgentBinarySHA256: strings.Repeat("a", 64),
	}
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal execution: %v", err)
	}
	var got RunExecutionPayload
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal execution: %v", err)
	}
	if got.ArtifactTransport != TransportInboundPush || !got.AgentBinaryStaged || got.AgentBinarySHA256 == "" {
		t.Fatalf("round trip execution = %+v", got)
	}
	if TransportOutbound != "outbound" || TransportInboundPull != "inbound_pull" || TransportInboundPush != "inbound_push" {
		t.Fatal("transport constants changed")
	}
	for _, code := range []string{
		ErrorCodeInboundDataPlaneUnavailable,
		ErrorCodeInboundStageUnavailable,
		ErrorCodeInboundAgentUnsupported,
		ErrorCodeInboundEndpointInvalid,
		ErrorCodeInboundPinMismatch,
		ErrorCodeInboundCertificateExpired,
		ErrorCodeInboundAuthFailed,
	} {
		if code == "" {
			t.Fatal("stable inbound error code must not be empty")
		}
	}
}
