package api

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// monitoringFailingStore 只覆盖节点读取，用于模拟驱动错误/SQLITE_BUSY，
// 其余 Store 方法仍委托给真实 SQLite 存储。
type monitoringFailingStore struct {
	store.Store
	getNodeErr error
}

func (f monitoringFailingStore) GetNode(context.Context, string) (*models.Node, error) {
	return nil, f.getNodeErr
}

func assertMonitoringStorageFailure(t *testing.T, rec *httptest.ResponseRecorder) {
	t.Helper()
	if rec.Code != http.StatusInternalServerError && rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("storage failure = %d %s, want 500/503", rec.Code, rec.Body.String())
	}
	body := strings.ToLower(rec.Body.String())
	for _, leak := range []string{"sql", "sqlite", "database", "locked", "closed", "no rows", "node lookup failed", "invalid character", "json"} {
		if strings.Contains(body, leak) {
			t.Fatalf("storage failure response leaked %q: %s", leak, rec.Body.String())
		}
	}
}

func TestMonitoringRulePrecheckStorageErrors(t *testing.T) {
	t.Run("target lookup driver failure", func(t *testing.T) {
		s, st := newMonitoringAPI(t, true)
		s.SetDevNoAuth(true)
		if err := st.DB().Close(); err != nil {
			t.Fatal(err)
		}
		body := `{"name":"rule","target_id":"target","schedule_id":"schedule","operator":"status_eq","expected":"200","enabled":true}`
		rec := doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/rules", "", body)
		assertMonitoringStorageFailure(t, rec)
	})

	t.Run("target config decode failure", func(t *testing.T) {
		s, st := newMonitoringAPI(t, true)
		s.SetDevNoAuth(true)
		target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
		if err := s.monitoring.Create(t.Context(), &target); err != nil {
			t.Fatal(err)
		}
		if _, err := st.DB().Exec(`UPDATE monitoring_http_targets SET config_json = '{' WHERE id = ?`, target.ID); err != nil {
			t.Fatal(err)
		}
		body := `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule","operator":"status_eq","expected":"200","enabled":true}`
		rec := doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/rules", "", body)
		assertMonitoringStorageFailure(t, rec)
	})

	t.Run("schedule config decode failure", func(t *testing.T) {
		s, st := newMonitoringAPI(t, true)
		s.SetDevNoAuth(true)
		target := monitoring.Target{Name: "service", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
		if err := s.monitoring.Create(t.Context(), &target); err != nil {
			t.Fatal(err)
		}
		createMonitoringSchedule(t, st, "schedule-bad")
		if _, err := st.DB().Exec(`UPDATE schedules SET definition_json = '{' WHERE id = 'schedule-bad'`); err != nil {
			t.Fatal(err)
		}
		body := `{"name":"rule","target_id":"` + target.ID + `","schedule_id":"schedule-bad","operator":"status_eq","expected":"200","enabled":true}`
		rec := doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/rules", "", body)
		assertMonitoringStorageFailure(t, rec)
	})
}

func TestMonitoringTargetSourceNodeNotFound(t *testing.T) {
	s, st := newMonitoringAPI(t, false)
	s.SetDevNoAuth(true)
	s.nodes = hub.NewNodeManager(st, hub.NewRevisionManager(st))
	body := `{"name":"probe","url":"http://example.test","source_node_id":"missing-node","interval_sec":60,"timeout_sec":5,"enabled":true}`
	rec := doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/http-targets", "", body)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "probe node not found") {
		t.Fatalf("missing source node = %d %s, want 400 probe node not found", rec.Code, rec.Body.String())
	}
}

func TestMonitoringTargetPrecheckStorageErrors(t *testing.T) {
	t.Run("source node driver failure", func(t *testing.T) {
		s, st := newMonitoringAPI(t, false)
		s.SetDevNoAuth(true)
		s.nodes = hub.NewNodeManager(monitoringFailingStore{Store: st, getNodeErr: errors.New("node lookup failed")}, hub.NewRevisionManager(st))
		body := `{"name":"probe","url":"http://example.test","source_node_id":"node-1","interval_sec":60,"timeout_sec":5,"enabled":true}`
		rec := doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/http-targets", "", body)
		assertMonitoringStorageFailure(t, rec)
	})

	t.Run("node metric node lookup driver failure", func(t *testing.T) {
		s, st := newMonitoringAPI(t, false)
		s.SetDevNoAuth(true)
		s.nodes = hub.NewNodeManager(st, hub.NewRevisionManager(st))
		if err := st.DB().Close(); err != nil {
			t.Fatal(err)
		}
		body := `{"name":"cpu","kind":"node_metric","monitored_node_id":"node-1","metric":"cpu.usage","series":"cpu.usage","interval_sec":60,"timeout_sec":5,"enabled":true}`
		rec := doMonitoringRequest(t, s.Routes(), http.MethodPost, "/api/monitoring/http-targets", "", body)
		assertMonitoringStorageFailure(t, rec)
	})
}
