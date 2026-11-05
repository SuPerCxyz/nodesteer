package monitoring

import (
	"encoding/json"
	"testing"
	"time"
)

func TestCompareHTTPExpiryAndFreshness(t *testing.T) {
	now := time.Date(2026, 1, 2, 0, 0, 0, 0, time.UTC)
	observation := HTTPObservation{ObservedAt: now, HasValue: true, Value: "2026-01-02T02:00:00+00:00"}
	matched, err := CompareHTTP(observation, now, time.Minute, "expires_within", "3h")
	if err != nil || !matched {
		t.Fatalf("matched = %v, error = %v", matched, err)
	}
	if _, err := CompareHTTP(observation, now.Add(2*time.Minute), time.Minute, "expires_within", "3h"); err == nil {
		t.Fatal("stale data must fail closed")
	}
	observation.Value = "2026-01-02T02:00:00"
	if _, err := CompareHTTP(observation, now, time.Minute, "expires_within", "3h"); err == nil {
		t.Fatal("timestamp without timezone must fail")
	}
}

func TestCompareHTTPZeroAndInvalidValues(t *testing.T) {
	now := time.Now()
	observation := HTTPObservation{ObservedAt: now, HasValue: true, Value: json.Number("0")}
	matched, err := CompareHTTP(observation, now, time.Minute, "eq", "0")
	if err != nil || !matched {
		t.Fatalf("matched = %v, error = %v", matched, err)
	}
	if _, err := CompareHTTP(observation, now, time.Minute, "gt", "NaN"); err == nil {
		t.Fatal("NaN comparison must fail")
	}
	observation.HasValue = false
	if _, err := CompareHTTP(observation, now, time.Minute, "eq", "0"); err == nil {
		t.Fatal("missing value must not equal zero")
	}
}
