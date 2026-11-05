package agent

import (
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func writeProcFixture(t *testing.T, root, name, content string) {
	t.Helper()
	path := filepath.Join(root, "proc", name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir fixture: %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write fixture %s: %v", name, err)
	}
}

func findMetricSample(samples []protocol.NodeMetricSample, metric string, dimensions map[string]string) *protocol.NodeMetricSample {
	for i := range samples {
		if samples[i].Metric != metric {
			continue
		}
		match := true
		for key, want := range dimensions {
			if samples[i].Dimensions[key] != want {
				match = false
				break
			}
		}
		if match {
			return &samples[i]
		}
	}
	return nil
}

func TestMetricsCollectorCPUUsage(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "stat", `cpu  300 0 300 1400 0 0 0 0
cpu0 100 0 100 800 0 0 0 0
cpu1 200 0 200 600 0 0 0 0
`)
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	if got := collector.cpuSamples(now); len(got) != 0 {
		t.Fatalf("first CPU sample must be skipped without a delta, got %d samples", len(got))
	}

	writeProcFixture(t, root, "stat", `cpu  600 0 600 2800 0 0 0 0
cpu0 200 0 200 1600 0 0 0 0
cpu1 400 0 400 1200 0 0 0 0
`)
	samples := collector.cpuSamples(now.Add(30 * time.Second))
	if len(samples) != 27 {
		t.Fatalf("cpu samples = %d, want 27 (3 cpus x usage + 8 composition)", len(samples))
	}
	for _, tc := range []struct {
		cpu  string
		want float64
	}{
		{"all", 30},
		{"0", 20},
		{"1", 40},
	} {
		sample := findMetricSample(samples, "cpu.usage", map[string]string{"cpu": tc.cpu})
		if sample == nil {
			t.Fatalf("missing cpu.usage cpu=%s", tc.cpu)
		}
		if sample.Value == nil || *sample.Value != tc.want {
			t.Fatalf("cpu=%s usage = %v, want %v", tc.cpu, sample.Value, tc.want)
		}
		if sample.Quality != protocol.NodeMetricQualityValid {
			t.Fatalf("cpu=%s quality = %d, want valid", tc.cpu, sample.Quality)
		}
	}
	for metric, want := range map[string]float64{
		"cpu.user": 15, "cpu.system": 15, "cpu.idle": 70,
	} {
		sample := findMetricSample(samples, metric, map[string]string{"cpu": "all"})
		if sample == nil || sample.Value == nil || *sample.Value != want {
			t.Fatalf("all %s = %+v, want %v", metric, sample, want)
		}
	}
}

func TestMetricsCollectorCPUResetSkipsInterval(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "stat", "cpu 100 0 100 800 0 0 0 0\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	collector.cpuSamples(now)

	// 计数器回退（重启/重置）时不得产出负值或伪造 0。
	writeProcFixture(t, root, "stat", "cpu 10 0 10 80 0 0 0 0\n")
	if got := collector.cpuSamples(now.Add(30 * time.Second)); len(got) != 0 {
		t.Fatalf("reset interval must be skipped, got %d samples", len(got))
	}

	writeProcFixture(t, root, "stat", "cpu 20 0 20 160 0 0 0 0\n")
	got := collector.cpuSamples(now.Add(60 * time.Second))
	if len(got) != 9 {
		t.Fatalf("baseline after reset should recover, got %d samples", len(got))
	}
	if sample := findMetricSample(got, "cpu.usage", map[string]string{"cpu": "all"}); sample == nil || sample.Value == nil {
		t.Fatalf("recovered cpu.usage = %+v", sample)
	}
}

