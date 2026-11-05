package store

import (
	"context"
	"fmt"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"testing"
	"time"
)

func TestExecutionScheduleScope(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	for i, item := range []struct{ schedule, node string }{{"a", "n1"}, {"a", "n1"}, {"a", "n2"}, {"b", "n1"}, {"", "n1"}} {
		e := &models.Execution{ID: fmt.Sprintf("scope-%d", i), TaskID: "same-task", ScheduleID: item.schedule, NodeID: item.node, Status: models.ExecStatusSuccess, CreatedAt: time.Now().Add(time.Duration(i) * time.Second)}
		if err := s.CreateExecution(ctx, e); err != nil {
			t.Fatal(err)
		}
	}
	for _, tc := range []struct {
		name          string
		filter        ExecutionFilter
		count, length int
	}{{"unfiltered", ExecutionFilter{}, 5, 5}, {"schedule", ExecutionFilter{ScheduleID: "a"}, 3, 3}, {"combined", ExecutionFilter{ScheduleID: "a", NodeID: "n1"}, 2, 2}, {"page", ExecutionFilter{ScheduleID: "a", Limit: 1, Offset: 1}, 3, 1}, {"missing", ExecutionFilter{ScheduleID: "missing"}, 0, 0}} {
		t.Run(tc.name, func(t *testing.T) {
			rows, err := s.ListExecutions(ctx, tc.filter)
			if err != nil {
				t.Fatal(err)
			}
			count, err := s.CountExecutions(ctx, tc.filter)
			if err != nil {
				t.Fatal(err)
			}
			if len(rows) != tc.length || int(count) != tc.count {
				t.Fatalf("rows=%d count=%d", len(rows), count)
			}
			for _, row := range rows {
				if tc.filter.ScheduleID != "" && row.ScheduleID != tc.filter.ScheduleID {
					t.Fatalf("wrong schedule: %s", row.ScheduleID)
				}
			}
		})
	}
}
