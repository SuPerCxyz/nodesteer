package agent

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func TestNormalizeMetricsInterval(t *testing.T) {
	cases := []struct {
		in   time.Duration
		want time.Duration
	}{
		{0, 30 * time.Second},
		{-time.Second, 30 * time.Second},
		{time.Second, 5 * time.Second},
		{4 * time.Second, 5 * time.Second},
		{5 * time.Second, 5 * time.Second},
		{30 * time.Second, 30 * time.Second},
		{300 * time.Second, 300 * time.Second},
		{301 * time.Second, 300 * time.Second},
		{time.Hour, 300 * time.Second},
	}
	for _, tc := range cases {
		if got := normalizeMetricsInterval(tc.in); got != tc.want {
			t.Fatalf("normalizeMetricsInterval(%v) = %v, want %v", tc.in, got, tc.want)
		}
	}
}

func TestMetricsSupportedByDeploymentMode(t *testing.T) {
	cases := []struct {
		mode            string
		hostIntegration bool
		want            bool
	}{
		{models.DeploymentModeNative, false, true},
		{"", false, true},
		{models.DeploymentModeDocker, false, false},
		{models.DeploymentModeDocker, true, true},
		{models.DeploymentModeDockerHostInt, false, true},
	}
	for _, tc := range cases {
		if got := metricsSupported(tc.mode, tc.hostIntegration); got != tc.want {
			t.Fatalf("metricsSupported(%q, %v) = %v, want %v", tc.mode, tc.hostIntegration, got, tc.want)
		}
	}
}

func TestNewInitializesMetricsCollector(t *testing.T) {
	a := newTestAgent(t, Config{MetricsEnabled: true, MetricsInterval: time.Second})
	if a.metricsCollector == nil || a.metricsQueue == nil {
		t.Fatal("metrics enabled agent must initialize collector and queue")
	}
	if a.cfg.MetricsInterval != 5*time.Second {
		t.Fatalf("metrics interval = %v, want clamped 5s", a.cfg.MetricsInterval)
	}
	if !a.capabilities()[models.CapNodeMetrics] {
		t.Fatal("metrics enabled agent must declare node_metrics")
	}
}

func TestNewDisablesMetricsInPlainDocker(t *testing.T) {
	a := newTestAgent(t, Config{MetricsEnabled: true, DeploymentMode: models.DeploymentModeDocker})
	if a.metricsCollector != nil || a.metricsQueue != nil {
		t.Fatal("plain docker agent must not read container /proc as node metrics")
	}
	if a.capabilities()[models.CapNodeMetrics] {
		t.Fatal("plain docker agent must not declare node_metrics")
	}
}

func TestMetricsRootForHostIntegration(t *testing.T) {
	if got := metricsRoot(Config{}); got != "" {
		t.Fatalf("native metrics root = %q, want empty", got)
	}
	if got := metricsRoot(Config{HostIntegration: true, HostRoot: "/host"}); got != "/host" {
		t.Fatalf("host integration root = %q, want /host", got)
	}
	if got := metricsRoot(Config{DeploymentMode: models.DeploymentModeDockerHostInt}); got != "/host" {
		t.Fatalf("default host integration root = %q, want /host", got)
	}
}

func TestCapabilitiesDeclareNodeMetrics(t *testing.T) {
	if models.CapNodeMetrics != "node_metrics" {
		t.Fatalf("CapNodeMetrics = %q, want node_metrics", models.CapNodeMetrics)
	}
	native := &Agent{cfg: Config{MetricsEnabled: true, DeploymentMode: models.DeploymentModeNative}}
	if !native.capabilities()[models.CapNodeMetrics] {
		t.Fatal("native agent with metrics enabled must declare node_metrics")
	}

	disabled := &Agent{cfg: Config{MetricsEnabled: false, DeploymentMode: models.DeploymentModeNative}}
	if disabled.capabilities()[models.CapNodeMetrics] {
		t.Fatal("metrics disabled must not declare node_metrics")
	}

	docker := &Agent{cfg: Config{MetricsEnabled: true, DeploymentMode: models.DeploymentModeDocker}}
	if docker.capabilities()[models.CapNodeMetrics] {
		t.Fatal("docker agent without host integration must not declare node_metrics")
	}

	hostIntegration := &Agent{cfg: Config{
		MetricsEnabled:  true,
		DeploymentMode:  models.DeploymentModeDockerHostInt,
		HostIntegration: true,
	}}
	if !hostIntegration.capabilities()[models.CapNodeMetrics] {
		t.Fatal("docker host integration must declare node_metrics")
	}
}

