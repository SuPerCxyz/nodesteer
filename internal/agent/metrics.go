package agent

import (
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

const (
	// maxDiskMetricMounts 单次采集最多上报的挂载点数量，避免挂载点过多时批次膨胀。
	maxDiskMetricMounts = 32
	// maxCPUMetricCores 单次采集最多上报 CPU 组成明细的核心数；cpu=all 始终上报。
	maxCPUMetricCores = 32
	// maxDiskMetricDevices 单次采集最多上报 I/O 速率的块设备数量。
	maxDiskMetricDevices = 32
	// maxNetworkMetricInterfaces 单次采集最多上报的网络接口数量。
	maxNetworkMetricInterfaces = 32
	// maxThermalZones 单次采集最多尝试读取的热区数量，避免遍历大量无效传感器。
	maxThermalZones = 16
	// maxFanSensors 单次采集最多上报的风扇转速数量。
	maxFanSensors = 16
	// diskSectorSize /proc/diskstats 固定以 512 字节扇区统计。
	diskSectorSize = 512
)

// pseudoFilesystems 不参与磁盘使用率采集的伪文件系统。
var pseudoFilesystems = map[string]bool{
	"autofs":          true,
	"binfmt_misc":     true,
	"bpf":             true,
	"cgroup":          true,
	"cgroup2":         true,
	"configfs":        true,
	"debugfs":         true,
	"devpts":          true,
	"devtmpfs":        true,
	"efivarfs":        true,
	"fusectl":         true,
	"hugetlbfs":       true,
	"mqueue":          true,
	"nsfs":            true,
	"overlay":         true,
	"proc":            true,
	"pstore":          true,
	"ramfs":           true,
	"rpc_pipefs":      true,
	"securityfs":      true,
	"selinuxfs":       true,
	"squashfs":        true,
	"sysfs":           true,
	"tmpfs":           true,
	"tracefs":         true,
	"fuse.lxcfs":      true,
	"fuse.gvfsd-fuse": true,
}

// cpuTimes 保存 /proc/stat 中单个 CPU 行的累计时间。
// guest/guest_nice 已包含在 user/nice 中，不重复计入 total。
type cpuTimes struct {
	user    uint64
	nice    uint64
	system  uint64
	idle    uint64
	iowait  uint64
	irq     uint64
	softirq uint64
	steal   uint64
	total   uint64
}

type netCounters struct {
	rx        uint64
	tx        uint64
	rxErrors  uint64
	txErrors  uint64
	rxDropped uint64
	txDropped uint64
}

type diskIOCounters struct {
	readSectors  uint64
	writeSectors uint64
	readOps      uint64
	writeOps     uint64
}

// metricsCollector 从 /proc 采集 Linux 节点指标。
// root 为空表示直接读取本机 /proc；非空时读取 root/proc（容器 Host Integration）。
// readFile/statfsFn 可注入，测试使用临时目录与固定 statfs 结果。
type metricsCollector struct {
	root         string
	readFile     func(string) ([]byte, error)
	statfsFn     func(string, *statfsT) error
	cpuPrev      map[string]cpuTimes
	netPrev      map[string]netCounters
	netPrevAt    time.Time
	netInit      bool
	diskIOPrev   map[string]diskIOCounters
	diskIOPrevAt time.Time
	diskIOInit   bool
	// containerSocketPath 仅用于测试注入；为空时自动探测常见 Docker/Podman socket。
	containerSocketPath string
	containerNetPrev    map[string]containerNetCounters
	containerNetPrevAt  time.Time
	containerNetInit    bool
	// containerCapable 记录 Docker/Podman API 是否已探测/采集成功，供 HELLO 能力上报。
	containerCapable atomic.Bool
}

func newMetricsCollector(root string) *metricsCollector {
	return &metricsCollector{
		root:             root,
		readFile:         os.ReadFile,
		statfsFn:         statfs,
		cpuPrev:          map[string]cpuTimes{},
		netPrev:          map[string]netCounters{},
		diskIOPrev:       map[string]diskIOCounters{},
		containerNetPrev: map[string]containerNetCounters{},
	}
}

func (c *metricsCollector) procPath(name string) string {
	if c.root == "" {
		return filepath.Join("/proc", name)
	}
	return filepath.Join(c.root, "proc", name)
}

func (c *metricsCollector) read(name string) ([]byte, error) {
	return c.readPath(c.procPath(name))
}

func (c *metricsCollector) sysPath(name string) string {
	if c.root == "" {
		return filepath.Join("/sys", name)
	}
	return filepath.Join(c.root, "sys", name)
}

func (c *metricsCollector) readPath(path string) ([]byte, error) {
	if c.readFile == nil {
		return os.ReadFile(path)
	}
	return c.readFile(path)
}

// collect 采集一轮指标。now 同时作为样本时间与速率计算基准。
func (c *metricsCollector) collect(now time.Time) []protocol.NodeMetricSample {
	now = now.UTC()
	samples := make([]protocol.NodeMetricSample, 0, 64)
	samples = append(samples, c.cpuSamples(now)...)
	samples = append(samples, c.memorySamples(now)...)
	samples = append(samples, c.loadSamples(now)...)
	samples = append(samples, c.diskSamples(now)...)
	samples = append(samples, c.diskIOSamples(now)...)
	samples = append(samples, c.networkSamples(now)...)
	samples = append(samples, c.systemSamples(now)...)
	samples = append(samples, c.temperatureSamples(now)...)
	samples = append(samples, c.fanSamples(now)...)
	samples = append(samples, c.containerSamples(now)...)
	return samples
}

// cpuCompositionMetrics 与 cpuTimes 的字段顺序一致，便于稳定输出组成明细。
var cpuCompositionMetrics = [8]string{
	"cpu.user",
	"cpu.nice",
	"cpu.system",
	"cpu.idle",
	"cpu.iowait",
	"cpu.irq",
	"cpu.softirq",
	"cpu.steal",
}

func (c *metricsCollector) cpuSamples(now time.Time) []protocol.NodeMetricSample {
	data, err := c.read("stat")
	if err != nil {
		return nil
	}
	current := parseCPUStat(data)
	if len(current) == 0 {
		return nil
	}
	keys := make([]string, 0, len(current))
	for key := range current {
		keys = append(keys, key)
	}
	sortCPUKeys(keys)

	samples := make([]protocol.NodeMetricSample, 0, len(keys)*9)
	for _, key := range keys {
		prev, ok := c.cpuPrev[key]
		if !ok {
			continue
		}
		usage, composition, ok := cpuBreakdown(prev, current[key])
		if !ok {
			continue
		}
		cpu := strings.TrimPrefix(key, "cpu")
		if cpu == "" {
			cpu = "all"
		}
		dims := map[string]string{"cpu": cpu}
		samples = append(samples, validMetricSample(now, "cpu.usage", dims, usage))
		// 总 CPU 始终上报组成；per-core 限制数量，避免核心数很多时指标爆炸。
		if core, perCore := cpuCoreIndex(key); perCore && core >= maxCPUMetricCores {
			continue
		}
		for index, metric := range cpuCompositionMetrics {
			samples = append(samples, validMetricSample(now, metric, dims, composition[index]))
		}
	}
	c.cpuPrev = current
	return samples
}

// parseCPUStat 解析 /proc/stat 的 cpu 汇总行与 per-core 行。
// guest/guest_nice 已包含在 user/nice 中，不重复计入 total。
func parseCPUStat(data []byte) map[string]cpuTimes {
	out := map[string]cpuTimes{}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		key := fields[0]
		if key != "cpu" {
			if !strings.HasPrefix(key, "cpu") {
				continue
			}
			suffix := strings.TrimPrefix(key, "cpu")
			if _, err := strconv.Atoi(suffix); err != nil {
				continue
			}
		}
		var values [8]uint64
		valid := true
		for i := 0; i < len(values) && i+1 < len(fields); i++ {
			v, err := strconv.ParseUint(fields[i+1], 10, 64)
			if err != nil {
				valid = false
				break
			}
			values[i] = v
		}
		if !valid {
			continue
		}
		var total uint64
		for _, v := range values {
			total += v
		}
		out[key] = cpuTimes{
			user:    values[0],
			nice:    values[1],
			system:  values[2],
			idle:    values[3],
			iowait:  values[4],
			irq:     values[5],
			softirq: values[6],
			steal:   values[7],
			total:   total,
		}
	}
	return out
}

