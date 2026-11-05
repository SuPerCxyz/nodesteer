package protocol

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
)

// InboundTokenProofLabel 入站令牌 HMAC 的域分隔标签。
const InboundTokenProofLabel = "nodesteer-inbound-v1"

// InboundTokenProofInput 返回参与 HMAC 的规范字符串：
// label | hub_nonce | agent_nonce | node_id。
func InboundTokenProofInput(hubNonce, agentNonce, nodeID string) string {
	return InboundTokenProofLabel + "|" + hubNonce + "|" + agentNonce + "|" + nodeID
}

// InboundTokenProof 计算 HUB_HELLO_ACK 的令牌证明（HMAC-SHA256，十六进制小写）。
// 令牌本身不在连接上传输。
func InboundTokenProof(token []byte, hubNonce, agentNonce, nodeID string) string {
	mac := hmac.New(sha256.New, token)
	_, _ = mac.Write([]byte(InboundTokenProofInput(hubNonce, agentNonce, nodeID)))
	return hex.EncodeToString(mac.Sum(nil))
}

// VerifyInboundTokenProof 常量时间校验令牌证明。
func VerifyInboundTokenProof(token []byte, hubNonce, agentNonce, nodeID, proof string) bool {
	expected := InboundTokenProof(token, hubNonce, agentNonce, nodeID)
	return hmac.Equal([]byte(expected), []byte(proof))
}
