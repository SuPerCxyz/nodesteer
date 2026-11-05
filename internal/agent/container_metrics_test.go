package agent

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func newDockerFixtureServer(t *testing.T, handler http.Handler) string {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "ns-sock-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	socket := filepath.Join(dir, "docker.sock")
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatalf("listen unix socket: %v", err)
	}
	server := &http.Server{Handler: handler}
	go func() {
		_ = server.Serve(listener)
	}()
	t.Cleanup(func() {
		_ = server.Close()
		_ = listener.Close()
	})
	return socket
}

func writeJSONResponse(t *testing.T, w http.ResponseWriter, value any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(value); err != nil {
		t.Errorf("encode response: %v", err)
	}
}

func TestMetricsCollectorContainerMetrics(t *testing.T) {
	statsCalls := 0
	mux := http.NewServeMux()
	mux.HandleFunc("/containers/json", func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, []dockerContainer{
			{ID: "abc123", Names: []string{"/web"}, Image: "nginx", State: "running"},
			{ID: "stopped", Names: []string{"/stopped"}, Image: "busybox", State: "exited"},
		})
	})
	mux.HandleFunc("/containers/abc123/stats", func(w http.ResponseWriter, r *http.Request) {
		statsCalls++
		if statsCalls == 1 {
			writeJSONResponse(t, w, dockerStats{
				CPUStats: dockerCPUStats{
					CPUUsage:    dockerCPUUsage{TotalUsage: 2_000_000_000, PercpuUsage: []uint64{1_000_000_000, 1_000_000_000}},
					SystemUsage: 20_000_000_000,
					OnlineCPUs:  2,
				},
				PreCPUStats: dockerCPUStats{
					CPUUsage:    dockerCPUUsage{TotalUsage: 1_000_000_000},
					SystemUsage: 10_000_000_000,
				},
				MemoryStats: dockerMemoryStats{Usage: 104_857_600, Limit: 209_715_200},
				Networks: map[string]dockerNetworkStats{
					"eth0": {RxBytes: 1000, TxBytes: 2000},
				},
			})
			return
		}
		writeJSONResponse(t, w, dockerStats{
			CPUStats: dockerCPUStats{
				CPUUsage:    dockerCPUUsage{TotalUsage: 3_000_000_000, PercpuUsage: []uint64{1_500_000_000, 1_500_000_000}},
				SystemUsage: 30_000_000_000,
				OnlineCPUs:  2,
			},
			PreCPUStats: dockerCPUStats{
				CPUUsage:    dockerCPUUsage{TotalUsage: 2_000_000_000},
				SystemUsage: 20_000_000_000,
			},
			MemoryStats: dockerMemoryStats{Usage: 104_857_600, Limit: 209_715_200},
			Networks: map[string]dockerNetworkStats{
				"eth0": {RxBytes: 3000, TxBytes: 5000},
			},
		})
	})

	collector := newMetricsCollector(t.TempDir())
	collector.containerSocketPath = newDockerFixtureServer(t, mux)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	first := collector.containerSamples(now)
	if len(first) != 5 {
		t.Fatalf("first container samples = %d, want cpu+memory: %+v", len(first), first)
	}
	cpu := findMetricSample(first, "container.cpu.usage", map[string]string{"container": "web", "container_id": "abc123"})
	if cpu == nil || cpu.Value == nil || *cpu.Value != 20 {
		t.Fatalf("container cpu = %+v, want 20", cpu)
	}
	memory := findMetricSample(first, "container.memory.usage", map[string]string{"container": "web", "container_id": "abc123"})
	if memory == nil || memory.Value == nil || *memory.Value != 50 {
		t.Fatalf("container memory = %+v, want 50", memory)
	}
	if findMetricSample(first, "container.cpu.usage", map[string]string{"container": "stopped"}) != nil {
		t.Fatal("stopped container must be excluded")
	}

	second := collector.containerSamples(now.Add(10 * time.Second))
	if len(second) != 7 {
		t.Fatalf("second container samples = %d, want 4: %+v", len(second), second)
	}
	for metric, want := range map[string]float64{
		"container.network.rx_bps": 200,
		"container.network.tx_bps": 300,
	} {
		sample := findMetricSample(second, metric, map[string]string{"container": "web", "container_id": "abc123"})
		if sample == nil || sample.Value == nil || *sample.Value != want {
			t.Fatalf("%s = %+v, want %v", metric, sample, want)
		}
	}
}