func TestMetricsCollectorCPUComposition(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "stat", `cpu  100 10 50 1000 100 5 5 10 0 0
cpu0 10 0 5 100 10 1 1 2 0 0
`)
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	collector.cpuSamples(now)

	writeProcFixture(t, root, "stat", `cpu  200 20 100 1500 150 10 10 20 0 0
cpu0 20 0 10 200 20 2 2 4 0 0
`)
	samples := collector.cpuSamples(now.Add(10 * time.Second))

	usage := findMetricSample(samples, "cpu.usage", map[string]string{"cpu": "all"})
	if usage == nil || usage.Value == nil {
		t.Fatalf("missing cpu.usage: %+v", samples)
	}
	want := map[string]float64{
		"cpu.user": 13.70, "cpu.nice": 1.37, "cpu.system": 6.85,
		"cpu.idle": 68.49, "cpu.iowait": 6.85, "cpu.irq": 0.68,
		"cpu.softirq": 0.68, "cpu.steal": 1.37,
	}
	nonIdleSum := 0.0
	for metric, expected := range want {
		sample := findMetricSample(samples, metric, map[string]string{"cpu": "all"})
		if sample == nil || sample.Value == nil {
			t.Fatalf("missing %s: %+v", metric, samples)
		}
		if math.Abs(*sample.Value-expected) > 0.02 {
			t.Fatalf("%s = %v, want %v", metric, *sample.Value, expected)
		}
		if metric != "cpu.idle" {
			nonIdleSum += *sample.Value
		}
	}
	if math.Abs(*usage.Value-nonIdleSum) > 0.05 {
		t.Fatalf("cpu.usage = %v, non-idle composition sum = %v", *usage.Value, nonIdleSum)
	}
	if math.Abs(*usage.Value-(100-want["cpu.idle"])) > 0.02 {
		t.Fatalf("cpu.usage = %v, want 100-idle = %v", *usage.Value, 100-want["cpu.idle"])
	}
	if sample := findMetricSample(samples, "cpu.iowait", map[string]string{"cpu": "0"}); sample == nil || sample.Value == nil {
		t.Fatalf("per-core iowait = %+v", sample)
	}
}

func TestMetricsCollectorCPUCompositionCoreLimit(t *testing.T) {
	root := t.TempDir()
	var first, second strings.Builder
	for i := 0; i < maxCPUMetricCores+4; i++ {
		fmt.Fprintf(&first, "cpu%d 100 0 100 800 0 0 0 0\n", i)
		fmt.Fprintf(&second, "cpu%d 200 0 200 1600 0 0 0 0\n", i)
	}
	writeProcFixture(t, root, "stat", first.String())
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	collector.cpuSamples(now)
	writeProcFixture(t, root, "stat", second.String())
	samples := collector.cpuSamples(now.Add(10 * time.Second))

	last := strconv.Itoa(maxCPUMetricCores + 3)
	if sample := findMetricSample(samples, "cpu.usage", map[string]string{"cpu": last}); sample == nil {
		t.Fatalf("cpu.usage for core %s must still be reported", last)
	}
	if sample := findMetricSample(samples, "cpu.user", map[string]string{"cpu": last}); sample != nil {
		t.Fatalf("composition for core %s must be limited, got %+v", last, sample)
	}
	firstLimit := strconv.Itoa(maxCPUMetricCores - 1)
	if sample := findMetricSample(samples, "cpu.user", map[string]string{"cpu": firstLimit}); sample == nil {
		t.Fatalf("composition for core %s must be reported", firstLimit)
	}
}