// cpuBreakdown 计算两次采样的各组成占比。iowait 属于非 idle 时间，计入 usage；
// 所有组成（含 idle）之和为 100%，非 idle 组成之和等于 cpu.usage。
func cpuBreakdown(prev, current cpuTimes) (float64, [8]float64, bool) {
	var composition [8]float64
	if current.total < prev.total {
		return 0, composition, false
	}
	deltas := [8]uint64{
		current.user - prev.user,
		current.nice - prev.nice,
		current.system - prev.system,
		current.idle - prev.idle,
		current.iowait - prev.iowait,
		current.irq - prev.irq,
		current.softirq - prev.softirq,
		current.steal - prev.steal,
	}
	// 任一字段回退都说明计数器被重置，整行本周期跳过，等待下一周期重建基线。
	values := [8]uint64{prev.user, prev.nice, prev.system, prev.idle, prev.iowait, prev.irq, prev.softirq, prev.steal}
	latest := [8]uint64{current.user, current.nice, current.system, current.idle, current.iowait, current.irq, current.softirq, current.steal}
	for index := range values {
		if latest[index] < values[index] {
			return 0, composition, false
		}
	}
	totalDelta := current.total - prev.total
	if totalDelta == 0 {
		return 0, composition, false
	}
	idleDelta := deltas[3]
	if idleDelta > totalDelta {
		return 0, composition, false
	}
	for index, delta := range deltas {
		composition[index] = clampPercent(float64(delta) / float64(totalDelta) * 100)
	}
	usage := clampPercent(float64(totalDelta-idleDelta) / float64(totalDelta) * 100)
	return usage, composition, true
}