func TestMetricsCollectorContainerUnavailable(t *testing.T) {
	collector := newMetricsCollector(t.TempDir())
	collector.containerSocketPath = filepath.Join(t.TempDir(), "missing.sock")
	if got := collector.containerSamples(time.Now()); len(got) != 0 {
		t.Fatalf("missing socket samples = %+v, want none", got)
	}
	if collector.containerCapable.Load() {
		t.Fatal("missing socket must not mark container metrics available")
	}

	// 非 socket 文件（模拟权限/挂载异常）也直接跳过。
	regular := filepath.Join(t.TempDir(), "docker.sock")
	if err := os.WriteFile(regular, []byte("not a socket"), 0o600); err != nil {
		t.Fatal(err)
	}
	collector.containerSocketPath = regular
	if got := collector.containerSamples(time.Now()); len(got) != 0 {
		t.Fatalf("non-socket samples = %+v, want none", got)
	}
}

func TestMetricsCollectorContainerAPIErrors(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/containers/json", func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "permission denied", http.StatusForbidden)
	})
	collector := newMetricsCollector(t.TempDir())
	collector.containerSocketPath = newDockerFixtureServer(t, mux)
	if got := collector.containerSamples(time.Now()); len(got) != 0 {
		t.Fatalf("forbidden api samples = %+v, want none", got)
	}
}

func TestMetricsCollectorContainerEmptyList(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/containers/json", func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, []dockerContainer{})
	})
	collector := newMetricsCollector(t.TempDir())
	collector.containerSocketPath = newDockerFixtureServer(t, mux)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	if got := collector.containerSamples(now); len(got) != 3 {
		t.Fatalf("empty container list samples = %+v, want completeness triple", got)
	}
	if collector.containerNetInit {
		t.Fatal("empty container list must reset network baseline")
	}
}

func TestDockerCPUPercentSkipsFirstSample(t *testing.T) {
	if _, ok := dockerCPUPercent(dockerStats{}); ok {
		t.Fatal("empty precpu_stats must be skipped")
	}
	stats := dockerStats{
		CPUStats:    dockerCPUStats{CPUUsage: dockerCPUUsage{TotalUsage: 100}, SystemUsage: 100, OnlineCPUs: 1},
		PreCPUStats: dockerCPUStats{CPUUsage: dockerCPUUsage{TotalUsage: 100}, SystemUsage: 100},
	}
	if _, ok := dockerCPUPercent(stats); ok {
		t.Fatal("non-increasing system usage must be skipped")
	}
}

func TestDockerMemoryPercent(t *testing.T) {
	stats := dockerStats{MemoryStats: dockerMemoryStats{Usage: 100, Limit: 0}}
	if _, ok := dockerMemoryPercent(stats); ok {
		t.Fatal("zero memory limit must be skipped")
	}
	stats.MemoryStats.Limit = 1000
	stats.MemoryStats.Usage = 400
	if value, ok := dockerMemoryPercent(stats); !ok || value != 40 {
		t.Fatalf("memory percent = %v/%v, want 40", value, ok)
	}
}

func TestDockerNetworkTotals(t *testing.T) {
	if _, _, ok := dockerNetworkTotals(dockerStats{}); ok {
		t.Fatal("missing networks must be skipped")
	}
	rx, tx, ok := dockerNetworkTotals(dockerStats{Networks: map[string]dockerNetworkStats{
		"eth0": {RxBytes: 10, TxBytes: 20},
		"eth1": {RxBytes: 5, TxBytes: 6},
	}})
	if !ok || rx != 15 || tx != 26 {
		t.Fatalf("network totals = %d/%d/%v, want 15/26/true", rx, tx, ok)
	}
}

func TestContainerMetricsCapabilityProbe(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/containers/json", func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, []dockerContainer{})
	})
	socket := newDockerFixtureServer(t, mux)

	a := newTestAgent(t, Config{MetricsEnabled: true, DeploymentMode: models.DeploymentModeNative})
	a.metricsCollector.containerSocketPath = socket
	if !a.capabilities()[models.CapContainerMetrics] {
		t.Fatal("agent with reachable Docker socket must declare container_metrics")
	}
	hello := a.helloFn()
	if !hello.Capabilities[models.CapContainerMetrics] {
		t.Fatal("HELLO must include container_metrics capability")
	}
}

