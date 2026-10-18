package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// TestApplicationSubresourceMissingAppReturns404 应用子资源（assign/deploy）必须先做
// 存在性检查：此前 assign 返回 500、deploy 返回 400，并把存储层
// "sql: no rows in result set" 原文外泄给调用方
// （缺陷 E2E-20261016-ASSIGN-MISSING-APP-500-SQL-LEAK）。
func TestApplicationSubresourceMissingAppReturns404(t *testing.T) {
	c := newContractServer(t)
	const missing = "00000000-0000-0000-0000-0000000000ff"

	for _, tc := range []struct {
		name string
		body string
	}{
		{name: "assign", body: `{"node_ids":[]}`},
		{name: "deploy", body: `{"node_ids":[]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rec := c.do(t, http.MethodPost, "/api/applications/"+missing+"/"+tc.name, tc.body)
			if rec.Code != http.StatusNotFound {
				t.Fatalf("status = %d body = %s, want 404", rec.Code, rec.Body.String())
			}
			if !strings.Contains(rec.Body.String(), "application not found") {
				t.Fatalf("body = %s, want normalized application not found", rec.Body.String())
			}
			body := strings.ToLower(rec.Body.String())
			for _, leak := range []string{"sql", "no rows", "sqlite", "select "} {
				if strings.Contains(body, leak) {
					t.Fatalf("storage error leaked to client: %s", rec.Body.String())
				}
			}
		})
	}
}

// TestApplicationSubresourceExistingAppStillWorks 修复不得改变既有正常路径：
// 已存在的应用 assign 成功；目标节点不存在时返回业务文案（不含存储层原文）。
func TestApplicationSubresourceExistingAppStillWorks(t *testing.T) {
	c := newContractServer(t)
	rec := c.do(t, http.MethodPost, "/api/applications", `{"name":"contract-app","unit_name":"contract-app.service","binary_path":"/usr/local/bin/contract-app"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("create application = %d %s", rec.Code, rec.Body.String())
	}
	app := decodeResource[models.Application](t, rec)

	rec = c.do(t, http.MethodPost, "/api/applications/"+app.ID+"/assign", `{"node_ids":[]}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("assign existing application = %d %s", rec.Code, rec.Body.String())
	}

	rec = c.do(t, http.MethodPost, "/api/applications/"+app.ID+"/deploy", `{"node_ids":["missing-node"]}`)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "node missing-node not found") {
		t.Fatalf("deploy with missing node = %d %s, want 400 business message", rec.Code, rec.Body.String())
	}
	if strings.Contains(strings.ToLower(rec.Body.String()), "no rows") {
		t.Fatalf("storage error leaked to client: %s", rec.Body.String())
	}
}

// TestExecutionExitCodeSerialization 合法退出码 0 必须能序列化，且未执行/未知状态
// 不得被默认 0 伪装成已知成功退出码
// （缺陷 OBS-20261008-EXECUTION-ZERO-EXIT-CODE）。
func TestExecutionExitCodeSerialization(t *testing.T) {
	c := newContractServer(t)
	ctx := t.Context()

	rows := []struct {
		id     string
		status string
		code   int
		want   any // nil 表示字段必须缺省
	}{
		{id: "exec-success-zero", status: models.ExecStatusSuccess, code: 0, want: float64(0)},
		{id: "exec-failed-three", status: models.ExecStatusFailed, code: 3, want: float64(3)},
		{id: "exec-timed-out", status: models.ExecStatusTimedOut, code: -1, want: float64(-1)},
		{id: "exec-pending", status: models.ExecStatusPending, code: 0, want: nil},
		{id: "exec-running", status: models.ExecStatusRunning, code: 0, want: nil},
		{id: "exec-skipped", status: models.ExecStatusSkipped, code: 0, want: nil},
		// FAILED 且退出码为 0 表示进程根本没启动（下发失败等），退出码未知。
		{id: "exec-failed-no-run", status: models.ExecStatusFailed, code: 0, want: nil},
	}
	for _, row := range rows {
		ex := &models.Execution{
			ID: row.id, TaskID: "task-1", NodeID: "node-1",
			TriggerType: models.TriggerManual, Status: row.status,
			ExitCode: models.ExecutionExitCode(row.status, row.code),
		}
		if err := c.store.CreateExecution(ctx, ex); err != nil {
			t.Fatalf("create execution %s: %v", row.id, err)
		}
	}

	for _, row := range rows {
		t.Run(row.id, func(t *testing.T) {
			rec := c.do(t, http.MethodGet, "/api/executions/"+row.id, "")
			if rec.Code != http.StatusOK {
				t.Fatalf("get execution = %d %s", rec.Code, rec.Body.String())
			}
			var body map[string]any
			if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
				t.Fatalf("decode: %v", err)
			}
			got, present := body["exit_code"]
			if row.want == nil {
				if present {
					t.Fatalf("exit_code must be omitted for %s, got %v", row.status, got)
				}
				return
			}
			if !present {
				t.Fatalf("exit_code missing for %s (want %v)", row.status, row.want)
			}
			if got != row.want {
				t.Fatalf("exit_code = %v, want %v", got, row.want)
			}
		})
	}

	// 列表端点共享同一序列化规则。
	rec := c.do(t, http.MethodGet, "/api/executions", "")
	if rec.Code != http.StatusOK {
		t.Fatalf("list executions = %d %s", rec.Code, rec.Body.String())
	}
	var list []map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &list); err != nil {
		t.Fatalf("decode list: %v", err)
	}
	byID := map[string]map[string]any{}
	for _, item := range list {
		if id, ok := item["id"].(string); ok {
			byID[id] = item
		}
	}
	if got, ok := byID["exec-success-zero"]["exit_code"]; !ok || got != float64(0) {
		t.Fatalf("list exit_code for SUCCESS = %v (present=%v), want 0", got, ok)
	}
	if _, present := byID["exec-pending"]["exit_code"]; present {
		t.Fatal("list must omit exit_code for PENDING execution")
	}
}