// cpuCoreIndex 返回 cpuN 的核心编号；cpu 汇总行返回 false。
func cpuCoreIndex(key string) (int, bool) {
	if key == "cpu" {
		return 0, false
	}
	index, err := strconv.Atoi(strings.TrimPrefix(key, "cpu"))
	if err != nil {
		return 0, false
	}
	return index, true
}

func sortCPUKeys(keys []string) {
	sort.Slice(keys, func(i, j int) bool {
		if keys[i] == "cpu" {
			return true
		}
		if keys[j] == "cpu" {
			return false
		}
		left, _ := strconv.Atoi(strings.TrimPrefix(keys[i], "cpu"))
		right, _ := strconv.Atoi(strings.TrimPrefix(keys[j], "cpu"))
		return left < right
	})
}

func (c *metricsCollector) memorySamples(now time.Time) []protocol.NodeMetricSample {
	data, err := c.read("meminfo")
	if err != nil {
		return missingMemorySamples(now)
	}
	values := parseMemInfo(data)
	totalKB, hasTotal := values["MemTotal"]
	if !hasTotal || totalKB == 0 {
		samples := missingMemorySamples(now)
		if freeKB, ok := values["MemFree"]; ok {
			for i := range samples {
				if samples[i].Metric == "memory.free_bytes" {
					samples[i] = validMetricSample(now, "memory.free_bytes", nil, kbToBytes(freeKB))
				}
			}
		}
		return samples
	}

	availableKB, hasAvailable := values["MemAvailable"]
	freeKB, hasFree := values["MemFree"]
	if !hasAvailable {
		// 兼容旧内核：MemAvailable 缺失时仍用 MemFree+Buffers+Cached 计算 usage，
		// 但 available_bytes 本身仍按缺失上报，不把推导值伪装成原始字段。
		if hasFree {
			availableKB = freeKB + values["Buffers"] + values["Cached"]
		}
	}
	if availableKB > totalKB {
		availableKB = totalKB
	}
	hasUsageBaseline := hasAvailable || hasFree

	samples := make([]protocol.NodeMetricSample, 0, 10)
	if hasUsageBaseline {
		usage := float64(totalKB-availableKB) / float64(totalKB) * 100
		samples = append(samples, validMetricSample(now, "memory.usage", nil, usage))
	} else {
		samples = append(samples, missingMetricSample(now, "memory.usage", nil))
	}

	totalBytes := kbToBytes(totalKB)
	samples = append(samples, validMetricSample(now, "memory.total_bytes", nil, totalBytes))

	if hasUsageBaseline {
		samples = append(samples, validMetricSample(now, "memory.used_bytes", nil, totalBytes-kbToBytes(availableKB)))
	} else {
		samples = append(samples, missingMetricSample(now, "memory.used_bytes", nil))
	}
	if hasAvailable {
		samples = append(samples, validMetricSample(now, "memory.available_bytes", nil, kbToBytes(availableKB)))
	} else {
		samples = append(samples, missingMetricSample(now, "memory.available_bytes", nil))
	}
	if hasFree {
		samples = append(samples, validMetricSample(now, "memory.free_bytes", nil, kbToBytes(freeKB)))
	} else {
		samples = append(samples, missingMetricSample(now, "memory.free_bytes", nil))
	}
	if cached, ok := values["Cached"]; ok {
		samples = append(samples, validMetricSample(now, "memory.cached_bytes", nil, kbToBytes(cached)))
	} else {
		samples = append(samples, missingMetricSample(now, "memory.cached_bytes", nil))
	}
	if buffers, ok := values["Buffers"]; ok {
		samples = append(samples, validMetricSample(now, "memory.buffers_bytes", nil, kbToBytes(buffers)))
	} else {
		samples = append(samples, missingMetricSample(now, "memory.buffers_bytes", nil))
	}
	samples = append(samples, swapSamples(now, values)...)
	return samples
}