func TestMetricsCollectorMemoryUsage(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\nMemAvailable: 250 kB\nCached: 100 kB\nBuffers: 50 kB\nSwapTotal: 2000 kB\nSwapFree: 500 kB\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	samples := collector.memorySamples(now)
	if len(samples) != 11 {
		t.Fatalf("memory samples = %d, want 11: %+v", len(samples), samples)
	}
	for metric, want := range map[string]float64{
		"memory.usage":            75,
		"memory.total_bytes":      1000 * 1024,
		"memory.used_bytes":       750 * 1024,
		"memory.available_bytes":  250 * 1024,
		"memory.cached_bytes":     100 * 1024,
		"memory.buffers_bytes":    50 * 1024,
		"memory.swap_total_bytes": 2000 * 1024,
		"memory.swap_used_bytes":  1500 * 1024,
		"memory.swap_free_bytes":  500 * 1024,
		"memory.swap_usage":       75,
	} {
		sample := findMetricSample(samples, metric, nil)
		if sample == nil || sample.Value == nil || *sample.Value != want {
			t.Fatalf("%s = %+v, want %v", metric, sample, want)
		}
		if sample.Quality != protocol.NodeMetricQualityValid {
			t.Fatalf("%s quality = %d, want valid", metric, sample.Quality)
		}
	}

	if sample := findMetricSample(samples, "memory.free_bytes", nil); sample == nil || sample.Value != nil {
		t.Fatalf("missing MemFree = %+v", sample)
	}

	// MemAvailable 缺失时回退 MemFree+Buffers+Cached 计算 usage/used，
	// 但 available_bytes 仍按缺失上报。
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 100 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
	samples = collector.memorySamples(now)
	if sample := findMetricSample(samples, "memory.free_bytes", nil); sample == nil || sample.Value == nil || *sample.Value != 100*1024 {
		t.Fatalf("MemFree = %+v", sample)
	}
	if sample := findMetricSample(samples, "memory.usage", nil); sample == nil || sample.Value == nil || *sample.Value != 75 {
		t.Fatalf("memory fallback usage = %+v, want 75", sample)
	}
	if sample := findMetricSample(samples, "memory.used_bytes", nil); sample == nil || sample.Value == nil || *sample.Value != 750*1024 {
		t.Fatalf("memory fallback used = %+v, want %v", sample, 750*1024)
	}
	if sample := findMetricSample(samples, "memory.available_bytes", nil); sample == nil || sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
		t.Fatalf("memory fallback available = %+v, want missing", sample)
	}

	// MemAvailable 大于 MemTotal 时按 0 使用率处理。
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\nMemAvailable: 2000 kB\nCached: 100 kB\nBuffers: 50 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
	samples = collector.memorySamples(now)
	if sample := findMetricSample(samples, "memory.usage", nil); sample == nil || sample.Value == nil || *sample.Value != 0 {
		t.Fatalf("clamped memory usage = %+v, want 0", sample)
	}
	if sample := findMetricSample(samples, "memory.available_bytes", nil); sample == nil || sample.Value == nil || *sample.Value != 1000*1024 {
		t.Fatalf("clamped memory available = %+v, want %v", sample, 1000*1024)
	}
	if sample := findMetricSample(samples, "memory.used_bytes", nil); sample == nil || sample.Value == nil || *sample.Value != 0 {
		t.Fatalf("clamped memory used = %+v, want 0", sample)
	}
}

func TestMetricsCollectorMemoryMissing(t *testing.T) {
	root := t.TempDir()
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	assertAllMissing := func(samples []protocol.NodeMetricSample) {
		t.Helper()
		if len(samples) != 11 {
			t.Fatalf("missing memory samples = %d, want 11", len(samples))
		}
		for _, sample := range samples {
			if sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
				t.Fatalf("missing memory sample = %+v, want nil value quality 2", sample)
			}
		}
	}

	assertAllMissing(collector.memorySamples(now))

	writeProcFixture(t, root, "meminfo", "MemFree: 100 kB\n")
	freeOnly := collector.memorySamples(now)
	if sample := findMetricSample(freeOnly, "memory.free_bytes", nil); sample == nil || sample.Value == nil || *sample.Value != 100*1024 {
		t.Fatalf("MemFree without MemTotal = %+v", sample)
	}

	// 只有 MemTotal 时总量仍有效，其余依赖字段按缺失上报。
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\n")
	samples := collector.memorySamples(now)
	if sample := findMetricSample(samples, "memory.total_bytes", nil); sample == nil || sample.Value == nil || *sample.Value != 1000*1024 {
		t.Fatalf("total_bytes with only MemTotal = %+v", sample)
	}
	for _, metric := range []string{"memory.usage", "memory.used_bytes", "memory.available_bytes", "memory.cached_bytes", "memory.buffers_bytes",
		"memory.swap_total_bytes", "memory.swap_used_bytes", "memory.swap_free_bytes", "memory.swap_usage"} {
		if sample := findMetricSample(samples, metric, nil); sample == nil || sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
			t.Fatalf("%s = %+v, want missing", metric, sample)
		}
	}
}

func TestMetricsCollectorSwap(t *testing.T) {
	root := t.TempDir()
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	// 未启用 Swap 时是真实 0，不是 missing。
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\nMemAvailable: 500 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n")
	samples := collector.memorySamples(now)
	for _, metric := range []string{"memory.swap_total_bytes", "memory.swap_used_bytes", "memory.swap_free_bytes", "memory.swap_usage"} {
		sample := findMetricSample(samples, metric, nil)
		if sample == nil || sample.Value == nil || *sample.Value != 0 || sample.Quality != protocol.NodeMetricQualityValid {
			t.Fatalf("%s = %+v, want valid 0", metric, sample)
		}
	}

	// SwapFree 缺失时只保留可确认的 total，其余按 missing。
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\nMemAvailable: 500 kB\nSwapTotal: 1000 kB\n")
	samples = collector.memorySamples(now)
	if sample := findMetricSample(samples, "memory.swap_total_bytes", nil); sample == nil || sample.Value == nil || *sample.Value != 1000*1024 {
		t.Fatalf("swap total = %+v", sample)
	}
	for _, metric := range []string{"memory.swap_used_bytes", "memory.swap_free_bytes", "memory.swap_usage"} {
		if sample := findMetricSample(samples, metric, nil); sample == nil || sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
			t.Fatalf("%s = %+v, want missing", metric, sample)
		}
	}

	// SwapTotal 缺失时整个 Swap 组按 missing。
	writeProcFixture(t, root, "meminfo", "MemTotal: 1000 kB\nMemAvailable: 500 kB\n")
	samples = collector.memorySamples(now)
	for _, metric := range []string{"memory.swap_total_bytes", "memory.swap_used_bytes", "memory.swap_free_bytes", "memory.swap_usage"} {
		if sample := findMetricSample(samples, metric, nil); sample == nil || sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
			t.Fatalf("%s = %+v, want missing", metric, sample)
		}
	}
}

