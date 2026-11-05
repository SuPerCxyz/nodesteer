package monitoring

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestObserveHTTPPreservesZeroAndStatus(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-secret" {
			t.Error("missing bearer credential")
		}
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = w.Write([]byte(`{"service":{"remaining":0}}`))
	}))
	defer server.Close()
	got, err := observeHTTP(context.Background(), HTTPProbe{URL: server.URL, JSONPath: []string{"service", "remaining"}, Credential: "test-secret", Timeout: time.Second}, server.Client().Transport)
	if err != nil || got.StatusCode != http.StatusServiceUnavailable || !got.HasValue || got.Value != json.Number("0") || got.ObservedAt.IsZero() {
		t.Fatalf("observation = %+v, error = %v", got, err)
	}
}

func TestObserveHTTPMissingAndInvalidValues(t *testing.T) {
	for _, body := range []string{`{"v":null}`, `{"other":0}`, `{"v":{}}`, `{"v":1} garbage`} {
		t.Run(body, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte(body)) }))
			defer server.Close()
			got, err := ObserveHTTP(context.Background(), HTTPProbe{URL: server.URL, JSONPath: []string{"v"}, Timeout: time.Second})
			if err == nil || got.HasValue {
				t.Fatalf("observation = %+v, error = %v", got, err)
			}
		})
	}
}

func TestObserveHTTPDoesNotFollowRedirectOrLeakCredential(t *testing.T) {
	redirected := false
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { redirected = true }))
	defer target.Close()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { http.Redirect(w, r, target.URL, http.StatusFound) }))
	defer server.Close()
	got, err := ObserveHTTP(context.Background(), HTTPProbe{URL: server.URL, Timeout: time.Second})
	if err != nil || got.StatusCode != http.StatusFound || redirected {
		t.Fatalf("observation = %+v, error = %v, redirected = %v", got, err, redirected)
	}
	_, err = ObserveHTTP(context.Background(), HTTPProbe{URL: "http://user:secret@example.com", Timeout: time.Second})
	if err == nil || strings.Contains(err.Error(), "secret") {
		t.Fatalf("unexpected error: %v", err)
	}
	_, err = ObserveHTTP(context.Background(), HTTPProbe{URL: server.URL, Credential: "secret", Timeout: time.Second})
	if err == nil || strings.Contains(err.Error(), "secret") {
		t.Fatalf("credentials over HTTP should be rejected: %v", err)
	}
}

func TestObserveHTTPBoundsResponse(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(strings.Repeat("x", maxResponseBytes+1)))
	}))
	defer server.Close()
	_, err := ObserveHTTP(context.Background(), HTTPProbe{URL: server.URL, JSONPath: []string{"field"}, Timeout: time.Second})
	if err == nil || !strings.Contains(err.Error(), "too large") {
		t.Fatalf("unexpected error: %v", err)
	}
}
