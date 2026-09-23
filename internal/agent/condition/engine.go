package condition

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// StateProvider 状态提供抽象（便于 Native/Docker 统一）
type StateProvider interface {
	CPUUsage() (float64, error)
	MemoryUsage() (float64, error)
	DiskUsage(mount string) (float64, error)
	FileExists(path string) (bool, error)
	DirExists(path string) (bool, error)
	ProcessExists(process string) (bool, error)
	PortListening(port string) (bool, error)
	CommandResult(command string) (string, error)
}

// OSProvider 使用 /proc 实现的 StateProvider
type OSProvider struct{}

func (OSProvider) CPUUsage() (float64, error) {
	// 简化：读取 /proc/stat 两次计算
	return readCPU()
}

func (OSProvider) MemoryUsage() (float64, error) {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0, err
	}
	lines := strings.Split(string(data), "\n")
	var total, avail int64
	for _, line := range lines {
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		v, err := strconv.ParseInt(parts[1], 10, 64)
		if err != nil {
			continue
		}
		switch {
		case strings.HasPrefix(parts[0], "MemTotal"):
			total = v
		case strings.HasPrefix(parts[0], "MemAvailable"):
			avail = v
		}
	}
	if total == 0 {
		return 0, nil
	}
	return float64(total-avail) / float64(total) * 100, nil
}

func (OSProvider) DiskUsage(mount string) (float64, error) {
	var st syscall.Statfs_t
	if err := syscall.Statfs(mount, &st); err != nil {
		return 0, err
	}
	total := st.Blocks * uint64(st.Bsize)
	free := st.Bavail * uint64(st.Bsize)
	if total == 0 {
		return 0, nil
	}
	return float64(total-free) / float64(total) * 100, nil
}

func (OSProvider) FileExists(path string) (bool, error) {
	_, err := os.Stat(path)
	return err == nil, nil
}

func (OSProvider) DirExists(path string) (bool, error) {
	fi, err := os.Stat(path)
	if err != nil {
		return false, nil
	}
	return fi.IsDir(), nil
}

func (OSProvider) ProcessExists(process string) (bool, error) {
	// 通过 pgrep 判断
	return commandExists("pgrep", "-x", process)
}

func (OSProvider) PortListening(port string) (bool, error) {
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 {
		return false, fmt.Errorf("invalid port: %s", port)
	}
	return commandExists("sh", "-c", "ss -ltn | grep -q ':"+port+" '")
}

func (OSProvider) CommandResult(command string) (string, error) {
	out, err := runShell(command)
	return strings.TrimSpace(out), err
}

func commandExists(name string, args ...string) (bool, error) {
	_, err := runShellCommand(name, args...)
	return err == nil, nil
}

// Engine 条件引擎
type Engine struct {
	provider StateProvider
	// Remote 远程状态查询（由 Agent 注入）
	Remote func(ctx context.Context, rc *models.RemoteCondition) (string, bool, error)
}

// New 创建条件引擎
func New(provider StateProvider) *Engine {
	return &Engine{provider: provider}
}

// SetProvider 替换 Provider
func (e *Engine) SetProvider(p StateProvider) { e.provider = p }

// Evaluate 评估条件；returns (satisfied, evaluated, error)
func (e *Engine) Evaluate(ctx context.Context, c *models.Condition) (bool, bool, error) {
	if c == nil {
		return true, true, nil
	}
	switch c.Type {
	case "and":
		for i := range c.And {
			ok, ev, err := e.Evaluate(ctx, &c.And[i])
			if err != nil {
				return false, ev, err
			}
			if !ev {
				// AND 中任一子条件未知，整体必须保持 UNKNOWN，不能被当成
				// 已评估的 SKIPPED/通过，否则会绕过 Fail Closed。
				return false, false, nil
			}
			if !ok {
				return false, true, nil
			}
		}
		return true, true, nil
	case "remote":
		if e.Remote == nil {
			return false, false, nil
		}
		val, ok, err := e.Remote(ctx, c.Remote)
		if err != nil || !ok {
			// 未知 → Fail Closed
			return false, false, err
		}
		expected := c.Remote.Value
		if c.Remote.Property == "online" {
			val, expected = strings.ToLower(val), strings.ToLower(expected)
		}
		return compare(val, c.Remote.Operator, expected), true, nil
	case "local":
		return e.evalLocal(ctx, c.Local)
	default:
		return false, false, nil
	}
}

