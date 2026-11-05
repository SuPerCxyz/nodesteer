package monitoring

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestRestrictedTransport(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) }))
	defer server.Close()
	denied, err := RestrictedTransport([]string{"192.0.2.0/24"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = observeHTTP(context.Background(), HTTPProbe{URL: server.URL, Timeout: time.Second}, denied)
	if err == nil {
		t.Fatal("unapproved loopback address was reached")
	}
	allowed, err := RestrictedTransport([]string{"127.0.0.0/8"})
	if err != nil {
		t.Fatal(err)
	}
	got, err := observeHTTP(context.Background(), HTTPProbe{URL: server.URL, Timeout: time.Second}, allowed)
	if err != nil || got.StatusCode != http.StatusNoContent {
		t.Fatalf("observation=%+v, error=%v", got, err)
	}
	if _, err := RestrictedTransport([]string{"invalid"}); err == nil {
		t.Fatal("invalid CIDR accepted")
	}
}

func TestAgentAndHubPoliciesBothRequired(t *testing.T) {
	policy, err := IntersectNetworkPolicies([]string{"127.0.0.0/8"}, []string{"192.0.2.0/24"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := policy.Observe(context.Background(), "tcp", "127.0.0.1", "80", time.Second); !errors.Is(err, ErrDestinationDenied) {
		t.Fatalf("unexpected error: %v", err)
	}
	policy, err = IntersectNetworkPolicies([]string{"127.0.0.0/8"}, []string{"127.0.0.1/32"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := policy.addresses(context.Background(), "127.0.0.2"); !errors.Is(err, ErrDestinationDenied) {
		t.Fatalf("outside intersection: %v", err)
	}
	if _, err := policy.addresses(context.Background(), "127.0.0.1"); err != nil {
		t.Fatal(err)
	}
}

func TestNetworkPolicyTCP(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	host, port, err := net.SplitHostPort(listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	denied, err := NewNetworkPolicy(nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := denied.Observe(context.Background(), "tcp", host, port, time.Second); !errors.Is(err, ErrDestinationDenied) {
		t.Fatalf("denied error = %v", err)
	}
	allowed, err := NewNetworkPolicy([]string{"127.0.0.0/8"})
	if err != nil {
		t.Fatal(err)
	}
	got, err := allowed.Observe(context.Background(), "tcp", host, port, time.Second)
	if err != nil || !got.HasValue || got.Value != true {
		t.Fatalf("observation=%+v err=%v", got, err)
	}
	listener.Close()
	if _, err := allowed.Observe(context.Background(), "tcp", host, port, time.Second); !errors.Is(err, ErrProbeUnreachable) {
		t.Fatalf("closed port error = %v", err)
	}
}

func TestNetworkPolicyICMPLoopback(t *testing.T) {
	allowed, err := NewNetworkPolicy([]string{"127.0.0.0/8"})
	if err != nil {
		t.Fatal(err)
	}
	got, err := allowed.Observe(context.Background(), "icmp", "127.0.0.1", "", 2*time.Second)
	if err != nil {
		t.Skipf("ICMP unavailable in this environment: %v", err)
	}
	if !got.HasValue || got.Value != true {
		t.Fatalf("observation=%+v", got)
	}
}

func TestSchedulerObserve(t *testing.T) {
	// Persistence and scheduler are exercised together against a local HTTP server.
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(`{"available":true}`)) }))
	defer server.Close()
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	target := Target{Name: "test", URL: server.URL, JSONPath: []string{"available"}, IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := repo.Create(context.Background(), &target); err != nil {
		t.Fatal(err)
	}
	transport, err := RestrictedTransport([]string{"127.0.0.0/8"})
	if err != nil {
		t.Fatal(err)
	}
	scheduler := NewScheduler(repo, transport, slog.New(slog.NewTextHandler(io.Discard, nil)))
	scheduler.observe(context.Background(), target)
	items, err := repo.Observations(context.Background(), target.ID, 10)
	if err != nil || len(items) != 1 || string(items[0].Value) != "true" {
		t.Fatalf("observations=%+v err=%v", items, err)
	}
}

func TestQuerySecretIsInjectedWithoutPersisting(t *testing.T) {
	t.Setenv("NODESTEER_MONITOR_SECRET_QUERY", "query-private-value")
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("api_key") != "query-private-value" || r.URL.Query().Get("region") != "north" {
			t.Error("query parameters not supplied")
		}
		_, _ = w.Write([]byte(`{"state":"query-private-value"}`))
	}))
	defer server.Close()
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	target := Target{Name: "test", URL: server.URL, JSONPath: []string{"state"}, QueryParams: []QueryParam{{Name: "api_key", SecretEnv: "NODESTEER_MONITOR_SECRET_QUERY"}, {Name: "region", Value: "north"}}, IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := repo.Create(context.Background(), &target); err != nil {
		t.Fatal(err)
	}
	scheduler := NewScheduler(repo, server.Client().Transport, slog.New(slog.NewTextHandler(io.Discard, nil)))
	scheduler.observe(context.Background(), target)
	items, err := repo.Observations(context.Background(), target.ID, 10)
	if err != nil || len(items) != 1 || string(items[0].Value) != "null" || items[0].Error != "monitoring response contains credential" {
		t.Fatalf("observations=%+v err=%v", items, err)
	}
	var config string
	if err := db.DB().QueryRow(`SELECT config_json FROM monitoring_http_targets WHERE id = ?`, target.ID).Scan(&config); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(config, "query-private-value") {
		t.Fatal("secret stored in configuration")
	}
	target.QueryParams[0] = QueryParam{Name: "api_key", Value: "plain"}
	if err := target.Validate(); err == nil {
		t.Fatal("sensitive parameter accepted as plain value")
	}
}

func TestRuleForHTTPFailureSurvivesNonJSONBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte("not json"))
	}))
	defer server.Close()
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	target := Target{Name: "test", URL: server.URL, JSONPath: []string{"status"}, IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := repo.Create(context.Background(), &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "failure", TargetID: target.ID, ScheduleID: "schedule", Operator: "status_eq", Expected: "503", Enabled: true}
	if err := repo.CreateRule(context.Background(), &rule); err != nil {
		t.Fatal(err)
	}
	transport, err := RestrictedTransport([]string{"127.0.0.0/8"})
	if err != nil {
		t.Fatal(err)
	}
	scheduler := NewScheduler(repo, transport, slog.New(slog.NewTextHandler(io.Discard, nil)))
	calls := 0
	scheduler.SetTrigger(func(_ context.Context, _ Rule, _ string) error { calls++; return nil })
	scheduler.observe(context.Background(), target)
	scheduler.actions.Wait()
	scheduler.observe(context.Background(), target)
	scheduler.actions.Wait()
	if calls != 1 {
		t.Fatalf("trigger calls = %d", calls)
	}
	events, err := repo.RuleEvents(context.Background(), rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "completed" {
		t.Fatalf("events = %+v, err = %v", events, err)
	}
}

func TestExplicitNetworkFailureRuleDoesNotTreatDeniedAddressAsOutage(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	address := server.URL
	server.Close()
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	target := Target{Name: "test", URL: address, IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := repo.Create(context.Background(), &target); err != nil {
		t.Fatal(err)
	}
	rule := Rule{Name: "unreachable", TargetID: target.ID, ScheduleID: "schedule", Operator: "probe_error", Enabled: true}
	if err := repo.CreateRule(context.Background(), &rule); err != nil {
		t.Fatal(err)
	}
	calls := 0
	trigger := func(_ context.Context, _ Rule, _ string) error { calls++; return nil }
	denied, err := RestrictedTransport(nil)
	if err != nil {
		t.Fatal(err)
	}
	scheduler := NewScheduler(repo, denied, slog.New(slog.NewTextHandler(io.Discard, nil)))
	scheduler.SetTrigger(trigger)
	scheduler.observe(context.Background(), target)
	scheduler.actions.Wait()
	if calls != 0 {
		t.Fatal("blocked address triggered outage action")
	}
	allowed, err := RestrictedTransport([]string{"127.0.0.0/8"})
	if err != nil {
		t.Fatal(err)
	}
	scheduler = NewScheduler(repo, allowed, slog.New(slog.NewTextHandler(io.Discard, nil)))
	scheduler.SetTrigger(trigger)
	scheduler.observe(context.Background(), target)
	scheduler.actions.Wait()
	if calls != 1 {
		t.Fatalf("unreachable triggered %d times", calls)
	}
}
