package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

func TestMonitoringObservationsLimitsErrorsAndDuration(t *testing.T) {
	s, st := newMonitoringAPI(t, false)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")
	handler := s.Routes()

	target := monitoring.Target{Name: "observed", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := s.monitoring.Create(t.Context(), &target); err != nil {
		t.Fatal(err)
	}
	empty := monitoring.Target{Name: "empty", URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := s.monitoring.Create(t.Context(), &empty); err != nil {
		t.Fatal(err)
	}

	base := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	for i, duration := range []time.Duration{1500 * time.Microsecond, 2500 * time.Microsecond, 3500 * time.Microsecond} {
		obs := monitoring.HTTPObservation{
			ObservedAt: base.Add(time.Duration(i) * time.Minute),
			StatusCode: 200,
			Duration:   duration,
			HasValue:   true,
			Value:      json.Number("42"),
		}
		if err := s.monitoring.AddObservation(t.Context(), target.ID, obs, nil); err != nil {
			t.Fatal(err)
		}
	}

	rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+target.ID+"/observations", admin, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("observations = %d %s", rec.Code, rec.Body.String())
	}
	items := decodeResource[[]monitoring.StoredObservation](t, rec)
	if len(items) != 3 {
		t.Fatalf("observations len = %d, want 3", len(items))
	}
	for i, item := range items {
		if item.TargetID != target.ID || item.DurationNS <= 0 || item.StatusCode != 200 {
			t.Fatalf("observation[%d] = %+v", i, item)
		}
		if i > 0 && items[i-1].ID <= item.ID {
			t.Fatalf("observations not newest-first: %+v", items)
		}
	}
	if items[0].DurationNS != int64(3500*time.Microsecond) {
		t.Fatalf("newest duration_ns = %d, want %d", items[0].DurationNS, int64(3500*time.Microsecond))
	}
	if string(items[0].Value) != "42" {
		t.Fatalf("newest value = %s, want 42", items[0].Value)
	}

	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+target.ID+"/observations?limit=1", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("limit=1 = %d %s", rec.Code, rec.Body.String())
	} else if got := decodeResource[[]monitoring.StoredObservation](t, rec); len(got) != 1 {
		t.Fatalf("limit=1 len = %d, want 1", len(got))
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+target.ID+"/observations?limit=1000", admin, ""); rec.Code != http.StatusOK {
		t.Fatalf("limit=1000 = %d %s", rec.Code, rec.Body.String())
	} else if got := decodeResource[[]monitoring.StoredObservation](t, rec); len(got) != 3 {
		t.Fatalf("limit=1000 len = %d, want 3", len(got))
	}

	for _, limit := range []string{"0", "-1", "1001", "not-a-number"} {
		if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+target.ID+"/observations?limit="+limit, admin, ""); rec.Code != http.StatusBadRequest {
			t.Fatalf("limit=%s = %d %s, want 400", limit, rec.Code, rec.Body.String())
		}
	}
	if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/missing/observations", admin, ""); rec.Code != http.StatusNotFound {
		t.Fatalf("unknown target observations = %d %s, want 404", rec.Code, rec.Body.String())
	}
	rec = doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/http-targets/"+empty.ID+"/observations", admin, "")
	if rec.Code != http.StatusOK || strings.TrimSpace(rec.Body.String()) != "[]" {
		t.Fatalf("empty observations = %d %s, want 200 []", rec.Code, rec.Body.String())
	}
}

func TestMonitoringLatestPaginationEmptyAndDuration(t *testing.T) {
	s, st := newMonitoringAPI(t, false)
	seedMonitoringUsers(t, st)
	admin := loginAs(t, s, "admin", "password")
	handler := s.Routes()

	rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/latest", admin, "")
	if rec.Code != http.StatusOK {
		t.Fatalf("empty latest = %d %s", rec.Code, rec.Body.String())
	}
	emptyPage := decodeResource[struct {
		Items []monitoring.StoredObservation `json:"items"`
		Next  string                         `json:"next"`
	}](t, rec)
	if len(emptyPage.Items) != 0 || emptyPage.Next != "" {
		t.Fatalf("empty latest = %+v", emptyPage)
	}
	if !strings.Contains(rec.Body.String(), `"items":[]`) {
		t.Fatalf("empty latest must return an empty array, got %s", rec.Body.String())
	}

	targets := make([]monitoring.Target, 0, 4)
	for _, name := range []string{"one", "two", "three", "no-observation"} {
		target := monitoring.Target{Name: name, URL: "http://example.test", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
		if err := s.monitoring.Create(t.Context(), &target); err != nil {
			t.Fatal(err)
		}
		targets = append(targets, target)
	}
	base := time.Date(2026, 11, 3, 11, 0, 0, 0, time.UTC)
	for i := 0; i < 3; i++ {
		obs := monitoring.HTTPObservation{
			ObservedAt: base.Add(time.Duration(i) * time.Minute),
			StatusCode: 200,
			Duration:   time.Duration(i+1) * time.Millisecond,
			HasValue:   true,
			Value:      json.Number("7"),
		}
		if err := s.monitoring.AddObservation(t.Context(), targets[i].ID, obs, nil); err != nil {
			t.Fatal(err)
		}
	}

	type latestPage struct {
		Items []monitoring.StoredObservation `json:"items"`
		Next  string                         `json:"next"`
	}
	decodePage := func(rec *httptest.ResponseRecorder) latestPage {
		t.Helper()
		return decodeResource[latestPage](t, rec)
	}

	first := decodePage(doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/latest?limit=2", admin, ""))
	if len(first.Items) != 2 || first.Next == "" {
		t.Fatalf("first page = %+v, want 2 items and next", first)
	}
	if first.Next != first.Items[len(first.Items)-1].TargetID {
		t.Fatalf("next = %q, want last target id %q", first.Next, first.Items[len(first.Items)-1].TargetID)
	}
	for _, item := range first.Items {
		if item.DurationNS <= 0 {
			t.Fatalf("latest item missing duration_ns: %+v", item)
		}
	}
	second := decodePage(doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/latest?limit=2&after="+first.Next, admin, ""))
	if len(second.Items) != 1 || second.Next != "" {
		t.Fatalf("second page = %+v, want 1 item and no next", second)
	}
	seen := map[string]bool{}
	for _, item := range append(first.Items, second.Items...) {
		if seen[item.TargetID] {
			t.Fatalf("duplicate target %s across pages", item.TargetID)
		}
		seen[item.TargetID] = true
	}
	for i := 0; i < 3; i++ {
		if !seen[targets[i].ID] {
			t.Fatalf("target %s missing from latest pages", targets[i].ID)
		}
	}
	if seen[targets[3].ID] {
		t.Fatalf("target without observations appeared in latest")
	}

	all := decodePage(doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/latest?limit=1000", admin, ""))
	if len(all.Items) != 3 || all.Next != "" {
		t.Fatalf("all latest = %+v, want 3 items and no next", all)
	}
	for _, limit := range []string{"0", "-1", "1001", "not-a-number"} {
		if rec := doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/latest?limit="+limit, admin, ""); rec.Code != http.StatusBadRequest {
			t.Fatalf("latest limit=%s = %d %s, want 400", limit, rec.Code, rec.Body.String())
		}
	}
	unknownAfter := decodePage(doMonitoringRequest(t, handler, http.MethodGet, "/api/monitoring/latest?after=zzzz", admin, ""))
	if len(unknownAfter.Items) != 0 || unknownAfter.Next != "" {
		t.Fatalf("unknown after = %+v, want empty page", unknownAfter)
	}
}
