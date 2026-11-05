// Package monitoring contains observation primitives for external services.
package monitoring

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const maxResponseBytes = 1 << 20

var ErrProbeUnreachable = errors.New("HTTP probe unreachable")

// HTTPProbe describes a single HTTP observation. Credential must never be logged.
type HTTPProbe struct {
	URL        string
	JSONPath   []string
	Credential string
	Timeout    time.Duration
}

// HTTPObservation keeps transport status separate from any business value.
type HTTPObservation struct {
	ObservedAt      time.Time
	ContinuityStart time.Time
	StatusCode      int
	Duration        time.Duration
	Value           any
	HasValue        bool
	Samples         []MetricSample
}

type MetricSample struct {
	ObservedAt time.Time
	Value      json.Number
	Valid      bool
}

// ObserveHTTP collects a bounded JSON response. JSONPath addresses object keys;
// empty paths observe only the HTTP status and timing.
func ObserveHTTP(ctx context.Context, probe HTTPProbe) (HTTPObservation, error) {
	return observeHTTP(ctx, probe, http.DefaultTransport)
}

func observeHTTP(ctx context.Context, probe HTTPProbe, transport http.RoundTripper) (HTTPObservation, error) {
	var result HTTPObservation
	u, err := url.Parse(probe.URL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil {
		return result, errors.New("invalid HTTP probe URL")
	}
	if probe.Credential != "" && u.Scheme != "https" {
		return result, errors.New("HTTP probe credentials require HTTPS")
	}
	if probe.Timeout <= 0 {
		return result, errors.New("HTTP probe timeout must be positive")
	}
	ctx, cancel := context.WithTimeout(ctx, probe.Timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return result, errors.New("invalid HTTP probe request")
	}
	if probe.Credential != "" {
		req.Header.Set("Authorization", "Bearer "+probe.Credential)
	}
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	started := time.Now()
	resp, err := client.Do(req)
	result.ObservedAt = time.Now().UTC()
	result.Duration = time.Since(started)
	if err != nil {
		// Transport errors can include URLs or credentials: do not return them.
		if errors.Is(err, ErrDestinationDenied) {
			return result, ErrDestinationDenied
		}
		if errors.Is(err, context.DeadlineExceeded) {
			return result, ErrProbeUnreachable
		}
		if errors.Is(err, context.Canceled) {
			return result, errors.New("HTTP probe request canceled")
		}
		return result, ErrProbeUnreachable
	}
	defer resp.Body.Close()
	result.StatusCode = resp.StatusCode
	if len(probe.JSONPath) == 0 {
		return result, nil
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if err != nil {
		return result, errors.New("HTTP probe response read failed")
	}
	if len(body) > maxResponseBytes {
		return result, errors.New("HTTP probe response too large")
	}
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return result, errors.New("HTTP probe response is not valid JSON")
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return result, errors.New("HTTP probe response contains trailing data")
	}
	for _, key := range probe.JSONPath {
		object, ok := value.(map[string]any)
		if !ok {
			return result, errors.New("HTTP probe JSON field is missing")
		}
		value, ok = object[key]
		if !ok || value == nil {
			return result, errors.New("HTTP probe JSON field is missing")
		}
	}
	switch value.(type) {
	case string, bool, json.Number:
		result.Value, result.HasValue = value, true
	default:
		return result, errors.New("HTTP probe JSON field must be a scalar")
	}
	return result, nil
}
