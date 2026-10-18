package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func userRequest(t *testing.T, s *Server, token, method, path string, body map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	data, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	req := httptest.NewRequest(method, path, bytes.NewReader(data))
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	if path == "/api/users" {
		s.withAuth(s.handleUsers, "read")(rec, req)
	} else {
		s.withAuth(s.handleUserByID, "read")(rec, req)
	}
	return rec
}

func TestUserCreateValidationDoesNotPersist(t *testing.T) {
	s, token := newEnrollmentTestServer(t)
	for _, body := range []map[string]string{
		{"username": "empty-password", "password": ""},
		{"username": "invalid-role", "password": "valid-password", "role": "superadmin"},
		{"username": "long-password", "password": strings.Repeat("p", 73)},
	} {
		before, err := s.store.CountAudit(t.Context(), store.AuditFilter{})
		if err != nil {
			t.Fatal(err)
		}
		rec := userRequest(t, s, token, http.MethodPost, "/api/users", body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("invalid create status=%d body=%s", rec.Code, rec.Body.String())
		}
		if _, err := s.store.GetUserByUsername(t.Context(), body["username"]); err == nil {
			t.Fatal("rejected user persisted")
		}
		after, err := s.store.CountAudit(t.Context(), store.AuditFilter{})
		if err != nil || after != before {
			t.Fatalf("rejected create changed audit count: before=%d after=%d err=%v", before, after, err)
		}
	}
	rec := userRequest(t, s, token, http.MethodPost, "/api/users", map[string]string{"username": "default-viewer", "password": "valid-password"})
	if rec.Code != http.StatusOK {
		t.Fatalf("valid create: %d %s", rec.Code, rec.Body.String())
	}
	u, err := s.store.GetUserByUsername(t.Context(), "default-viewer")
	if err != nil || u.Role != models.RoleViewer {
		t.Fatalf("default role changed: user=%+v err=%v", u, err)
	}
	loginAs(t, s, "default-viewer", "valid-password")
}

func TestUserUpdateValidationPreservesRoleAndPassword(t *testing.T) {
	s, token := newEnrollmentTestServer(t)
	u, err := s.store.GetUserByUsername(t.Context(), "enroll-admin")
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []map[string]string{
		{"role": "superadmin", "password": "changed-password"},
		{"role": models.RoleViewer, "password": strings.Repeat("p", 73)},
	} {
		rec := userRequest(t, s, token, http.MethodPut, "/api/users/"+u.ID, body)
		if rec.Code != http.StatusBadRequest {
			t.Fatalf("invalid update status=%d body=%s", rec.Code, rec.Body.String())
		}
		persisted, err := s.store.GetUserByUsername(t.Context(), u.Username)
		if err != nil || persisted.Role != u.Role || persisted.PasswordHash != u.PasswordHash {
			t.Fatalf("rejected update partially persisted: user=%+v err=%v", persisted, err)
		}
	}
}

type passwordFailureStore struct{ *store.SQLiteStore }

func (s passwordFailureStore) UpdateUserPassword(context.Context, string, string) error {
	return errors.New("injected password persistence failure")
}

func TestUserUpdateRollsBackRoleOnPersistenceFailure(t *testing.T) {
	s, token := newEnrollmentTestServer(t)
	u, err := s.store.GetUserByUsername(t.Context(), "enroll-admin")
	if err != nil {
		t.Fatal(err)
	}
	s.store = passwordFailureStore{s.store.(*store.SQLiteStore)}
	rec := userRequest(t, s, token, http.MethodPut, "/api/users/"+u.ID, map[string]string{
		"role": models.RoleViewer, "password": "changed-password",
	})
	if rec.Code != http.StatusInternalServerError {
		t.Fatalf("persistence failure status=%d body=%s", rec.Code, rec.Body.String())
	}
	persisted, err := s.store.GetUserByUsername(t.Context(), u.Username)
	if err != nil || persisted.Role != u.Role || persisted.PasswordHash != u.PasswordHash {
		t.Fatalf("failed transaction changed user: user=%+v err=%v", persisted, err)
	}
}