// swapSamples 输出 Swap 明细。SwapTotal=0 表示未启用 Swap，是真实 0；
// 只有字段缺失时才用 quality=2。
func swapSamples(now time.Time, values map[string]uint64) []protocol.NodeMetricSample {
	totalKB, hasTotal := values["SwapTotal"]
	freeKB, hasFree := values["SwapFree"]
	if !hasTotal {
		return missingMetricSamples(now, []string{
			"memory.swap_total_bytes",
			"memory.swap_used_bytes",
			"memory.swap_free_bytes",
			"memory.swap_usage",
		})
	}
	if totalKB == 0 {
		return []protocol.NodeMetricSample{
			validMetricSample(now, "memory.swap_total_bytes", nil, 0),
			validMetricSample(now, "memory.swap_used_bytes", nil, 0),
			validMetricSample(now, "memory.swap_free_bytes", nil, 0),
			validMetricSample(now, "memory.swap_usage", nil, 0),
		}
	}
	if !hasFree {
		return []protocol.NodeMetricSample{
			validMetricSample(now, "memory.swap_total_bytes", nil, kbToBytes(totalKB)),
			missingMetricSample(now, "memory.swap_used_bytes", nil),
			missingMetricSample(now, "memory.swap_free_bytes", nil),
			missingMetricSample(now, "memory.swap_usage", nil),
		}
	}
	if freeKB > totalKB {
		freeKB = totalKB
	}
	usedKB := totalKB - freeKB
	return []protocol.NodeMetricSample{
		validMetricSample(now, "memory.swap_total_bytes", nil, kbToBytes(totalKB)),
		validMetricSample(now, "memory.swap_used_bytes", nil, kbToBytes(usedKB)),
		validMetricSample(now, "memory.swap_free_bytes", nil, kbToBytes(freeKB)),
		validMetricSample(now, "memory.swap_usage", nil, float64(usedKB)/float64(totalKB)*100),
	}
}

func missingMemorySamples(now time.Time) []protocol.NodeMetricSample {
	return missingMetricSamples(now, []string{
		"memory.usage",
		"memory.total_bytes",
		"memory.used_bytes",
		"memory.available_bytes",
		"memory.free_bytes",
		"memory.cached_bytes",
		"memory.buffers_bytes",
		"memory.swap_total_bytes",
		"memory.swap_used_bytes",
		"memory.swap_free_bytes",
		"memory.swap_usage",
	})
}

func kbToBytes(kb uint64) float64 {
	return float64(kb) * 1024
}

func parseMemInfo(data []byte) map[string]uint64 {
	out := map[string]uint64{}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		key := strings.TrimSuffix(fields[0], ":")
		v, err := strconv.ParseUint(fields[1], 10, 64)
		if err != nil {
			continue
		}
		out[key] = v
	}
	return out
}

func (c *metricsCollector) loadSamples(now time.Time) []protocol.NodeMetricSample {
	names := []string{"load.1", "load.5", "load.15"}
	data, err := c.read("loadavg")
	if err != nil {
		return missingMetricSamples(now, names)
	}
	fields := strings.Fields(string(data))
	if len(fields) < len(names) {
		return missingMetricSamples(now, names)
	}
	samples := make([]protocol.NodeMetricSample, 0, len(names))
	for i, name := range names {
		value, err := strconv.ParseFloat(fields[i], 64)
		if err != nil {
			samples = append(samples, missingMetricSample(now, name, nil))
			continue
		}
		samples = append(samples, validMetricSample(now, name, nil, value))
	}
	return samples
}

