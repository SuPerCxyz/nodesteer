package hub

import (
	"context"
	"strings"
	"testing"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/store"
)

// TestTaskTimeoutValidation 验证任务超时不允许负数（缺陷 FAIL-A-008），
// 0 仍按默认 300s 处理。
func TestTaskTimeoutValidation(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-t", "host-t", "10.0.0.30", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatal(err)
	}
	tm := NewTaskManager(st, NewRevisionManager(st), nil)

	negative := &models.Task{
		Name:    "negative-timeout",
		Type:    models.TaskTypeCommand,
		Command: "true",
		Timeout: -5,
		Target:  models.Target{Type: "node", NodeIDs: []string{node.ID}},
	}
	if err := tm.Validate(ctx, negative); err == nil || !strings.Contains(err.Error(), "timeout cannot be negative") {
		t.Fatalf("expected negative timeout rejection, got %v", err)
	}

	zero := &models.Task{
		Name:    "default-timeout",
		Type:    models.TaskTypeCommand,
		Command: "true",
		Target:  models.Target{Type: "node", NodeIDs: []string{node.ID}},
	}
	if err := tm.Validate(ctx, zero); err != nil {
		t.Fatalf("zero timeout should be accepted and defaulted: %v", err)
	}
	if zero.Timeout != 300 {
		t.Fatalf("expected default timeout 300, got %d", zero.Timeout)
	}
}

// TestTaskUpdateHealsDeletedTargetNode 验证目标节点被删除后任务仍可编辑/启停，
// 失效节点在更新时被自动剔除（缺陷 FAIL-A-009）。
func TestTaskUpdateHealsDeletedTargetNode(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))
	rm := NewRevisionManager(st)
	syncMgr := NewSyncManager(st, rm, NewSessionManager(), nm)
	tm := NewTaskManager(st, rm, syncMgr)

	nodeA, _, _, err := nm.RegisterOrUpdate(ctx, "agent-h1", "host-h1", "10.0.0.51", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatal(err)
	}
	nodeB, _, _, err := nm.RegisterOrUpdate(ctx, "agent-h2", "host-h2", "10.0.0.52", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatal(err)
	}
	task := &models.Task{
		Name:    "heal-target",
		Type:    models.TaskTypeCommand,
		Command: "true",
		Enabled: true,
		Target:  models.Target{Type: "node", NodeIDs: []string{nodeA.ID, nodeB.ID}},
	}
	if err := tm.Create(ctx, task); err != nil {
		t.Fatalf("create task: %v", err)
	}
	if err := nm.Delete(ctx, nodeB.ID); err != nil {
		t.Fatalf("delete node: %v", err)
	}
	// 目标里仍带着已删除节点，更新（如启停）应成功并自动剔除失效节点
	task.Enabled = false
	if err := tm.Update(ctx, task); err != nil {
		t.Fatalf("update with deleted target node should self-heal, got %v", err)
	}
	got, err := tm.Get(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(got.Target.NodeIDs) != 1 || got.Target.NodeIDs[0] != nodeA.ID {
		t.Fatalf("expected stale node removed, got %v", got.Target.NodeIDs)
	}

	// 运行目标全部失效的任务时应给出可读错误
	task.Target = models.Target{Type: "node", NodeIDs: []string{nodeB.ID}}
	if _, err := nm.ResolveTarget(ctx, task.Target); err == nil {
		t.Fatalf("expected friendly error for deleted target node")
	}
}

// TestDefinitionNameAndParameterUniqueness 验证重名脚本/任务与重复参数被拒绝（缺陷 FAIL-A-014）。
func TestDefinitionNameAndParameterUniqueness(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	nm := NewNodeManager(st, NewRevisionManager(st))
	rm := NewRevisionManager(st)
	syncMgr := NewSyncManager(st, rm, NewSessionManager(), nm)
	sm := NewScriptManager(st, rm, syncMgr)
	tm := NewTaskManager(st, rm, syncMgr)
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-u", "host-u", "10.0.0.60", "linux", "amd64", "1.0", "native", false, nil)
	if err != nil {
		t.Fatal(err)
	}

	first := &models.Script{Name: "uniq-script", Interpreter: "bash", Content: "echo a", Enabled: true}
	if err := sm.Create(ctx, first); err != nil {
		t.Fatalf("create first script: %v", err)
	}
	dup := &models.Script{Name: "Uniq-Script", Interpreter: "bash", Content: "echo b", Enabled: true}
	if err := sm.Create(ctx, dup); err == nil || !strings.Contains(err.Error(), "already exists") {
		t.Fatalf("expected duplicate script name rejection, got %v", err)
	}
	// 重命名自身不冲突
	first.Name = "uniq-script"
	if err := sm.Update(ctx, first); err != nil {
		t.Fatalf("update self should pass: %v", err)
	}
	// 重复参数名
	badParams := &models.Script{
		Name: "uniq-script-2", Interpreter: "bash", Content: "echo c", Enabled: true,
		Parameters: []models.Parameter{{Name: "P"}, {Name: "p"}},
	}
	if err := sm.Create(ctx, badParams); err == nil || !strings.Contains(err.Error(), "duplicate parameter name") {
		t.Fatalf("expected duplicate parameter rejection, got %v", err)
	}

	task := &models.Task{Name: "uniq-task", Type: models.TaskTypeCommand, Command: "true", Enabled: true,
		Target: models.Target{Type: "node", NodeIDs: []string{node.ID}}}
	if err := tm.Create(ctx, task); err != nil {
		t.Fatalf("create task: %v", err)
	}
	dupTask := &models.Task{Name: "uniq-task", Type: models.TaskTypeCommand, Command: "true", Enabled: true,
		Target: models.Target{Type: "node", NodeIDs: []string{node.ID}}}
	if err := tm.Create(ctx, dupTask); err == nil || !strings.Contains(err.Error(), "task name already exists") {
		t.Fatalf("expected duplicate task name rejection, got %v", err)
	}
}
