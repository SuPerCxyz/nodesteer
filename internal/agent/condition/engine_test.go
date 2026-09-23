package condition

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func TestCompareOperators(t *testing.T) {
	cases := []struct {
		actual, op, expected string
		want                 bool
	}{
		{"5", "==", "5", true},
		{"5", "==", "6", false},
		{"5", "!=", "6", true},
		{"10", ">", "5", true},
		{"5", "<", "10", true},
		{"10", ">=", "10", true},
		{"5", "<=", "5", true},
		{"80.5", ">", "50", true},
	}
	for _, c := range cases {
		if got := compare(c.actual, c.op, c.expected); got != c.want {
			t.Errorf("compare(%s %s %s) = %v, want %v", c.actual, c.op, c.expected, got, c.want)
		}
	}
}

func TestEngineLocalFileExists(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "testfile")
	os.WriteFile(p, []byte("x"), 0o644)

	e := New(OSProvider{})
	c := &models.Condition{Type: "local", Local: &models.LocalCondition{
		Metric: "file_exists", Operator: "==", Value: "true", Path: p,
	}}
	ok, ev, err := e.Evaluate(context.Background(), c)
	if err != nil {
		t.Fatalf("evaluate: %v", err)
	}
	if !ok || !ev {
		t.Fatalf("expected satisfied, got ok=%v ev=%v", ok, ev)
	}

	// 不存在的文件
	c2 := &models.Condition{Type: "local", Local: &models.LocalCondition{
		Metric: "file_exists", Operator: "==", Value: "true", Path: "/nonexistent-xyz",
	}}
	ok2, _, err := e.Evaluate(context.Background(), c2)
	if err != nil {
		t.Fatalf("evaluate: %v", err)
	}
	if ok2 {
		t.Fatalf("expected unsatisfied for missing file")
	}
}

func TestEngineAnd(t *testing.T) {
	e := New(OSProvider{})
	dir := t.TempDir()
	c := &models.Condition{Type: "and", And: []models.Condition{
		{Type: "local", Local: &models.LocalCondition{Metric: "file_exists", Operator: "==", Value: "true", Path: dir}},
		{Type: "local", Local: &models.LocalCondition{Metric: "file_exists", Operator: "==", Value: "true", Path: "/nonexistent-xyz"}},
	}}
	ok, _, err := e.Evaluate(context.Background(), c)
	if err != nil {
		t.Fatalf("evaluate: %v", err)
	}
	if ok {
		t.Fatalf("AND should be false when one is false")
	}
}

func TestRemoteConditionFailClosed(t *testing.T) {
	e := New(OSProvider{})
	c := &models.Condition{Type: "remote", Remote: &models.RemoteCondition{
		NodeID: "node2", Property: "online", Operator: "==", Value: "ONLINE",
	}}
	// Remote 不可用时返回 evaluated=false（Fail Closed）
	ok, ev, err := e.Evaluate(context.Background(), c)
	if err != nil {
		t.Fatalf("evaluate: %v", err)
	}
	if ok || ev {
		t.Fatalf("expected fail-closed (ok=false, ev=false), got ok=%v ev=%v", ok, ev)
	}
}