func (c *metricsCollector) diskSamples(now time.Time) []protocol.NodeMetricSample {
	data, err := c.read("mounts")
	if err != nil {
		return nil
	}
	samples := make([]protocol.NodeMetricSample, 0, maxDiskMetricMounts*4)
	seen := map[string]bool{}
	mountCount := 0
	for _, line := range strings.Split(string(data), "\n") {
		if mountCount >= maxDiskMetricMounts {
			break
		}
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		device := unescapeMount(fields[0])
		mount := unescapeMount(fields[1])
		fstype := strings.ToLower(fields[2])
		options := fields[3]
		if seen[mount] || !metricFilesystemAllowed(fstype, mount, options) {
			continue
		}
		seen[mount] = true
		path := mount
		if c.root != "" {
			path = filepath.Join(c.root, mount)
		}
		statfsFn := c.statfsFn
		if statfsFn == nil {
			statfsFn = statfs
		}
		var st statfsT
		if statfsFn(path, &st) != nil {
			continue
		}
		if st.Bsize <= 0 {
			continue
		}
		blockSize := uint64(st.Bsize)
		total := st.Blocks * blockSize
		if total == 0 {
			continue
		}
		available := st.Bavail * blockSize
		if available > total {
			available = total
		}
		usage := float64(total-available) / float64(total) * 100
		dims := map[string]string{
			"mount":  mount,
			"device": device,
		}
		freeBlocks := st.Bfree
		if freeBlocks > st.Blocks {
			freeBlocks = st.Blocks
		}
		samples = append(samples,
			validMetricSample(now, "disk.usage", dims, usage),
			validMetricSample(now, "filesystem.total_bytes", dims, float64(st.Blocks)*float64(blockSize)),
			validMetricSample(now, "filesystem.used_bytes", dims, float64(st.Blocks-freeBlocks)*float64(blockSize)),
		)
		if st.Files > 0 {
			freeInodes := st.Ffree
			if freeInodes > st.Files {
				freeInodes = st.Files
			}
			usedInodes := st.Files - freeInodes
			inodeUsage := float64(usedInodes) / float64(st.Files) * 100
			samples = append(samples,
				validMetricSample(now, "filesystem.inodes_usage", dims, inodeUsage),
				validMetricSample(now, "filesystem.inodes_used", dims, float64(usedInodes)),
				validMetricSample(now, "filesystem.inodes_free", dims, float64(freeInodes)),
			)
		}
		mountCount++
	}
	return samples
}

func metricFilesystemAllowed(fstype, mount, options string) bool {
	if pseudoFilesystems[fstype] {
		return false
	}
	for _, prefix := range []string{"/proc", "/sys", "/dev"} {
		if mount == prefix || strings.HasPrefix(mount, prefix+"/") {
			return false
		}
	}
	for _, option := range strings.Split(options, ",") {
		if option == "ro" {
			return false
		}
	}
	return true
}

