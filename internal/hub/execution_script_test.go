package hub

import (
	"context"
	"strings"
	"testing"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/store"
)

// TestRunBlocksDisabledScript 验证引用已禁用脚本的任务在运行前被拦截（缺陷 FAIL-A-006）。
func TestRunBlocksDisabledScript(t *testing.T) {
	ctx := context.Background()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	nm := NewNodeManager(st, NewRevisionManager(st))
	node, _, _, err := nm.RegisterOrUpdate(ctx, "agent-s", "host-s", "10.0.0.40", "linux", "amd64", "1.0", "native", false, map[string]bool{"script": true})
	if err != nil {
		t.Fatal(err)
	}
	rm := NewRevisionManager(st)
	sessions := NewSessionManager()
	syncMgr := NewSyncManager(st, rm, sessions, nm)
	sm := NewScriptManager(st, rm, syncMgr)
	sc := &models.Script{Name: "script-a", Interpreter: "bash", Content: "echo hi", Enabled: true}
	if err := sm.Create(ctx, sc); err != nil {
		t.Fatalf("create script: %v", err)
	}
	tm := NewTaskManager(st, rm, syncMgr)
	task := &models.Task{
		Name:     "task-a",
		Type:     models.TaskTypeScript,
		ScriptID: sc.ID,
		Enabled:  true,
		Target:   models.Target{Type: "node", NodeIDs: []string{node.ID}},
	}
	if err := tm.Create(ctx, task); err != nil {
		t.Fatalf("create task: %v", err)
	}

	em := NewExecutionManager(st, sessions, nm, nil)

	// 禁用脚本后，手动运行应被拦截
	sc.Enabled = false
	if err := sm.Update(ctx, sc); err != nil {
		t.Fatalf("disable script: %v", err)
	}
	if _, err := em.RunManual(ctx, task, []string{node.ID}, nil); err == nil ||
		!strings.Contains(err.Error(), "referenced script") || !strings.Contains(err.Error(), "is disabled") {
		t.Fatalf("expected disabled-script rejection, got %v", err)
	}

	// 重新启用后可正常创建执行（离线节点不会下发，但应创建记录）
	sc.Enabled = true
	if err := sm.Update(ctx, sc); err != nil {
		t.Fatalf("enable script: %v", err)
	}
	// 重新启用后脚本校验不再拦截；本用例未建立 Agent 会话，因此后续在派发阶段失败属预期
	if _, err := em.RunManual(ctx, task, []string{node.ID}, nil); err != nil &&
		strings.Contains(err.Error(), "referenced script") {
		t.Fatalf("script check should pass after re-enable, got %v", err)
	}
}
