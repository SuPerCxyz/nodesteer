package hub

import (
	"context"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// TestNodeDisplayNameSurvivesEnrollmentAndRegistration 覆盖纳管/重注册路径：
// 重新纳管复用节点、Agent 重新注册上报新 hostname 都不得清空或覆盖 display_name。
func TestNodeDisplayNameSurvivesEnrollmentAndRegistration(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))

	pending, err := nm.PrepareEnrollment(ctx, "display-node", "10.0.0.55", "10.0.0.55:18444", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("prepare enrollment: %v", err)
	}
	if pending.DisplayName != "" {
		t.Fatalf("new node display_name = %q, want empty", pending.DisplayName)
	}
	if err := st.UpdateNodeDisplayName(ctx, pending.ID, "生产数据库 01"); err != nil {
		t.Fatalf("set display name: %v", err)
	}

	// 重新纳管：复用既有节点（同 hostname/IP），显示名保留。
	reused, err := nm.PrepareEnrollment(ctx, "display-node", "10.0.0.55", "10.0.0.55:18444", models.DeploymentModeNative)
	if err != nil {
		t.Fatalf("re-enroll: %v", err)
	}
	if reused.ID != pending.ID {
		t.Fatalf("re-enrollment node id = %q, want %q", reused.ID, pending.ID)
	}
	if reused.DisplayName != "生产数据库 01" {
		t.Fatalf("display_name after re-enroll = %q, want preserved", reused.DisplayName)
	}

	// Agent HELLO/重新注册：hostname 更新，显示名保持不变。
	registered, _, _, err := nm.RegisterOrUpdate(ctx, pending.AgentID, "display-node-new-host", "10.0.0.56",
		"linux", "amd64", "1.0.0", models.DeploymentModeNative, false, map[string]bool{})
	if err != nil {
		t.Fatalf("register or update: %v", err)
	}
	if registered.Hostname != "display-node-new-host" {
		t.Fatalf("hostname = %q, want agent-reported value", registered.Hostname)
	}
	if registered.DisplayName != "生产数据库 01" {
		t.Fatalf("display_name after re-register = %q, want preserved", registered.DisplayName)
	}
	persisted, err := st.GetNode(ctx, pending.ID)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	if persisted.DisplayName != "生产数据库 01" {
		t.Fatalf("persisted display_name = %q, want preserved", persisted.DisplayName)
	}
}
