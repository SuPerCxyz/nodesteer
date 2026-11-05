package api

import (
	"bytes"
	"log/slog"
	"net/http"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

func TestMonitoringCredentialValuesAreNotStoredReturnedOrLogged(t *testing.T) {
	const credentialSecret = "credential-log-secret-4b2f"
	const querySecret = "query-log-secret-8c6a"
	t.Setenv("NODESTEER_MONITOR_SECRET_API", credentialSecret)
	t.Setenv("NODESTEER_MONITOR_SECRET_QUERY", querySecret)

	var logs bytes.Buffer
	s, st := newMonitoringAPIWithLogger(t, slog.New(slog.NewTextHandler(&logs, &slog.HandlerOptions{Level: slog.LevelDebug})), false)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")
	handler := s.Routes()

	body := `{"name":"credential-service","url":"https://example.test","credential_env":"NODESTEER_MONITOR_SECRET_API","query_params":[{"name":"api_key","secret_env":"NODESTEER_MONITOR_SECRET_QUERY"}],"interval_sec":60,"timeout_sec":5,"enabled":true}`
	rec := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", admin, body)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create = %d %s", rec.Code, rec.Body.String())
	}
	target := decodeResource[monitoring.Target](t, rec)

	for _, path := range map[string]string{
		"list":   "/api/monitoring/http-targets",
		"detail": "/api/monitoring/http-targets/" + target.ID,
	} {
		rec := doMonitoringRequest(t, handler, http.MethodGet, path, admin, "")
		if rec.Code != http.StatusOK {
			t.Fatalf("GET %s = %d %s", path, rec.Code, rec.Body.String())
		}
		if strings.Contains(rec.Body.String(), credentialSecret) || strings.Contains(rec.Body.String(), querySecret) {
			t.Fatalf("GET %s leaked a secret value: %s", path, rec.Body.String())
		}
	}

	var config string
	if err := st.DB().QueryRow(`SELECT config_json FROM monitoring_http_targets WHERE id = ?`, target.ID).Scan(&config); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(config, credentialSecret) || strings.Contains(config, querySecret) {
		t.Fatalf("stored config contains a secret value: %s", config)
	}

	// 触发一次验证失败，确认错误路径也不会把 secret 写入日志。
	bad := doMonitoringRequest(t, handler, http.MethodPost, "/api/monitoring/http-targets", admin,
		`{"name":"bad","url":"http://example.test","credential_env":"`+credentialSecret+`","interval_sec":60,"timeout_sec":5}`)
	if bad.Code != http.StatusBadRequest {
		t.Fatalf("invalid credential target = %d %s, want 400", bad.Code, bad.Body.String())
	}
	if strings.Contains(bad.Body.String(), credentialSecret) {
		t.Fatalf("validation response leaked credential value: %s", bad.Body.String())
	}

	// 让存储层解码失败以触发 error 日志，确认日志路径同样不包含 secret 值。
	if _, err := st.DB().Exec(`INSERT INTO monitoring_http_targets (id, config_json, updated_at) VALUES ('broken-target', '{', '2026-11-03T00:00:00Z')`); err != nil {
		t.Fatal(err)
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets", admin, ""); rec.Code != http.StatusInternalServerError {
		t.Fatalf("broken target list = %d %s, want 500", rec.Code, rec.Body.String())
	}
	if strings.Contains(logs.String(), credentialSecret) || strings.Contains(logs.String(), querySecret) {
		t.Fatalf("logs leaked a secret value: %s", logs.String())
	}
	if logs.Len() == 0 {
		t.Fatal("expected the storage decode failure to be logged")
	}
}
