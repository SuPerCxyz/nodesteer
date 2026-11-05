package hub

import (
	"context"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestScheduleReferencedByMonitoringRuleCannotBeDeletedOrUpdated(t *testing.T) {
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	revisions := NewRevisionManager(st)
	sessions := NewSessionManager()
	nodes := NewNodeManager(st, revisions)
	syncMgr := NewSyncManager(st, revisions, sessions, nodes)
	execs := NewExecutionManager(st, sessions, nodes, nil)
	schedules := NewScheduleManager(st, revisions, syncMgr, nodes, execs)
	if err := st.UpsertNode(ctx, &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline}); err != nil {
		t.Fatal(err)
	}
	task := &models.Task{Name: "task", Type: models.TaskTypeCommand, Command: "true", Enabled: true}
	if err := st.CreateTask(ctx, task); err != nil {
		t.Fatal(err)
	}
	ids := make([]string, 0, 3)
	for _, name := range []string{"action", "verify", "compensate"} {
		schedule := &models.Schedule{Name: name, TaskID: task.ID, Type: models.ScheduleTypeManual, Target: models.Target{Type: "node", NodeIDs: []string{"node"}}, Enabled: true}
		if err := schedules.Create(ctx, schedule); err != nil {
			t.Fatal(err)
		}
		ids = append(ids, schedule.ID)
	}
	repo := monitoring.NewRepository(st.DB())
	target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: ids[0], VerifyScheduleID: ids[1], CompensateScheduleID: ids[2], Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(ctx, &rule); err != nil {
		t.Fatal(err)
	}
	for _, id := range ids {
		if err := schedules.Delete(ctx, id); err == nil || !strings.Contains(err.Error(), "referenced by monitoring rules") {
			t.Fatalf("delete %s err=%v", id, err)
		}
		updated := &models.Schedule{ID: id, Name: "changed"}
		if err := schedules.Update(ctx, updated); err == nil || !strings.Contains(err.Error(), "referenced by monitoring rules") {
			t.Fatalf("update %s err=%v", id, err)
		}
	}
	if err := repo.DeleteRule(ctx, rule.ID); err != nil {
		t.Fatal(err)
	}
	for _, id := range ids {
		if err := schedules.Delete(ctx, id); err != nil {
			t.Fatalf("delete unreferenced %s: %v", id, err)
		}
	}
}