func TestSplitMetricsBatches(t *testing.T) {
	samples := make([]protocol.NodeMetricSample, 1201)
	batches := splitMetricsBatches(samples, 500)
	if len(batches) != 3 {
		t.Fatalf("batches = %d, want 3", len(batches))
	}
	for i, want := range []int{500, 500, 201} {
		if len(batches[i]) != want {
			t.Fatalf("batch %d size = %d, want %d", i, len(batches[i]), want)
		}
	}
	if got := splitMetricsBatches(nil, 500); got != nil {
		t.Fatalf("empty samples must produce no batches, got %d", len(got))
	}
}

func TestMetricsSequenceIncrements(t *testing.T) {
	a := &Agent{}
	for want := uint64(1); want <= 3; want++ {
		if got := a.nextMetricsSequence(); got != want {
			t.Fatalf("sequence = %d, want %d", got, want)
		}
	}
}

func TestMetricsQueueBuffersAndFlushesInOrder(t *testing.T) {
	connected := false
	var sent []protocol.NodeMetricsPayload
	queue := newMetricsQueue(func(env protocol.Envelope) bool {
		if !connected {
			return false
		}
		if env.Type != protocol.MsgNodeMetrics {
			t.Fatalf("message type = %q, want %q", env.Type, protocol.MsgNodeMetrics)
		}
		var payload protocol.NodeMetricsPayload
		if err := json.Unmarshal(env.Payload, &payload); err != nil {
			t.Fatalf("decode payload: %v", err)
		}
		sent = append(sent, payload)
		return true
	})

	for i := uint64(1); i <= 3; i++ {
		if dropped := queue.enqueue(protocol.NodeMetricsPayload{NodeID: "node-1", Sequence: i}); dropped != 0 {
			t.Fatalf("unexpected drop: %d", dropped)
		}
	}
	if queue.pending() != 3 {
		t.Fatalf("pending = %d, want 3", queue.pending())
	}

	connected = true
	if got := queue.flush(); got != 3 {
		t.Fatalf("flushed = %d, want 3", got)
	}
	if queue.pending() != 0 {
		t.Fatalf("pending after flush = %d, want 0", queue.pending())
	}
	for i, payload := range sent {
		if payload.Sequence != uint64(i+1) {
			t.Fatalf("sent[%d] sequence = %d, want %d", i, payload.Sequence, i+1)
		}
	}
}

func TestMetricsQueueDropsOldestAtCapacity(t *testing.T) {
	queue := newMetricsQueue(func(protocol.Envelope) bool { return false })
	totalDropped := 0
	for i := uint64(1); i <= metricsMaxBufferedBatches+2; i++ {
		totalDropped += queue.enqueue(protocol.NodeMetricsPayload{NodeID: "node-1", Sequence: i})
	}
	if totalDropped != 2 {
		t.Fatalf("dropped = %d, want 2", totalDropped)
	}
	if queue.pending() != metricsMaxBufferedBatches {
		t.Fatalf("pending = %d, want %d", queue.pending(), metricsMaxBufferedBatches)
	}

	var sequences []uint64
	queue.send = func(env protocol.Envelope) bool {
		var payload protocol.NodeMetricsPayload
		if err := json.Unmarshal(env.Payload, &payload); err != nil {
			t.Fatalf("decode payload: %v", err)
		}
		sequences = append(sequences, payload.Sequence)
		return true
	}
	queue.flush()
	if len(sequences) != metricsMaxBufferedBatches {
		t.Fatalf("sent = %d, want %d", len(sequences), metricsMaxBufferedBatches)
	}
	if sequences[0] != 3 || sequences[len(sequences)-1] != uint64(metricsMaxBufferedBatches+2) {
		t.Fatalf("sequences = %v, want oldest dropped and order preserved", sequences)
	}
}