func TestMetricsCollectorLoad(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "loadavg", "0.50 1.25 2.75 1/100 12345\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	samples := collector.loadSamples(now)
	for _, tc := range []struct {
		metric string
		want   float64
	}{{"load.1", 0.5}, {"load.5", 1.25}, {"load.15", 2.75}} {
		sample := findMetricSample(samples, tc.metric, nil)
		if sample == nil || sample.Value == nil || *sample.Value != tc.want {
			t.Fatalf("%s = %+v, want %v", tc.metric, sample, tc.want)
		}
	}
}

func TestMetricsCollectorLoadMissingAndInvalid(t *testing.T) {
	root := t.TempDir()
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	samples := collector.loadSamples(now)
	if len(samples) != 3 {
		t.Fatalf("missing loadavg samples = %d, want 3", len(samples))
	}
	for _, sample := range samples {
		if sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
			t.Fatalf("missing load sample = %+v", sample)
		}
	}

	writeProcFixture(t, root, "loadavg", "not-a-number 1.0 2.0\n")
	samples = collector.loadSamples(now)
	if len(samples) != 3 {
		t.Fatalf("invalid loadavg samples = %d, want 3", len(samples))
	}
	if samples[0].Value != nil || samples[0].Quality != protocol.NodeMetricQualityMissing {
		t.Fatalf("invalid load.1 = %+v, want missing", samples[0])
	}
	if samples[1].Value == nil || *samples[1].Value != 1 {
		t.Fatalf("valid load.5 = %+v, want 1", samples[1])
	}
}

func TestMetricsCollectorDiskUsage(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "mounts", `/dev/sda1 / ext4 rw,relatime 0 0
proc /proc proc rw 0 0
tmpfs /run tmpfs rw 0 0
/dev/sdb1 /data xfs rw 0 0
/dev/sdc1 /boot ext4 ro 0 0
overlay /var/lib/docker/overlay2/abc overlay rw 0 0
/dev/sdd1 /mnt/my\040disk ext4 rw 0 0
/dev/sde1 /broken ext4 rw 0 0
`)
	collector := newMetricsCollector(root)
	collector.statfsFn = func(path string, st *statfsT) error {
		switch {
		case path == root:
			st.Blocks, st.Bfree, st.Bavail, st.Bsize = 1000, 150, 100, 4096
		case strings.HasSuffix(path, "/data"):
			st.Blocks, st.Bavail, st.Bsize = 2000, 500, 4096
		case strings.HasSuffix(path, "/mnt/my disk"):
			st.Blocks, st.Bavail, st.Bsize = 100, 0, 4096
		case strings.HasSuffix(path, "/broken"):
			return errors.New("statfs failed")
		default:
			t.Fatalf("unexpected statfs path %q", path)
		}
		return nil
	}
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	samples := collector.diskSamples(now)
	if len(samples) != 9 {
		t.Fatalf("disk samples = %d, want 9: %+v", len(samples), samples)
	}
	if sample := findMetricSample(samples, "filesystem.used_bytes", map[string]string{"mount": "/", "device": "/dev/sda1"}); sample == nil || sample.Value == nil || *sample.Value != 850*4096 {
		t.Fatalf("filesystem used with reserved blocks = %+v", sample)
	}
	for _, tc := range []struct {
		mount  string
		device string
		want   float64
	}{
		{"/", "/dev/sda1", 90},
		{"/data", "/dev/sdb1", 75},
		{"/mnt/my disk", "/dev/sdd1", 100},
	} {
		sample := findMetricSample(samples, "disk.usage", map[string]string{"mount": tc.mount, "device": tc.device})
		if sample == nil || sample.Value == nil || *sample.Value != tc.want {
			t.Fatalf("disk %s = %+v, want %v", tc.mount, sample, tc.want)
		}
	}
}

