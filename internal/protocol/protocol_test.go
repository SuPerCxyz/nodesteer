package protocol

import (
	"bytes"
	"encoding/json"
	"testing"
	"time"
)

func TestNodeMetricsPayloadRoundTrip(t *testing.T) {
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	value := 12.5
	payload := NodeMetricsPayload{
		NodeID:   "node-1",
		Sequence: 7,
		Samples: []NodeMetricSample{
			{
				Timestamp:  now,
				Metric:     "cpu.usage",
				Dimensions: map[string]string{"cpu": "all"},
				Value:      &value,
				Quality:    NodeMetricQualityValid,
			},
			{
				Timestamp: now,
				Metric:    "load.1",
				Value:     nil,
				Quality:   NodeMetricQualityMissing,
			},
		},
	}

	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if !bytes.Contains(data, []byte(`"value":null`)) {
		t.Fatalf("missing value must serialize as null: %s", data)
	}

	var got NodeMetricsPayload
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.NodeID != "node-1" || got.Sequence != 7 || len(got.Samples) != 2 {
		t.Fatalf("payload = %+v", got)
	}
	if got.Samples[0].Metric != "cpu.usage" || got.Samples[0].Dimensions["cpu"] != "all" {
		t.Fatalf("valid sample = %+v", got.Samples[0])
	}
	if got.Samples[0].Value == nil || *got.Samples[0].Value != 12.5 || got.Samples[0].Quality != 1 {
		t.Fatalf("valid sample value = %+v", got.Samples[0])
	}
	if !got.Samples[0].Timestamp.Equal(now) {
		t.Fatalf("timestamp = %v, want %v", got.Samples[0].Timestamp, now)
	}
	if got.Samples[1].Value != nil || got.Samples[1].Quality != 2 {
		t.Fatalf("missing sample = %+v", got.Samples[1])
	}
}

func TestNodeMetricsZeroValueIsSerialized(t *testing.T) {
	zero := 0.0
	sample := NodeMetricSample{
		Timestamp: time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC),
		Metric:    "load.1",
		Value:     &zero,
		Quality:   NodeMetricQualityValid,
	}
	data, err := json.Marshal(sample)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if !bytes.Contains(data, []byte(`"value":0`)) {
		t.Fatalf("valid zero value must not be omitted: %s", data)
	}
}

func TestNodeMetricsEnvelope(t *testing.T) {
	if MsgNodeMetrics != "NODE_METRICS" {
		t.Fatalf("MsgNodeMetrics = %q", MsgNodeMetrics)
	}
	payload := NodeMetricsPayload{NodeID: "node-1", Sequence: 1}
	env := NewEnvelope(MsgNodeMetrics, "", payload)
	if env.Type != MsgNodeMetrics {
		t.Fatalf("envelope type = %q", env.Type)
	}
	var got NodeMetricsPayload
	if err := json.Unmarshal(env.Payload, &got); err != nil {
		t.Fatalf("unmarshal envelope payload: %v", err)
	}
	if got.NodeID != "node-1" || got.Sequence != 1 {
		t.Fatalf("envelope payload = %+v", got)
	}
}