var mountEscapes = strings.NewReplacer(
	`\040`, " ",
	`\011`, "\t",
	`\012`, "\n",
	`\134`, `\`,
)

func unescapeMount(value string) string {
	return mountEscapes.Replace(value)
}

// diskIOSamples 基于 /proc/diskstats 两次差值计算每设备吞吐与 IOPS。
func (c *metricsCollector) diskIOSamples(now time.Time) []protocol.NodeMetricSample {
	data, err := c.read("diskstats")
	if err != nil {
		return nil
	}
	current := parseDiskStats(data)
	if len(current) == 0 {
		return nil
	}
	if !c.diskIOInit {
		c.diskIOPrev = current
		c.diskIOPrevAt = now
		c.diskIOInit = true
		return nil
	}
	elapsed := now.Sub(c.diskIOPrevAt).Seconds()
	if elapsed <= 0 {
		c.diskIOPrev = current
		c.diskIOPrevAt = now
		return nil
	}

	names := make([]string, 0, len(current))
	for name := range current {
		names = append(names, name)
	}
	sort.Strings(names)
	if len(names) > maxDiskMetricDevices {
		names = names[:maxDiskMetricDevices]
	}

	samples := make([]protocol.NodeMetricSample, 0, len(names)*4)
	for _, name := range names {
		prev, ok := c.diskIOPrev[name]
		if !ok {
			continue
		}
		cur := current[name]
		// 任一计数器回退都按设备重置处理，本周期跳过并重建基线。
		if cur.readSectors < prev.readSectors || cur.writeSectors < prev.writeSectors ||
			cur.readOps < prev.readOps || cur.writeOps < prev.writeOps {
			continue
		}
		dims := map[string]string{"device": name}
		readBPS := float64(cur.readSectors-prev.readSectors) * diskSectorSize / elapsed
		writeBPS := float64(cur.writeSectors-prev.writeSectors) * diskSectorSize / elapsed
		readIOPS := float64(cur.readOps-prev.readOps) / elapsed
		writeIOPS := float64(cur.writeOps-prev.writeOps) / elapsed
		samples = append(samples,
			validMetricSample(now, "disk.read_bps", dims, readBPS),
			validMetricSample(now, "disk.write_bps", dims, writeBPS),
			validMetricSample(now, "disk.read_iops", dims, readIOPS),
			validMetricSample(now, "disk.write_iops", dims, writeIOPS),
		)
	}
	c.diskIOPrev = current
	c.diskIOPrevAt = now
	return samples
}

// parseDiskStats 解析 /proc/diskstats，跳过 loop/ram/zram 等伪设备。
func parseDiskStats(data []byte) map[string]diskIOCounters {
	out := map[string]diskIOCounters{}
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 14 {
			continue
		}
		name := fields[2]
		if name == "" || isPseudoDiskDevice(name) {
			continue
		}
		readOps, readOpsErr := strconv.ParseUint(fields[3], 10, 64)
		readSectors, readSectorsErr := strconv.ParseUint(fields[5], 10, 64)
		writeOps, writeOpsErr := strconv.ParseUint(fields[7], 10, 64)
		writeSectors, writeSectorsErr := strconv.ParseUint(fields[9], 10, 64)
		if readOpsErr != nil || readSectorsErr != nil || writeOpsErr != nil || writeSectorsErr != nil {
			continue
		}
		out[name] = diskIOCounters{
			readSectors:  readSectors,
			writeSectors: writeSectors,
			readOps:      readOps,
			writeOps:     writeOps,
		}
	}
	return out
}

var pseudoDiskPrefixes = []string{"loop", "ram", "zram", "fd", "sr"}

func isPseudoDiskDevice(name string) bool {
	for _, prefix := range pseudoDiskPrefixes {
		if strings.HasPrefix(name, prefix) {
			return true
		}
	}
	return false
}

func (c *metricsCollector) networkSamples(now time.Time) []protocol.NodeMetricSample {
	data, err := c.read("net/dev")
	if err != nil {
		return nil
	}
	current := parseNetDev(data)
	if len(current) == 0 {
		return nil
	}
	if !c.netInit {
		c.netPrev = current
		c.netPrevAt = now
		c.netInit = true
		return nil
	}
	elapsed := now.Sub(c.netPrevAt).Seconds()
	if elapsed <= 0 {
		c.netPrev = current
		c.netPrevAt = now
		return nil
	}

	names := make([]string, 0, len(current))
	for name := range current {
		names = append(names, name)
	}
	sort.Strings(names)
	if len(names) > maxNetworkMetricInterfaces {
		names = names[:maxNetworkMetricInterfaces]
	}

	samples := make([]protocol.NodeMetricSample, 0, len(names)*6)
	for _, name := range names {
		prev, ok := c.netPrev[name]
		if !ok {
			continue
		}
		cur := current[name]
		// 计数器回绕或重置时跳过本周期，等待下一周期重新建立基线。
		if cur.rx < prev.rx || cur.tx < prev.tx ||
			cur.rxErrors < prev.rxErrors || cur.txErrors < prev.txErrors ||
			cur.rxDropped < prev.rxDropped || cur.txDropped < prev.txDropped {
			continue
		}
		dims := map[string]string{"interface": name}
		samples = append(samples,
			validMetricSample(now, "network.rx_bps", dims, float64(cur.rx-prev.rx)/elapsed),
			validMetricSample(now, "network.tx_bps", dims, float64(cur.tx-prev.tx)/elapsed),
			validMetricSample(now, "network.rx_errors", dims, float64(cur.rxErrors-prev.rxErrors)/elapsed),
			validMetricSample(now, "network.tx_errors", dims, float64(cur.txErrors-prev.txErrors)/elapsed),
			validMetricSample(now, "network.rx_dropped", dims, float64(cur.rxDropped-prev.rxDropped)/elapsed),
			validMetricSample(now, "network.tx_dropped", dims, float64(cur.txDropped-prev.txDropped)/elapsed),
		)
	}
	c.netPrev = current
	c.netPrevAt = now
	return samples
}

// parseNetDev 解析 /proc/net/dev。明确跳过回环 lo，避免把本机回环流量计入节点网络指标。
func parseNetDev(data []byte) map[string]netCounters {
	out := map[string]netCounters{}
	for _, line := range strings.Split(string(data), "\n") {
		colon := strings.IndexByte(line, ':')
		if colon < 0 {
			continue
		}
		name := strings.TrimSpace(line[:colon])
		if name == "" || name == "lo" {
			continue
		}
		fields := strings.Fields(line[colon+1:])
		if len(fields) < 16 {
			continue
		}
		rx, rxErr := strconv.ParseUint(fields[0], 10, 64)
		tx, txErr := strconv.ParseUint(fields[8], 10, 64)
		rxErrors, rxErrorsErr := strconv.ParseUint(fields[2], 10, 64)
		rxDropped, rxDroppedErr := strconv.ParseUint(fields[3], 10, 64)
		txErrors, txErrorsErr := strconv.ParseUint(fields[10], 10, 64)
		txDropped, txDroppedErr := strconv.ParseUint(fields[11], 10, 64)
		if rxErr != nil || txErr != nil || rxErrorsErr != nil || rxDroppedErr != nil ||
			txErrorsErr != nil || txDroppedErr != nil {
			continue
		}
		out[name] = netCounters{
			rx:        rx,
			tx:        tx,
			rxErrors:  rxErrors,
			txErrors:  txErrors,
			rxDropped: rxDropped,
			txDropped: txDropped,
		}
	}
	return out
}

// systemSamples 采集开机时长与进程数。进程数来自 /proc/loadavg 的 running/total 字段。
func (c *metricsCollector) systemSamples(now time.Time) []protocol.NodeMetricSample {
	samples := make([]protocol.NodeMetricSample, 0, 3)

	data, err := c.read("uptime")
	if err != nil {
		samples = append(samples, missingMetricSample(now, "system.uptime", nil))
	} else if uptime, ok := parseUptime(data); ok {
		samples = append(samples, validMetricSample(now, "system.uptime", nil, uptime))
	} else {
		samples = append(samples, missingMetricSample(now, "system.uptime", nil))
	}

	loadData, err := c.read("loadavg")
	if err != nil {
		samples = append(samples,
			missingMetricSample(now, "system.processes", nil),
			missingMetricSample(now, "system.processes_running", nil),
		)
		return samples
	}
	running, total, ok := parseProcessCounts(loadData)
	if !ok {
		samples = append(samples,
			missingMetricSample(now, "system.processes", nil),
			missingMetricSample(now, "system.processes_running", nil),
		)
		return samples
	}
	samples = append(samples,
		validMetricSample(now, "system.processes", nil, float64(total)),
		validMetricSample(now, "system.processes_running", nil, float64(running)),
	)
	return samples
}

func parseUptime(data []byte) (float64, bool) {
	fields := strings.Fields(string(data))
	if len(fields) == 0 {
		return 0, false
	}
	value, err := strconv.ParseFloat(fields[0], 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 {
		return 0, false
	}
	return value, true
}

func parseProcessCounts(data []byte) (uint64, uint64, bool) {
	fields := strings.Fields(string(data))
	if len(fields) < 4 {
		return 0, 0, false
	}
	parts := strings.SplitN(fields[3], "/", 2)
	if len(parts) != 2 {
		return 0, 0, false
	}
	running, err := strconv.ParseUint(parts[0], 10, 64)
	if err != nil {
		return 0, 0, false
	}
	total, err := strconv.ParseUint(parts[1], 10, 64)
	if err != nil {
		return 0, 0, false
	}
	return running, total, true
}

// temperatureSamples 读取第一个有效的 thermal_zone 温度。
// 热区数量和读取次数均受限，避免无传感器或异常驱动拖慢采集周期。
func (c *metricsCollector) temperatureSamples(now time.Time) []protocol.NodeMetricSample {
	dir := c.sysPath(filepath.Join("class", "thermal"))
	entries, err := os.ReadDir(dir)
	if err != nil {
		return []protocol.NodeMetricSample{missingMetricSample(now, "system.temperature_c", nil)}
	}
	checked := 0
	for _, entry := range entries {
		if checked >= maxThermalZones {
			break
		}
		name := entry.Name()
		if !strings.HasPrefix(name, "thermal_zone") {
			continue
		}
		checked++
		data, err := c.readPath(filepath.Join(dir, name, "temp"))
		if err != nil {
			continue
		}
		value, ok := parseTemperature(data)
		if !ok {
			continue
		}
		return []protocol.NodeMetricSample{validMetricSample(now, "system.temperature_c", nil, value)}
	}
	return []protocol.NodeMetricSample{missingMetricSample(now, "system.temperature_c", nil)}
}

// parseTemperature 将 thermal_zone 的毫摄氏度转换为摄氏度。
// 0 及明显越界的值视为传感器不可用，避免把占位值当成真实温度。
func parseTemperature(data []byte) (float64, bool) {
	fields := strings.Fields(string(data))
	if len(fields) == 0 {
		return 0, false
	}
	raw, err := strconv.ParseFloat(fields[0], 64)
	if err != nil || math.IsNaN(raw) || math.IsInf(raw, 0) {
		return 0, false
	}
	celsius := raw / 1000
	if celsius <= 0 || celsius > 200 {
		return 0, false
	}
	return celsius, true
}

// fanSamples 读取 /sys/class/hwmon/hwmon*/fan*_input。没有风扇时直接跳过，
// 不为未知设备伪造 0 转速。
func (c *metricsCollector) fanSamples(now time.Time) []protocol.NodeMetricSample {
	base := c.sysPath(filepath.Join("class", "hwmon"))
	chips, err := os.ReadDir(base)
	if err != nil {
		return nil
	}
	samples := make([]protocol.NodeMetricSample, 0, 4)
	attempts := 0
	for _, chip := range chips {
		if attempts >= maxFanSensors {
			break
		}
		chipName := chip.Name()
		if !strings.HasPrefix(chipName, "hwmon") {
			continue
		}
		chipDir := filepath.Join(base, chipName)
		files, err := os.ReadDir(chipDir)
		if err != nil {
			continue
		}
		for _, file := range files {
			if attempts >= maxFanSensors {
				break
			}
			name := file.Name()
			if !strings.HasPrefix(name, "fan") || !strings.HasSuffix(name, "_input") {
				continue
			}
			attempts++
			data, err := c.readPath(filepath.Join(chipDir, name))
			if err != nil {
				continue
			}
			rpm, ok := parseFanRPM(data)
			if !ok {
				continue
			}
			fan := strings.TrimSuffix(name, "_input")
			samples = append(samples, validMetricSample(now, "system.fan_rpm", map[string]string{
				"fan": chipName + "/" + fan,
			}, rpm))
		}
	}
	return samples
}

func parseFanRPM(data []byte) (float64, bool) {
	fields := strings.Fields(string(data))
	if len(fields) == 0 {
		return 0, false
	}
	value, err := strconv.ParseFloat(fields[0], 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) || value < 0 || value > 100000 {
		return 0, false
	}
	return value, true
}

func validMetricSample(now time.Time, metric string, dimensions map[string]string, value float64) protocol.NodeMetricSample {
	if rounded := metricValue(value); rounded != nil {
		return protocol.NodeMetricSample{
			Timestamp:  now.UTC(),
			Metric:     metric,
			Dimensions: dimensions,
			Value:      rounded,
			Quality:    protocol.NodeMetricQualityValid,
		}
	}
	return missingMetricSample(now, metric, dimensions)
}

func missingMetricSample(now time.Time, metric string, dimensions map[string]string) protocol.NodeMetricSample {
	return protocol.NodeMetricSample{
		Timestamp:  now.UTC(),
		Metric:     metric,
		Dimensions: dimensions,
		Value:      nil,
		Quality:    protocol.NodeMetricQualityMissing,
	}
}

func missingMetricSamples(now time.Time, metrics []string) []protocol.NodeMetricSample {
	samples := make([]protocol.NodeMetricSample, 0, len(metrics))
	for _, metric := range metrics {
		samples = append(samples, missingMetricSample(now, metric, nil))
	}
	return samples
}

// metricValue 保留两位小数，并拒绝 NaN/Inf，避免生成无效 JSON 数值。
func metricValue(value float64) *float64 {
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return nil
	}
	rounded := math.Round(value*100) / 100
	return &rounded
}

func clampPercent(value float64) float64 {
	if value < 0 {
		return 0
	}
	if value > 100 {
		return 100
	}
	return value
}
