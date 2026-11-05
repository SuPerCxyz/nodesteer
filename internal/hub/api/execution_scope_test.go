package api

import (
	"encoding/json"
	"fmt"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"net/http"
	"testing"
)

func TestExecutionScheduleScopeAPI(t *testing.T) {
	c := newContractServer(t)
	for i, item := range []struct{ schedule, node string }{{"a", "n1"}, {"a", "n1"}, {"a", "n2"}, {"b", "n1"}, {"", "n1"}} {
		if err := c.store.CreateExecution(t.Context(), &models.Execution{ID: fmt.Sprintf("scope-%d", i), TaskID: "same-task", ScheduleID: item.schedule, NodeID: item.node, Status: models.ExecStatusSuccess}); err != nil {
			t.Fatal(err)
		}
	}
	for _, tc := range []struct {
		query         string
		total, length int
	}{{"", 5, 5}, {"?schedule_id=", 5, 5}, {"?schedule_id=a", 3, 3}, {"?schedule_id=a&node_id=n1", 2, 2}, {"?schedule_id=a&limit=1&offset=1", 3, 1}, {"?schedule_id=missing", 0, 0}} {
		t.Run(tc.query, func(t *testing.T) {
			rec := c.do(t, http.MethodGet, "/api/executions"+tc.query, "")
			if rec.Code != http.StatusOK {
				t.Fatalf("%d %s", rec.Code, rec.Body.String())
			}
			var rows []models.Execution
			if err := json.Unmarshal(rec.Body.Bytes(), &rows); err != nil {
				t.Fatal(err)
			}
			if len(rows) != tc.length || rec.Header().Get("X-Total-Count") != fmt.Sprint(tc.total) {
				t.Fatalf("rows=%d total=%s", len(rows), rec.Header().Get("X-Total-Count"))
			}
		})
	}
}
