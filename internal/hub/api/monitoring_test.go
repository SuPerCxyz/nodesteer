package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

func TestMonitoringTargetAPIAndPermissions(t *testing.T) {
	s, st := newMeTestServer(t)
	s.SetMonitoring(monitoring.NewRepository(st.DB()))
	handler := s.Routes()
	body := `{"name":"service","url":"http://example.test","interval_sec":60,"timeout_sec":5,"enabled":true}`
	do := func(method, path, token, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		return rec
	}
	if rec := do(http.MethodPost, "/api/monitoring/http-targets", "", body); rec.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated = %d", rec.Code)
	}
	hash, err := auth.HashPassword("password")
	if err != nil {
		t.Fatal(err)
	}
	for _, user := range []*models.User{{ID: "admin", Username: "admin", PasswordHash: hash, Role: models.RoleAdministrator}, {ID: "viewer", Username: "viewer", PasswordHash: hash, Role: models.RoleViewer}} {
		if err := st.CreateUser(t.Context(), user); err != nil {
			t.Fatal(err)
		}
	}
	viewer := loginAs(t, s, "viewer", "password")
	if rec := do(http.MethodPost, "/api/monitoring/http-targets", viewer, body); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer write = %d", rec.Code)
	}
	admin := loginAs(t, s, "admin", "password")
	rec := do(http.MethodPost, "/api/monitoring/http-targets", admin, body)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create = %d %s", rec.Code, rec.Body.String())
	}
	var target monitoring.Target
	if err := json.Unmarshal(rec.Body.Bytes(), &target); err != nil {
		t.Fatal(err)
	}
	if rec := do(http.MethodGet, "/api/monitoring/latest", viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("viewer latest = %d", rec.Code)
	}
	if rec := do(http.MethodGet, "/api/monitoring/latest?limit=1001", admin, ""); rec.Code != http.StatusBadRequest {
		t.Fatalf("unbounded latest = %d", rec.Code)
	}
	if rec := do(http.MethodGet, "/api/monitoring/latest?limit=1", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("admin latest = %d", rec.Code)
	}
	if rec := do(http.MethodGet, "/api/monitoring/http-targets/"+target.ID+"/observations", viewer, ""); rec.Code != http.StatusForbidden {
		t.Fatalf("history = %d %s", rec.Code, rec.Body.String())
	}
	if rec := do(http.MethodGet, "/api/monitoring/http-targets/"+target.ID+"/observations", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("admin history = %d %s", rec.Code, rec.Body.String())
	}
	if rec := do(http.MethodDelete, "/api/monitoring/http-targets/"+target.ID, admin, ""); rec.Code != http.StatusNoContent {
		t.Fatalf("delete = %d %s", rec.Code, rec.Body.String())
	}
	var count int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM audit_logs WHERE resource = 'monitoring_http_target'`).Scan(&count); err != nil || count != 2 {
		t.Fatalf("audit count = %d, err = %v", count, err)
	}
}
