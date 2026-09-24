package hub

import (
	"context"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// helloWithMode 以注册路径发送携带 deployment_mode 的 HELLO 并返回入库后的节点。
func helloWithMode(t *testing.T, hostname, mode string) *models.Node {
	t.Helper()
	ctx := context.Background()
	g, nm, _ := newTestGateway(t)
	conn := newHelloTestConn(t, g)
	env := protocol.NewEnvelope(protocol.MsgHello, "r1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		RegistrationKey: "test-token",
		Hostname:        hostname,
		IP:              "192.0.2.50",
		DeploymentMode:  mode,
	})
	if err := g.handleHello(ctx, conn, env); err != nil {
		t.Fatalf("expected accepted HELLO, got %v", err)
	}
	ack := drainHelloAck(t, conn)
	got, err := nm.GetNode(ctx, ack.NodeID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	return got
}

// TestHelloNormalizesInvalidDeploymentMode 非法/空 deployment_mode 归一化为 native，
// HELLO 不被拒绝（升级窗口不踢存量节点）；合法值原样写入。
func TestHelloNormalizesInvalidDeploymentMode(t *testing.T) {
	if got := helloWithMode(t, "mode-invalid", "vmware"); got.DeploymentMode != models.DeploymentModeNative {
		t.Fatalf("invalid mode must normalize to native, got %q", got.DeploymentMode)
	}
	if got := helloWithMode(t, "mode-empty", ""); got.DeploymentMode != models.DeploymentModeNative {
		t.Fatalf("empty mode must normalize to native, got %q", got.DeploymentMode)
	}
	if got := helloWithMode(t, "mode-docker", models.DeploymentModeDocker); got.DeploymentMode != models.DeploymentModeDocker {
		t.Fatalf("valid docker must be kept, got %q", got.DeploymentMode)
	}
	if got := helloWithMode(t, "mode-hostint", models.DeploymentModeDockerHostInt); got.DeploymentMode != models.DeploymentModeDockerHostInt {
		t.Fatalf("valid docker_host_integration must be kept, got %q", got.DeploymentMode)
	}
}
