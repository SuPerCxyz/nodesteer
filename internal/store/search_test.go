package store

import (
	"fmt"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func TestExecutionSearchBeforePaging(t *testing.T) {
	s := newTestStore(t)
	ctx := t.Context()
	for _, task := range []*models.Task{
		{ID: "task-target", Name: "Deploy MIXED%_ literal", Type: models.TaskTypeCommand, Command: "true", Revision: 1},
		{ID: "task-normal", Name: "Deploy normal", Type: models.TaskTypeCommand, Command: "true", Revision: 1},
	} {
		if err := s.CreateTask(ctx, task); err != nil {
			t.Fatal(err)
		}
	}
	for i := 0; i < 30; i++ {
		e := &models.Execution{ID: fmt.Sprintf("exec-%02d", i), TaskID: "task-normal", NodeID: fmt.Sprintf("node-%02d", i),
			Status: models.ExecStatusSuccess, TriggerType: models.TriggerManual}
		if i == 0 {
			e.ID, e.TaskID = "exec-target%_", "task-target"
		}
		if err := s.CreateExecution(ctx, e); err != nil {
			t.Fatal(err)
		}
	}
	for _, filter := range []ExecutionFilter{
		{Query: "MiXeD%_", Limit: 20},
		{Query: "target%_", Status: models.ExecStatusSuccess, TriggerType: models.TriggerManual, Limit: 1},
		{Query: "node-00", Limit: 1},
	} {
		list, err := s.ListExecutions(ctx, filter)
		if err != nil || len(list) != 1 || list[0].ID != "exec-target%_" {
			t.Fatalf("cross-page filtered list=%+v err=%v", list, err)
		}
		count, err := s.CountExecutions(ctx, filter)
		if err != nil || count != 1 {
			t.Fatalf("filtered count=%d err=%v", count, err)
		}
		filter.Offset = 1
		list, err = s.ListExecutions(ctx, filter)
		if err != nil || len(list) != 0 {
			t.Fatalf("paging must follow filtering: list=%+v err=%v", list, err)
		}
		count, err = s.CountExecutions(ctx, filter)
		if err != nil || count != 1 {
			t.Fatalf("count must ignore offset: count=%d err=%v", count, err)
		}
	}
	for _, filter := range []ExecutionFilter{{Query: "missing"}, {Query: "MIXED%_", TriggerType: "scheduled"}} {
		count, err := s.CountExecutions(ctx, filter)
		if err != nil || count != 0 {
			t.Fatalf("no-match count=%d err=%v", count, err)
		}
	}
}

func TestAuditSearchLiteralFieldsAndPaging(t *testing.T) {
	s := newTestStore(t)
	ctx := t.Context()
	for i := 0; i < 30; i++ {
		a := &models.AuditLog{ID: fmt.Sprintf("audit-%02d", i), Username: "qa", Action: "update", Resource: "task", ResourceID: "normal", Detail: "normal"}
		if i == 0 {
			a.Username, a.ResourceID, a.Detail = "Operator-Target", "resource%_literal", "Exact Detail Needle"
		}
		if err := s.AddAudit(ctx, a); err != nil {
			t.Fatal(err)
		}
	}
	for _, q := range []string{"operator-target", "resource%_", "DETAIL needle"} {
		filter := AuditFilter{Query: q, Action: "update", Limit: 1}
		list, err := s.ListAudit(ctx, filter)
		if err != nil || len(list) != 1 || list[0].ID != "audit-00" {
			t.Fatalf("audit q=%q list=%+v err=%v", q, list, err)
		}
		count, err := s.CountAudit(ctx, filter)
		if err != nil || count != 1 {
			t.Fatalf("audit q=%q count=%d err=%v", q, count, err)
		}
		filter.Offset = 1
		list, err = s.ListAudit(ctx, filter)
		if err != nil || len(list) != 0 {
			t.Fatalf("audit paging before filter: list=%+v err=%v", list, err)
		}
	}
	count, err := s.CountAudit(ctx, AuditFilter{Query: "absent%_"})
	if err != nil || count != 0 {
		t.Fatalf("audit wildcard input must be literal: count=%d err=%v", count, err)
	}
}
