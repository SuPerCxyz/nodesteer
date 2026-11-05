package api

import (
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

func TestMonitoringTargetCRUDValidationAndPermissions(t *testing.T) {
	s, st := newMonitoringAPI(t, false)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	viewer := loginAs(t, s, "viewer", "password")

	validBody := `{"name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":5,"enabled":true}`

	if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", viewer, validBody); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer create = %d %s, want 403", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets", viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer list = %d %s, want 403", rec.Code, rec.Body.String())
	}

	for name, body := range map[string]string{
		"empty body":                  "",
		"malformed body":              `{"name":`,
		"missing name":                `{"url":"http://example.test","interval_sec":60,"timeout_sec":5}`,
		"unsupported scheme":          `{"name":"service","url":"ftp://example.test","interval_sec":60,"timeout_sec":5}`,
		"unsupported kind":            `{"name":"service","kind":"database","url":"http://example.test","interval_sec":60,"timeout_sec":5}`,
		"interval too small":          `{"name":"service","url":"http://example.test","interval_sec":4,"timeout_sec":1}`,
		"interval too large":          `{"name":"service","url":"http://example.test","interval_sec":86401,"timeout_sec":5}`,
		"timeout zero":                `{"name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":0}`,
		"timeout not below":           `{"name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":60}`,
		"url userinfo":                `{"name":"service","url":"http://user:pass@example.test","interval_sec":60,"timeout_sec":5}`,
		"url query":                   `{"name":"service","url":"http://example.test/?token=x","interval_sec":60,"timeout_sec":5}`,
		"credential over http":        `{"name":"service","url":"http://example.test","credential_env":"NODESTEER_MONITOR_SECRET_API","interval_sec":60,"timeout_sec":5}`,
		"credential without prefix":   `{"name":"service","url":"https://example.test","credential_env":"HOME","interval_sec":60,"timeout_sec":5}`,
		"query secret without prefix": `{"name":"service","url":"https://example.test","interval_sec":60,"timeout_sec":5,"query_params":[{"name":"api_key","secret_env":"HOME"}]}`,
		"client supplied id":          `{"id":"client-id","name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":5}`,
		"plain sensitive param":       `{"name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":5,"query_params":[{"name":"api_key","value":"literal"}]}`,
		"invalid query param":         `{"name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":5,"query_params":[{"name":"","value":"x"}]}`,
		"invalid tcp target":          `{"name":"service","kind":"tcp","url":"http://example.test","interval_sec":60,"timeout_sec":5}`,
		"invalid icmp target":         `{"name":"service","kind":"icmp","url":"icmp://example.test:1","interval_sec":60,"timeout_sec":5}`,
		"invalid json path depth":     `{"name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":5,"json_path":["a","b","c","d","e","f","g","h","i","j","k","l","m","n","o","p","q"]}`,
	} {
		t.Run(name, func(t *testing.T) {
			if rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", admin, body); rec.Code != http.StatusBadRequest {
				t.Fatalf("POST %s = %d %s, want 400", name, rec.Code, rec.Body.String())
			}
		})
	}

	rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", admin, validBody)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create = %d %s", rec.Code, rec.Body.String())
	}
	target := decodeResource[monitoring.Target](t, rec)
	if target.ID == "" || target.Name != "service" || !target.Enabled {
		t.Fatalf("created target = %+v", target)
	}

	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("list = %d %s", rec.Code, rec.Body.String())
	} else if got := decodeResource[[]monitoring.Target](t, rec); len(got) != 1 || got[0].ID != target.ID {
		t.Fatalf("targets = %+v", got)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+target.ID, admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("get = %d %s", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+target.ID, viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer get = %d %s, want 403", rec.Code, rec.Body.String())
	}

	updateBody := `{"name":"service-updated","url":"http://example.test","interval_sec":120,"timeout_sec":10,"enabled":false}`
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/http-targets/"+target.ID, admin, updateBody); rec.Code != http.StatusOK {
		t.Fatalf("update = %d %s", rec.Code, rec.Body.String())
	}
	rec = doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+target.ID, admin, "")
	updated := decodeResource[monitoring.Target](t, rec)
	if updated.Name != "service-updated" || updated.IntervalSec != 120 || updated.TimeoutSec != 10 || updated.Enabled {
		t.Fatalf("updated target = %+v", updated)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/http-targets/"+target.ID, viewer, updateBody); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer update = %d %s, want 403", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/http-targets/"+target.ID, admin, `{"id":"other","name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":5}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("mismatched update id = %d %s, want 400", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/http-targets/"+target.ID, admin, `{"name":"service","url":"http://example.test","interval_sec":0,"timeout_sec":5}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid update = %d %s, want 400", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/http-targets/missing", admin, validBody); rec.Code != http.StatusNotFound {
		t.Fatalf("update missing = %d %s, want 404", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/http-targets/missing", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("delete missing = %d %s, want 404", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/http-targets/"+target.ID, viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer delete = %d %s, want 403", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/http-targets/"+target.ID, admin, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("delete = %d %s, want 204", rec.Code, rec.Body.String())
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+target.ID, admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("get deleted = %d %s, want 404", rec.Code, rec.Body.String())
	}

	var count int
	for action, want := range map[string]int{"create": 1, "update": 1, "delete": 1} {
		if err := st.DB().QueryRow(`SELECT COUNT(*) FROM audit_logs WHERE resource = 'monitoring_http_target' AND action = ? AND username = 'admin' AND resource_id = ?`, action, target.ID).Scan(&count); err != nil || count != want {
			t.Fatalf("audit %s count = %d, err = %v, want %d", action, count, err, want)
		}
	}
}

func TestMonitoringTargetDeleteRejectsRuleReference(t *testing.T) {
	s, st := newMonitoringAPI(t, false)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")
	handler := s.Routes()

	target := monitoring.Target{Name: "referenced", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := s.monitoring.Create(t.Context(), &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "rule", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "200", Enabled: true}
	if err := s.monitoring.CreateRule(t.Context(), &rule); err != nil {
		t.Fatal(err)
	}

	rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/http-targets/"+target.ID, admin, "")
	if rec.Code != http.StatusConflict {
		t.Fatalf("delete referenced target = %d %s, want 409", rec.Code, rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), "sql:") {
		t.Fatalf("conflict response leaks SQL details: %s", rec.Body.String())
	}
	if _, err := s.monitoring.Get(t.Context(), target.ID); err != nil {
		t.Fatalf("referenced target removed: %v", err)
	}

	if err := s.monitoring.DeleteRule(t.Context(), rule.ID); err != nil {
		t.Fatal(err)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodDelete, "/api/monitoring/http-targets/"+target.ID, admin, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("delete unreferenced target = %d %s, want 204", rec.Code, rec.Body.String())
	}
}

func TestMonitoringTargetUpdateRejectsPendingExecution(t *testing.T) {
	s, st := newMonitoringAPI(t, false)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")

	target := monitoring.Target{Name: "pending-target", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := s.monitoring.Create(t.Context(), &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "pending-rule", TargetID: target.ID, ScheduleID: "schedule-1", Operator: "status_eq", Expected: "200", Enabled: true}
	if err := s.monitoring.CreateRule(t.Context(), &rule); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`UPDATE monitoring_rules SET pending_id = 'event-pending' WHERE id = ?`, rule.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`INSERT INTO monitoring_rule_events (id, rule_id, target_id, observed_at, status) VALUES ('event-pending', ?, ?, ?, 'pending')`, rule.ID, target.ID, time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		t.Fatal(err)
	}

	body := `{"name":"changed","url":"http://example.test","interval_sec":120,"timeout_sec":10,"enabled":true}`
	rec := doMonitoringRequest(t, s.Routes(), http.MethodPut, "/api/monitoring/http-targets/"+target.ID, admin, body)
	if rec.Code != http.StatusConflict {
		t.Fatalf("update target with pending execution = %d %s, want 409", rec.Code, rec.Body.String())
	}
	got, err := s.monitoring.Get(t.Context(), target.ID)
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != target.Name || got.IntervalSec != target.IntervalSec {
		t.Fatalf("pending target was updated: %+v", got)
	}
}

func TestMonitoringTargetNodeMetricRequiresExistingNode(t *testing.T) {
	s, st := newMonitoringAPI(t, false)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")
	handler := s.Routes()
	s.nodes = hub.NewNodeManager(st, hub.NewRevisionManager(st))

	body := `{"name":"cpu","kind":"node_metric","monitored_node_id":"node-1","metric":"cpu.usage","series":"cpu.usage","interval_sec":60,"timeout_sec":5,"enabled":true}`
	rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", admin, body)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("node metric without node = %d %s, want 400", rec.Code, rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), "sql:") {
		t.Fatalf("node metric error leaks SQL details: %s", rec.Body.String())
	}

	var raw int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM monitoring_http_targets`).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	if raw != 0 {
		t.Fatalf("invalid node metric target persisted: %d", raw)
	}

	createMonitoringNode(t, st, "node-1")
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", admin, body)
	if rec.Code != http.StatusCreated {
		t.Fatalf("node metric with existing node = %d %s, want 201", rec.Code, rec.Body.String())
	}
}

