package monitoring

import "testing"

func TestValidateRuleTargetRestrictsOperatorsByKind(t *testing.T) {
	tests := []struct {
		name    string
		rule    Rule
		target  Target
		wantErr bool
	}{
		{name: "http status", rule: Rule{Operator: "status_eq"}, target: Target{Kind: "http"}},
		{name: "http probe error", rule: Rule{Operator: "probe_error"}, target: Target{Kind: "http"}},
		{name: "http eq without json path", rule: Rule{Operator: "eq"}, target: Target{Kind: "http"}, wantErr: true},
		{name: "http eq with json path", rule: Rule{Operator: "eq"}, target: Target{Kind: "http", JSONPath: []string{"value"}}},
		{name: "http expiry without json path", rule: Rule{Operator: "expires_within"}, target: Target{Kind: "http"}, wantErr: true},
		{name: "tcp probe error", rule: Rule{Operator: "probe_error"}, target: Target{Kind: "tcp"}},
		{name: "tcp status comparison", rule: Rule{Operator: "status_eq"}, target: Target{Kind: "tcp"}, wantErr: true},
		{name: "icmp numeric comparison", rule: Rule{Operator: "gt"}, target: Target{Kind: "icmp"}, wantErr: true},
		{name: "node metric numeric comparison", rule: Rule{Operator: "gt", Expected: "90"}, target: Target{Kind: "node_metric"}},
		{name: "node metric status comparison", rule: Rule{Operator: "status_eq"}, target: Target{Kind: "node_metric"}, wantErr: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := ValidateRuleTarget(tc.rule, tc.target)
			if tc.wantErr && err == nil {
				t.Fatal("invalid rule/target combination accepted")
			}
			if !tc.wantErr && err != nil {
				t.Fatalf("valid combination rejected: %v", err)
			}
		})
	}
}

func TestRuleValidateRejectsReusedSchedules(t *testing.T) {
	base := Rule{Name: "rule", TargetID: "target", ScheduleID: "action", Operator: "status_eq", Expected: "200", Enabled: true}
	verifySame := base
	verifySame.VerifyScheduleID = "action"
	if err := verifySame.Validate(); err == nil {
		t.Fatal("verify reused action schedule")
	}
	compensateSameAction := base
	compensateSameAction.CompensateScheduleID = "action"
	if err := compensateSameAction.Validate(); err == nil {
		t.Fatal("compensate reused action schedule")
	}
	compensateSameVerify := base
	compensateSameVerify.VerifyScheduleID = "verify"
	compensateSameVerify.CompensateScheduleID = "verify"
	if err := compensateSameVerify.Validate(); err == nil {
		t.Fatal("compensate reused verify schedule")
	}
	valid := base
	valid.VerifyScheduleID = "verify"
	valid.CompensateScheduleID = "compensate"
	if err := valid.Validate(); err != nil {
		t.Fatalf("distinct schedules rejected: %v", err)
	}
}

func TestTargetValidateNodeMetric(t *testing.T) {
	valid := Target{Name: "CPU", Kind: "node_metric", MonitoredNodeID: "node-1", Metric: "cpu.usage", Series: "cpu.usage|cpu=all", MaxAgeSec: 60, IntervalSec: 10, TimeoutSec: 2, Enabled: true}
	if err := valid.Validate(); err != nil {
		t.Fatalf("valid node metric target rejected: %v", err)
	}
	for name, mutate := range map[string]func(*Target){
		"missing node":        func(target *Target) { target.MonitoredNodeID = "" },
		"invalid metric":      func(target *Target) { target.Metric = "cpu usage" },
		"empty series":        func(target *Target) { target.Series = "" },
		"series other metric": func(target *Target) { target.Series = "memory.usage" },
		"negative max age":    func(target *Target) { target.MaxAgeSec = -1 },
		"excessive max age":   func(target *Target) { target.MaxAgeSec = 301 },
		"http fields":         func(target *Target) { target.URL = "https://example.test" },
	} {
		t.Run(name, func(t *testing.T) {
			target := valid
			mutate(&target)
			if err := target.Validate(); err == nil {
				t.Fatalf("invalid node metric target accepted: %+v", target)
			}
		})
	}
}

func TestTargetRejectsSensitiveQueryParameterAliases(t *testing.T) {
	base := Target{Name: "service", Kind: "http", URL: "https://example.test", IntervalSec: 10, TimeoutSec: 1, Enabled: true}
	for _, name := range []string{"api-key", "access-token", "x-api-key", "auth", "api_key", "apikey", "authorization"} {
		t.Run(name, func(t *testing.T) {
			target := base
			target.QueryParams = []QueryParam{{Name: name, Value: "plain"}}
			if err := target.Validate(); err == nil {
				t.Fatalf("sensitive parameter %q accepted without credential reference", name)
			}
			target.QueryParams[0].Value = ""
			target.QueryParams[0].SecretEnv = "NODESTEER_MONITOR_SECRET_TEST"
			if err := target.Validate(); err != nil {
				t.Fatalf("sensitive parameter %q rejected with credential reference: %v", name, err)
			}
		})
	}
	target := base
	target.QueryParams = []QueryParam{{Name: "limit", Value: "10"}}
	if err := target.Validate(); err != nil {
		t.Fatalf("non-sensitive parameter rejected: %v", err)
	}
}
