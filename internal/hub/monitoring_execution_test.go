package hub

import (
	"context"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestMonitoringExecutionRejectsDisabledRuleAndChangedSchedule(t *testing.T) {
	for _, change := range []string{"rule", "schedule", "task", "membership", "label"} {
		t.Run(change, func(t *testing.T) {
			st, err := store.OpenInMemory()
			if err != nil {
				t.Fatal(err)
			}
			defer st.Close()
			ctx := context.Background()
			node := &models.Node{ID: "node", AgentID: "agent", Status: models.NodeStatusOnline, Capabilities: map[string]bool{models.CapScript: true}}
			if change == "label" {
				node.Labels = map[string]string{"role": "primary"}
			}
			if err := st.UpsertNode(ctx, node); err != nil {
				t.Fatal(err)
			}
			task := &models.Task{ID: "task", Name: "task", Type: models.TaskTypeCommand, Command: "true", Enabled: true, Revision: 1}
			if err := st.CreateTask(ctx, task); err != nil {
				t.Fatal(err)
			}
			target := models.Target{Type: "node", NodeIDs: []string{node.ID}}
			if change == "membership" {
				group := &models.Group{ID: "group", Name: "group", Type: "static"}
				if err := st.CreateGroup(ctx, group); err != nil {
					t.Fatal(err)
				}
				if err := st.AddGroupMember(ctx, group.ID, node.ID); err != nil {
					t.Fatal(err)
				}
				target = models.Target{Type: "group", GroupIDs: []string{group.ID}}
			}
			if change == "label" {
				target = models.Target{Type: "label", LabelKey: "role", LabelValue: "primary"}
			}
			schedule := &models.Schedule{ID: "schedule", TaskID: task.ID, Name: "schedule", Target: target, Type: models.ScheduleTypeManual, Enabled: true, Revision: 1}
			if err := st.CreateSchedule(ctx, schedule); err != nil {
				t.Fatal(err)
			}
			repo := monitoring.NewRepository(st.DB())
			monitor := monitoring.Target{Name: "external", URL: "http://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
			if err := repo.Create(ctx, &monitor); err != nil {
				t.Fatal(err)
			}
			rule := monitoring.Rule{Name: "rule", TargetID: monitor.ID, ScheduleID: schedule.ID, Operator: "status_eq", Expected: "503", Enabled: true}
			if err := repo.CreateRule(ctx, &rule); err != nil {
				t.Fatal(err)
			}
			claim, err := repo.ClaimRule(ctx, rule, monitor, true, time.Now(), time.Minute)
			if err != nil || claim == "" {
				t.Fatalf("claim=%s err=%v", claim, err)
			}
			switch change {
			case "rule":
				rule.Enabled = false
				if err := repo.UpdateRule(ctx, rule); err != nil {
					t.Fatal(err)
				}
			case "schedule":
				schedule.Enabled = false
				schedule.Revision++
				if err := st.UpdateSchedule(ctx, schedule, 1); err != nil {
					t.Fatal(err)
				}
			case "task":
				task.Revision++
				if err := st.UpdateTask(ctx, task, 1); err != nil {
					t.Fatal(err)
				}
			case "membership":
				if err := st.RemoveGroupMember(ctx, "group", node.ID); err != nil {
					t.Fatal(err)
				}
			case "label":
				if err := st.SetNodeLabels(ctx, node.ID, map[string]string{"role": "secondary"}); err != nil {
					t.Fatal(err)
				}
			}
			ex := &models.Execution{ID: "execution", TaskID: task.ID, TaskRevision: 1, NodeID: node.ID, ScheduleID: schedule.ID, ScheduleRevision: 1, Status: models.ExecStatusPending}
			em := newTestExecMgr(t, st)
			if err := em.createExecutionWithEvent(store.WithMonitoringEvent(ctx, claim), ex); err == nil {
				t.Fatal("changed authorization permitted execution")
			}
			if _, err := st.GetExecution(ctx, ex.ID); err == nil {
				t.Fatal("execution was not rolled back")
			}
		})
	}
}