func TestMonitoringTargetNodeMetricRejectsSeriesFromAnotherMetric(t *testing.T) {
	s, st := newMonitoringAPI(t, false)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")
	s.nodes = hub.NewNodeManager(st, hub.NewRevisionManager(st))
	createMonitoringNode(t, st, "node-1")

	body := `{"name":"cpu","kind":"node_metric","monitored_node_id":"node-1","metric":"cpu.usage","series":"memory.usage","interval_sec":60,"timeout_sec":5,"enabled":true}`
	rec := doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/http-targets", admin, body)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("mismatched series = %d %s, want 400", rec.Code, rec.Body.String())
	}
}

func TestMonitoringTargetListAndDetailDoNotResolveCredentialSecrets(t *testing.T) {
	const credentialSecret = "credential-secret-value-7f3c"
	const querySecret = "query-secret-value-9a1d"
	t.Setenv("NODESTEER_MONITOR_SECRET_API", credentialSecret)
	t.Setenv("NODESTEER_MONITOR_SECRET_QUERY", querySecret)

	s, st := newMonitoringAPI(t, false)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")
	handler := s.Routes()

	body := `{"name":"secret-service","url":"https://example.test","credential_env":"NODESTEER_MONITOR_SECRET_API","query_params":[{"name":"api_key","secret_env":"NODESTEER_MONITOR_SECRET_QUERY"},{"name":"region","value":"north"}],"interval_sec":60,"timeout_sec":5,"enabled":true}`
	rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", admin, body)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create = %d %s", rec.Code, rec.Body.String())
	}
	target := decodeResource[monitoring.Target](t, rec)
	if strings.Contains(rec.Body.String(), credentialSecret) || strings.Contains(rec.Body.String(), querySecret) {
		t.Fatalf("create response leaked secret value: %s", rec.Body.String())
	}
	if target.CredentialEnv != "NODESTEER_MONITOR_SECRET_API" || len(target.QueryParams) != 2 {
		t.Fatalf("created target = %+v", target)
	}
	if target.QueryParams[0].SecretEnv != "NODESTEER_MONITOR_SECRET_QUERY" || target.QueryParams[0].Value != "" {
		t.Fatalf("query secret reference = %+v", target.QueryParams[0])
	}
	if target.QueryParams[1].Value != "north" {
		t.Fatalf("plain query param = %+v", target.QueryParams[1])
	}

	for name, path := range map[string]string{
		"list":   "/api/monitoring/http-targets",
		"detail": "/api/monitoring/http-targets/" + target.ID,
	} {
		t.Run(name, func(t *testing.T) {
			rec := doMonitoringRequest(t, handler, http.MethodGet, path, admin, "")
			if rec.Code != http.StatusOK {
				t.Fatalf("GET %s = %d %s", path, rec.Code, rec.Body.String())
			}
			response := rec.Body.String()
			if strings.Contains(response, credentialSecret) || strings.Contains(response, querySecret) {
				t.Fatalf("GET %s leaked secret value: %s", path, response)
			}
			if !strings.Contains(response, "NODESTEER_MONITOR_SECRET_API") || !strings.Contains(response, "NODESTEER_MONITOR_SECRET_QUERY") {
				t.Fatalf("GET %s should retain credential references: %s", path, response)
			}
		})
	}

	var config string
	if err := st.DB().QueryRow(`SELECT config_json FROM monitoring_http_targets WHERE id = ?`, target.ID).Scan(&config); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(config, credentialSecret) || strings.Contains(config, querySecret) {
		t.Fatalf("stored target config contains secret value: %s", config)
	}
	if !strings.Contains(config, "NODESTEER_MONITOR_SECRET_API") || !strings.Contains(config, "NODESTEER_MONITOR_SECRET_QUERY") {
		t.Fatalf("stored target config should contain credential references: %s", config)
	}

	// 更新路径同样不得把已解析的 secret 写入响应或存储。
	updateBody := `{"name":"secret-service","url":"https://example.test","credential_env":"NODESTEER_MONITOR_SECRET_API","query_params":[{"name":"api_key","secret_env":"NODESTEER_MONITOR_SECRET_QUERY"},{"name":"region","value":"north"}],"interval_sec":120,"timeout_sec":10,"enabled":true}`
	rec = doMonitoringRequest(t, handler, http.MethodPut, "/api/monitoring/http-targets/"+target.ID, admin, updateBody)
	if rec.Code != http.StatusOK {
		t.Fatalf("update = %d %s", rec.Code, rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), credentialSecret) || strings.Contains(rec.Body.String(), querySecret) {
		t.Fatalf("update response leaked secret value: %s", rec.Body.String())
	}

	// 非法凭据引用必须被拒绝，且错误响应不能回显调用方提供的值。
	badBody := `{"name":"bad","url":"http://example.test","credential_env":"` + credentialSecret + `","interval_sec":60,"timeout_sec":5}`
	rec = doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", admin, badBody)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid credential reference = %d %s, want 400", rec.Code, rec.Body.String())
	}
	if strings.Contains(rec.Body.String(), credentialSecret) {
		t.Fatalf("validation response echoed credential value: %s", rec.Body.String())
	}

	var count int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM monitoring_http_targets`).Scan(&count); err != nil || count != 1 {
		t.Fatalf("target count after invalid create = %d, err = %v", count, err)
	}
}