func TestMetricsCollectorDiskInodes(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "mounts", "/dev/sda1 / ext4 rw 0 0\n/dev/sdb1 /nodata xfs rw 0 0\n")
	collector := newMetricsCollector(root)
	collector.statfsFn = func(path string, st *statfsT) error {
		st.Blocks, st.Bavail, st.Bsize = 100, 50, 4096
		if strings.HasSuffix(path, "/nodata") {
			st.Files, st.Ffree = 0, 0
			return nil
		}
		st.Files, st.Ffree = 1000, 400
		return nil
	}
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	samples := collector.diskSamples(now)

	for metric, want := range map[string]float64{
		"filesystem.inodes_usage": 60,
		"filesystem.inodes_used":  600,
		"filesystem.inodes_free":  400,
	} {
		sample := findMetricSample(samples, metric, map[string]string{"mount": "/", "device": "/dev/sda1"})
		if sample == nil || sample.Value == nil || *sample.Value != want {
			t.Fatalf("%s = %+v, want %v", metric, sample, want)
		}
	}
	if sample := findMetricSample(samples, "filesystem.inodes_usage", map[string]string{"mount": "/nodata", "device": "/dev/sdb1"}); sample != nil {
		t.Fatalf("f_files=0 must not emit inode metrics, got %+v", sample)
	}
}

func TestMetricsCollectorDiskMountLimit(t *testing.T) {
	root := t.TempDir()
	var mounts strings.Builder
	for i := 0; i < 40; i++ {
		fmt.Fprintf(&mounts, "/dev/sd%d /data%d ext4 rw 0 0\n", i, i)
	}
	writeProcFixture(t, root, "mounts", mounts.String())
	collector := newMetricsCollector(root)
	collector.statfsFn = func(path string, st *statfsT) error {
		st.Blocks, st.Bavail, st.Bsize = 100, 50, 4096
		return nil
	}
	samples := collector.diskSamples(time.Now())
	if len(samples) != 3*maxDiskMetricMounts {
		t.Fatalf("disk samples = %d, want limit %d", len(samples), 3*maxDiskMetricMounts)
	}
}

func TestMetricsCollectorDiskIO(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "diskstats", `   8       0 sda 100 0 2000 0 50 0 1000 0 0 0 0
   7       0 loop0 100 0 2000 0 50 0 1000 0 0 0 0
   1       0 ram0 100 0 2000 0 50 0 1000 0 0 0 0
 252       0 zram0 100 0 2000 0 50 0 1000 0 0 0 0
 259       0 nvme0n1 10 0 100 0 5 0 50 0 0 0 0
`)
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	if got := collector.diskIOSamples(now); len(got) != 0 {
		t.Fatalf("first disk I/O sample must be skipped, got %d", len(got))
	}

	writeProcFixture(t, root, "diskstats", `   8       0 sda 200 0 4000 0 100 0 2000 0 0 0 0
   7       0 loop0 200 0 4000 0 100 0 2000 0 0 0 0
   1       0 ram0 200 0 4000 0 100 0 2000 0 0 0 0
 252       0 zram0 200 0 4000 0 100 0 2000 0 0 0 0
 259       0 nvme0n1 20 0 300 0 10 0 150 0 0 0 0
`)
	samples := collector.diskIOSamples(now.Add(10 * time.Second))
	if len(samples) != 8 {
		t.Fatalf("disk I/O samples = %d, want 8: %+v", len(samples), samples)
	}
	for metric, want := range map[string]float64{
		"disk.read_bps":   2000 * 512 / 10,
		"disk.write_bps":  1000 * 512 / 10,
		"disk.read_iops":  10,
		"disk.write_iops": 5,
	} {
		sample := findMetricSample(samples, metric, map[string]string{"device": "sda"})
		if sample == nil || sample.Value == nil || *sample.Value != want {
			t.Fatalf("sda %s = %+v, want %v", metric, sample, want)
		}
	}
	if sample := findMetricSample(samples, "disk.read_bps", map[string]string{"device": "nvme0n1"}); sample == nil || sample.Value == nil || *sample.Value != 10240 {
		t.Fatalf("nvme read = %+v, want 10240", sample)
	}
	for _, pseudo := range []string{"loop0", "ram0", "zram0"} {
		if sample := findMetricSample(samples, "disk.read_bps", map[string]string{"device": pseudo}); sample != nil {
			t.Fatalf("pseudo device %s must be excluded", pseudo)
		}
	}
}