func TestContainerMetricsCapabilityUnavailable(t *testing.T) {
	a := newTestAgent(t, Config{MetricsEnabled: true, DeploymentMode: models.DeploymentModeNative})
	a.metricsCollector.containerSocketPath = filepath.Join(t.TempDir(), "missing.sock")
	if a.capabilities()[models.CapContainerMetrics] {
		t.Fatal("missing Docker socket must not declare container_metrics")
	}
	if a.helloFn().Capabilities[models.CapContainerMetrics] {
		t.Fatal("HELLO must not declare container_metrics without socket")
	}

	// 非 socket 文件同样视为不可用。
	regular := filepath.Join(t.TempDir(), "docker.sock")
	if err := os.WriteFile(regular, []byte("not a socket"), 0o600); err != nil {
		t.Fatal(err)
	}
	a.metricsCollector.containerSocketPath = regular
	if a.capabilities()[models.CapContainerMetrics] {
		t.Fatal("regular file must not be treated as Docker socket")
	}

	// 指标整体关闭时不探测、不声明容器能力。
	disabled := newTestAgent(t, Config{MetricsEnabled: false, DeploymentMode: models.DeploymentModeNative})
	if disabled.capabilities()[models.CapContainerMetrics] {
		t.Fatal("metrics disabled agent must not declare container_metrics")
	}
}

func TestContainerMetricsCapAfterCollect(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/containers/json", func(w http.ResponseWriter, r *http.Request) {
		writeJSONResponse(t, w, []dockerContainer{})
	})
	collector := newMetricsCollector(t.TempDir())
	collector.containerSocketPath = newDockerFixtureServer(t, mux)
	if collector.containerCapable.Load() {
		t.Fatal("capability flag must be false before collection")
	}
	if got := collector.containerSamples(time.Now()); len(got) != 3 {
		t.Fatalf("empty container list samples = %+v, want completeness triple", got)
	}
	if !collector.containerMetricsAvailable() {
		t.Fatal("successful collection must mark container metrics available")
	}
}

func TestContainerMetricsCapabilityAPIError(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/containers/json", func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "permission denied", http.StatusForbidden)
	})
	collector := newMetricsCollector(t.TempDir())
	collector.containerSocketPath = newDockerFixtureServer(t, mux)
	if collector.containerMetricsAvailable() {
		t.Fatal("Docker API error must not declare container_metrics")
	}
}

func TestContainerCollectionCompletenessAndIdentity(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/containers/json", func(w http.ResponseWriter, r *http.Request) {
		if got := r.URL.Query().Get("limit"); got != "101" {
			t.Errorf("limit = %q", got)
		}
		containers := make([]dockerContainer, 150)
		for i := range containers {
			containers[i] = dockerContainer{ID: fmt.Sprintf("id%03d", i), Names: []string{"/same"}, State: "running"}
		}
		writeJSONResponse(t, w, containers)
	})
	mux.HandleFunc("/containers/", func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "id001") {
			http.Error(w, "failed", 500)
			return
		}
		writeJSONResponse(t, w, dockerStats{MemoryStats: dockerMemoryStats{Usage: 1, Limit: 2}})
	})
	c := newMetricsCollector(t.TempDir())
	c.containerSocketPath = newDockerFixtureServer(t, mux)
	now := time.Now().UTC()
	samples := c.containerSamples(now)
	for metric, want := range map[string]float64{"container.collection.discovered_count": 101, "container.collection.sampled_count": 99, "container.collection.limit": 100} {
		sample := findMetricSample(samples, metric, nil)
		if sample == nil || sample.Value == nil || *sample.Value != want || !sample.Timestamp.Equal(now) {
			t.Fatalf("%s = %+v", metric, sample)
		}
	}
	if findMetricSample(samples, "container.memory.usage", map[string]string{"container": "same", "container_id": "id000"}) == nil || findMetricSample(samples, "container.memory.usage", map[string]string{"container": "same", "container_id": "id002"}) == nil {
		t.Fatal("same-name containers must retain distinct IDs")
	}
}

func TestContainerCollectionListFailureInvalidatesPreviousCompleteness(t *testing.T) {
	fail := false
	mux := http.NewServeMux()
	mux.HandleFunc("/containers/json", func(w http.ResponseWriter, r *http.Request) {
		if fail {
			http.Error(w, "unavailable", http.StatusInternalServerError)
			return
		}
		writeJSONResponse(t, w, []dockerContainer{})
	})
	c := newMetricsCollector(t.TempDir())
	c.containerSocketPath = newDockerFixtureServer(t, mux)
	now := time.Now().UTC()
	if got := c.containerSamples(now); len(got) != 3 {
		t.Fatalf("initial samples: %+v", got)
	}
	fail = true
	got := c.containerSamples(now.Add(time.Second))
	if len(got) != 3 {
		t.Fatalf("failed list samples: %+v", got)
	}
	for _, sample := range got {
		if sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
			t.Fatalf("must invalidate: %+v", sample)
		}
	}
}