func TestCollectAndReportMetricsBuffersUntilReconnect(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
	writeProcFixture(t, root, "loadavg", "0.1 0.2 0.3 1/100 1\n")

	connected := false
	var sent []protocol.NodeMetricsPayload
	queue := newMetricsQueue(func(env protocol.Envelope) bool {
		if !connected {
			return false
		}
		var payload protocol.NodeMetricsPayload
		if err := json.Unmarshal(env.Payload, &payload); err != nil {
			t.Fatalf("decode payload: %v", err)
		}
		sent = append(sent, payload)
		return true
	})
	a := &Agent{
		cfg:              Config{MetricsEnabled: true, MetricsInterval: 30 * time.Second},
		nodeID:           "node-1",
		metricsCollector: newMetricsCollector(root),
		metricsQueue:     queue,
	}

	a.collectAndReportMetrics()
	a.collectAndReportMetrics()
	if queue.pending() != 2 {
		t.Fatalf("pending = %d, want 2 buffered batches", queue.pending())
	}

	connected = true
	a.flushMetrics()
	if queue.pending() != 0 {
		t.Fatalf("pending after reconnect = %d, want 0", queue.pending())
	}
	if len(sent) != 2 {
		t.Fatalf("sent = %d, want 2", len(sent))
	}
	for i, payload := range sent {
		if payload.NodeID != "node-1" {
			t.Fatalf("payload node = %q, want node-1", payload.NodeID)
		}
		if payload.Sequence != uint64(i+1) {
			t.Fatalf("payload sequence = %d, want %d", payload.Sequence, i+1)
		}
		if len(payload.Samples) == 0 {
			t.Fatalf("payload %d has no samples", i)
		}
	}
}

func TestCollectAndReportMetricsWithoutNodeIDDoesNotBuffer(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\nMemAvailable: 250 kB\n")
	queue := newMetricsQueue(func(protocol.Envelope) bool { return false })
	a := &Agent{
		cfg:              Config{MetricsEnabled: true},
		metricsCollector: newMetricsCollector(root),
		metricsQueue:     queue,
	}
	a.collectAndReportMetrics()
	if queue.pending() != 0 {
		t.Fatalf("pending = %d, want 0 before node_id is known", queue.pending())
	}
}

func TestCompletenessTripleStaysInSameBatch(t *testing.T) {
	samples := make([]protocol.NodeMetricSample, 499)
	now := time.Now().UTC()
	samples = append(samples, containerCollectionSamples(now, 101, 100)...)
	batches := splitMetricsBatches(samples, 500)
	if len(batches) != 2 || len(batches[0]) != 499 || len(batches[1]) != 3 {
		t.Fatalf("batches = %d: %d/%d", len(batches), len(batches[0]), len(batches[1]))
	}
	for _, sample := range batches[1] {
		if !sample.Timestamp.Equal(now) {
			t.Fatal("unaligned timestamp")
		}
	}
}

func TestContainerSamplesAndCompletenessShareBatch(t *testing.T) {
	samples := make([]protocol.NodeMetricSample, 450)
	for i := 0; i < 400; i++ {
		samples = append(samples, protocol.NodeMetricSample{Metric: "container.cpu.usage"})
	}
	samples = append(samples, containerCollectionSamples(time.Now(), 100, 100)...)
	batches := splitMetricsBatches(samples, 500)
	if len(batches) != 2 || len(batches[0]) != 450 || len(batches[1]) != 403 {
		t.Fatalf("unexpected batch sizes: %+v", []int{len(batches[0]), len(batches[1])})
	}
}