func TestMetricsCollectorZeroRatesAreValid(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "diskstats", "8 0 sda 10 0 100 0 5 0 50 0 0 0 0\n")
	writeProcFixture(t, root, "net/dev", "eth0: 100 10 0 0 0 0 0 0 200 10 0 0 0 0 0 0\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	collector.diskIOSamples(now)
	collector.networkSamples(now)

	diskSamples := collector.diskIOSamples(now.Add(10 * time.Second))
	if len(diskSamples) != 4 {
		t.Fatalf("zero disk samples = %d, want 4", len(diskSamples))
	}
	for _, sample := range diskSamples {
		if sample.Value == nil || *sample.Value != 0 || sample.Quality != protocol.NodeMetricQualityValid {
			t.Fatalf("zero disk sample = %+v, want valid 0", sample)
		}
	}
	networkSamples := collector.networkSamples(now.Add(10 * time.Second))
	if len(networkSamples) != 6 {
		t.Fatalf("zero network samples = %d, want 6", len(networkSamples))
	}
	for _, sample := range networkSamples {
		if sample.Value == nil || *sample.Value != 0 || sample.Quality != protocol.NodeMetricQualityValid {
			t.Fatalf("zero network sample = %+v, want valid 0", sample)
		}
	}
}

func TestMetricsCollectorDiskIOCounterReset(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "diskstats", "8 0 sda 100 0 2000 0 50 0 1000 0 0 0 0\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	collector.diskIOSamples(now)

	writeProcFixture(t, root, "diskstats", "8 0 sda 10 0 100 0 5 0 50 0 0 0 0\n")
	if got := collector.diskIOSamples(now.Add(10 * time.Second)); len(got) != 0 {
		t.Fatalf("disk I/O counter reset interval must be skipped, got %+v", got)
	}
}

func TestMetricsCollectorDiskIODeviceLimit(t *testing.T) {
	root := t.TempDir()
	var first, second strings.Builder
	for i := 0; i < maxDiskMetricDevices+4; i++ {
		fmt.Fprintf(&first, "8 %d sd%d 0 0 0 0 0 0 0 0 0 0 0\n", i, i)
		fmt.Fprintf(&second, "8 %d sd%d 0 0 100 0 0 0 100 0 0 0 0\n", i, i)
	}
	writeProcFixture(t, root, "diskstats", first.String())
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	collector.diskIOSamples(now)
	writeProcFixture(t, root, "diskstats", second.String())
	samples := collector.diskIOSamples(now.Add(10 * time.Second))
	if len(samples) != maxDiskMetricDevices*4 {
		t.Fatalf("disk I/O samples = %d, want %d", len(samples), maxDiskMetricDevices*4)
	}
}

func TestMetricsCollectorSystemSamples(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "uptime", "12345.67 1000.00\n")
	writeProcFixture(t, root, "loadavg", "0.50 1.25 2.75 3/456 12345\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	samples := collector.systemSamples(now)
	if sample := findMetricSample(samples, "system.uptime", nil); sample == nil || sample.Value == nil || *sample.Value != 12345.67 {
		t.Fatalf("system.uptime = %+v", sample)
	}
	if sample := findMetricSample(samples, "system.processes", nil); sample == nil || sample.Value == nil || *sample.Value != 456 {
		t.Fatalf("system.processes = %+v", sample)
	}
	if sample := findMetricSample(samples, "system.processes_running", nil); sample == nil || sample.Value == nil || *sample.Value != 3 {
		t.Fatalf("system.processes_running = %+v", sample)
	}

	writeProcFixture(t, root, "uptime", "0.00 0.00\n")
	writeProcFixture(t, root, "loadavg", "0.00 0.00 0.00 0/0 1\n")
	samples = collector.systemSamples(now)
	if sample := findMetricSample(samples, "system.uptime", nil); sample == nil || sample.Value == nil || *sample.Value != 0 {
		t.Fatalf("zero uptime = %+v", sample)
	}
	if sample := findMetricSample(samples, "system.processes", nil); sample == nil || sample.Value == nil || *sample.Value != 0 {
		t.Fatalf("zero processes = %+v", sample)
	}
}