func (e *Engine) evalLocal(ctx context.Context, lc *models.LocalCondition) (bool, bool, error) {
	if lc == nil || e.provider == nil {
		return false, false, nil
	}
	var actual string
	switch lc.Metric {
	case "cpu_usage":
		v, err := e.provider.CPUUsage()
		if err != nil {
			return false, false, err
		}
		actual = strconv.FormatFloat(v, 'f', 2, 64)
	case "memory_usage":
		v, err := e.provider.MemoryUsage()
		if err != nil {
			return false, false, err
		}
		actual = strconv.FormatFloat(v, 'f', 2, 64)
	case "disk_usage":
		v, err := e.provider.DiskUsage(lc.Path)
		if err != nil {
			return false, false, err
		}
		actual = strconv.FormatFloat(v, 'f', 2, 64)
	case "file_exists":
		b, err := e.provider.FileExists(lc.Path)
		if err != nil {
			return false, false, err
		}
		actual = strconv.FormatBool(b)
	case "dir_exists":
		b, err := e.provider.DirExists(lc.Path)
		if err != nil {
			return false, false, err
		}
		actual = strconv.FormatBool(b)
	case "process_exists":
		b, err := e.provider.ProcessExists(lc.Path)
		if err != nil {
			return false, false, err
		}
		actual = strconv.FormatBool(b)
	case "port_listening":
		b, err := e.provider.PortListening(lc.Path)
		if err != nil {
			return false, false, err
		}
		actual = strconv.FormatBool(b)
	case "command_result":
		s, err := e.provider.CommandResult(lc.Command)
		if err != nil {
			return false, false, err
		}
		actual = s
	default:
		return true, false, nil
	}
	return compare(actual, lc.Operator, lc.Value), true, nil
}

// compare 比较运算符
func compare(actual, op, expected string) bool {
	switch op {
	case "==":
		return actual == expected
	case "!=":
		return actual != expected
	case ">":
		return numericCompare(actual, expected) > 0
	case "<":
		return numericCompare(actual, expected) < 0
	case ">=":
		return numericCompare(actual, expected) >= 0
	case "<=":
		return numericCompare(actual, expected) <= 0
	}
	return false
}

func numericCompare(a, b string) int {
	af, aerr := strconv.ParseFloat(a, 64)
	bf, berr := strconv.ParseFloat(b, 64)
	if aerr == nil && berr == nil {
		switch {
		case af < bf:
			return -1
		case af > bf:
			return 1
		default:
			return 0
		}
	}
	return strings.Compare(a, b)
}

// readCPU 读取 CPU 使用率（两次采样）
func readCPU() (float64, error) {
	s1, err := readCPUTicks()
	if err != nil {
		return 0, err
	}
	time.Sleep(200 * time.Millisecond)
	s2, err := readCPUTicks()
	if err != nil {
		return 0, err
	}
	idle := s2.idle - s1.idle
	total := s2.total - s1.total
	if total == 0 {
		return 0, nil
	}
	return float64(total-idle) / float64(total) * 100, nil
}

type cpuTicks struct{ idle, total uint64 }

func readCPUTicks() (cpuTicks, error) {
	data, err := os.ReadFile("/proc/stat")
	if err != nil {
		return cpuTicks{}, err
	}
	lines := strings.Split(string(data), "\n")
	if len(lines) == 0 {
		return cpuTicks{}, nil
	}
	fields := strings.Fields(lines[0])
	if len(fields) < 5 || fields[0] != "cpu" {
		return cpuTicks{}, nil
	}
	var ct cpuTicks
	for i := 1; i < len(fields); i++ {
		v, err := strconv.ParseUint(fields[i], 10, 64)
		if err != nil {
			continue
		}
		ct.total += v
		if i == 4 {
			ct.idle = v
		}
	}
	return ct, nil
}