func TestMetricsCollectorSystemMissingAndInvalid(t *testing.T) {
	root := t.TempDir()
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	samples := collector.systemSamples(now)
	if len(samples) != 3 {
		t.Fatalf("missing system samples = %d, want 3", len(samples))
	}
	for _, sample := range samples {
		if sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
			t.Fatalf("missing system sample = %+v", sample)
		}
	}

	writeProcFixture(t, root, "uptime", "not-a-number\n")
	writeProcFixture(t, root, "loadavg", "0.1 0.2 0.3 broken\n")
	samples = collector.systemSamples(now)
	if sample := findMetricSample(samples, "system.uptime", nil); sample == nil || sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
		t.Fatalf("invalid uptime = %+v, want missing", sample)
	}
	if sample := findMetricSample(samples, "system.processes", nil); sample == nil || sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
		t.Fatalf("invalid processes = %+v, want missing", sample)
	}
}

func TestMetricsCollectorTemperature(t *testing.T) {
	root := t.TempDir()
	writeSysFixture(t, root, "class/thermal/thermal_zone0/temp", "not-a-number\n")
	writeSysFixture(t, root, "class/thermal/thermal_zone1/temp", "0\n")
	writeSysFixture(t, root, "class/thermal/thermal_zone2/temp", "42000\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	samples := collector.temperatureSamples(now)
	if len(samples) != 1 || samples[0].Value == nil || *samples[0].Value != 42 {
		t.Fatalf("temperature samples = %+v, want 42 C", samples)
	}
	if samples[0].Quality != protocol.NodeMetricQualityValid {
		t.Fatalf("temperature quality = %d, want valid", samples[0].Quality)
	}
}

func TestMetricsCollectorTemperatureMissing(t *testing.T) {
	root := t.TempDir()
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	samples := collector.temperatureSamples(now)
	if len(samples) != 1 || samples[0].Value != nil || samples[0].Quality != protocol.NodeMetricQualityMissing {
		t.Fatalf("missing temperature = %+v, want nil quality 2", samples)
	}

	writeSysFixture(t, root, "class/thermal/thermal_zone0/temp", "0\n")
	writeSysFixture(t, root, "class/thermal/thermal_zone1/temp", "not-a-number\n")
	samples = collector.temperatureSamples(now)
	if len(samples) != 1 || samples[0].Value != nil || samples[0].Quality != protocol.NodeMetricQualityMissing {
		t.Fatalf("invalid temperature = %+v, want nil quality 2", samples)
	}
}

func TestMetricsCollectorFanRPM(t *testing.T) {
	root := t.TempDir()
	writeSysFixture(t, root, "class/hwmon/hwmon0/fan1_input", "1200\n")
	writeSysFixture(t, root, "class/hwmon/hwmon0/fan2_input", "not-a-number\n")
	writeSysFixture(t, root, "class/hwmon/hwmon1/fan1_input", "800\n")
	writeSysFixture(t, root, "class/hwmon/hwmon1/name", "test-chip\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	samples := collector.fanSamples(now)
	if len(samples) != 2 {
		t.Fatalf("fan samples = %d, want 2: %+v", len(samples), samples)
	}
	for fan, want := range map[string]float64{"hwmon0/fan1": 1200, "hwmon1/fan1": 800} {
		sample := findMetricSample(samples, "system.fan_rpm", map[string]string{"fan": fan})
		if sample == nil || sample.Value == nil || *sample.Value != want {
			t.Fatalf("fan %s = %+v, want %v", fan, sample, want)
		}
	}
}

func TestMetricsCollectorFanMissingAndLimit(t *testing.T) {
	root := t.TempDir()
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	if got := collector.fanSamples(now); len(got) != 0 {
		t.Fatalf("no hwmon should produce no fan samples, got %+v", got)
	}

	for i := 0; i < maxFanSensors+4; i++ {
		writeSysFixture(t, root, fmt.Sprintf("class/hwmon/hwmon0/fan%d_input", i), "1000\n")
	}
	samples := collector.fanSamples(now)
	if len(samples) != maxFanSensors {
		t.Fatalf("fan samples = %d, want limit %d", len(samples), maxFanSensors)
	}
}

func writeSysFixture(t *testing.T, root, name, content string) {
	t.Helper()
	path := filepath.Join(root, "sys", name)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir sys fixture: %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write sys fixture %s: %v", name, err)
	}
}

func TestMetricsCollectorNetworkRates(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "net/dev", `Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0
  eth0: 1000 10 0 0 0 0 0 0 2000 10 0 0 0 0 0 0
`)
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	if got := collector.networkSamples(now); len(got) != 0 {
		t.Fatalf("first network sample must be skipped, got %d", len(got))
	}

	writeProcFixture(t, root, "net/dev", `Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 9000 10 5 5 0 0 0 0 9000 10 5 5 0 0 0 0
  eth0: 3000 10 2 3 0 0 0 0 5000 10 4 5 0 0 0 0
`)
	samples := collector.networkSamples(now.Add(10 * time.Second))
	if len(samples) != 6 {
		t.Fatalf("network samples = %d, want 6: %+v", len(samples), samples)
	}
	for metric, want := range map[string]float64{
		"network.rx_bps":     200,
		"network.tx_bps":     300,
		"network.rx_errors":  0.2,
		"network.tx_errors":  0.4,
		"network.rx_dropped": 0.3,
		"network.tx_dropped": 0.5,
	} {
		sample := findMetricSample(samples, metric, map[string]string{"interface": "eth0"})
		if sample == nil || sample.Value == nil || *sample.Value != want {
			t.Fatalf("eth0 %s = %+v, want %v", metric, sample, want)
		}
	}
	if findMetricSample(samples, "network.rx_bps", map[string]string{"interface": "lo"}) != nil {
		t.Fatal("loopback must be excluded from network metrics")
	}
}

func TestMetricsCollectorNetworkInterfaceLimit(t *testing.T) {
	root := t.TempDir()
	var first, second strings.Builder
	for i := 0; i < maxNetworkMetricInterfaces+4; i++ {
		fmt.Fprintf(&first, "eth%d: 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n", i)
		fmt.Fprintf(&second, "eth%d: 100 0 0 0 0 0 0 0 100 0 0 0 0 0 0 0\n", i)
	}
	writeProcFixture(t, root, "net/dev", first.String())
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	collector.networkSamples(now)
	writeProcFixture(t, root, "net/dev", second.String())
	samples := collector.networkSamples(now.Add(10 * time.Second))
	if len(samples) != maxNetworkMetricInterfaces*6 {
		t.Fatalf("network samples = %d, want %d", len(samples), maxNetworkMetricInterfaces*6)
	}
}

func TestMetricsCollectorNetworkCounterReset(t *testing.T) {
	root := t.TempDir()
	writeProcFixture(t, root, "net/dev", "eth0: 1000 10 0 0 0 0 0 0 2000 10 0 0 0 0 0 0\n")
	collector := newMetricsCollector(root)
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)
	collector.networkSamples(now)

	writeProcFixture(t, root, "net/dev", "eth0: 100 10 0 0 0 0 0 0 100 10 0 0 0 0 0 0\n")
	if got := collector.networkSamples(now.Add(10 * time.Second)); len(got) != 0 {
		t.Fatalf("counter reset interval must be skipped, got %+v", got)
	}
}

func TestMetricsCollectorMissingFilesAreNotZero(t *testing.T) {
	collector := newMetricsCollector(t.TempDir())
	now := time.Date(2026, 11, 3, 10, 0, 0, 0, time.UTC)

	samples := collector.collect(now)
	if len(samples) != 18 {
		t.Fatalf("samples = %d, want 11 memory + 3 load + 3 system + 1 temperature: %+v", len(samples), samples)
	}
	for _, sample := range samples {
		if sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
			t.Fatalf("sample %+v must be missing, not zero", sample)
		}
	}
}

func TestMetricValueRejectsNonFinite(t *testing.T) {
	if metricValue(math.NaN()) != nil {
		t.Fatal("NaN must be rejected")
	}
	if metricValue(math.Inf(1)) != nil {
		t.Fatal("Inf must be rejected")
	}
	sample := validMetricSample(time.Now(), "cpu.usage", nil, math.NaN())
	if sample.Value != nil || sample.Quality != protocol.NodeMetricQualityMissing {
		t.Fatalf("non-finite sample = %+v, want missing", sample)
	}
}

func TestUnescapeMount(t *testing.T) {
	if got := unescapeMount(`/mnt/my\040disk\011x\134y`); got != "/mnt/my disk\tx\\y" {
		t.Fatalf("unescapeMount = %q", got)
	}
}
