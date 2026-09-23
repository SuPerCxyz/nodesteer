package agent

import (
	"bufio"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// collectInventory 采集宿主 Inventory（Docker 无 Host 访问时返回 nil 表示 UNAVAILABLE）
// hostInt 为 true 且 mode=docker_host_integration 时读取 hostRoot（如 /host）下的宿主机信息
func collectInventory(mode string, hostInt bool, hostRoot string) *models.Inventory {
	// Docker 基础模式无法可靠访问 Host
	if mode == "docker" && !hostInt {
		return nil
	}
	root := ""
	if hostInt && hostRoot != "" {
		root = hostRoot
	}
	inv := &models.Inventory{
		OS:        readOS(root),
		OSVersion: readOSVersion(root),
		Kernel:    readKernel(root),
		Arch:      runtime.GOARCH,
		CPU:       readCPUInfo(root),
		Memory:    readMemInfo(root),
	}
	inv.Filesystem = readFilesystems(root)
	if root == "" {
		inv.Network = readNetworks()
	}
	return inv
}

func readOS(root string) string {
	fields := readOSRelease(root)
	if v, ok := fields["PRETTY_NAME"]; ok {
		return v
	}
	if v, ok := fields["NAME"]; ok {
		return v
	}
	return "linux"
}

// readOSVersion 读取 OS 版本（VERSION_ID，如 "22.04"）；无法可靠获取时返回 UNAVAILABLE
func readOSVersion(root string) string {
	fields := readOSRelease(root)
	if v, ok := fields["VERSION_ID"]; ok && v != "" {
		return v
	}
	if v, ok := fields["VERSION"]; ok && v != "" {
		return v
	}
	return "UNAVAILABLE"
}

func readOSRelease(root string) map[string]string {
	path := "/etc/os-release"
	if root != "" {
		path = root + path
	}
	fields := map[string]string{}
	f, err := os.Open(path)
	if err != nil {
		return fields
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		if i := strings.Index(line, "="); i > 0 {
			k := line[:i]
			v := strings.Trim(strings.Trim(line[i+1:], `"`), " \t")
			fields[k] = v
		}
	}
	return fields
}

func readKernel(root string) string {
	path := "/proc/sys/kernel/osrelease"
	if root != "" {
		path = root + path
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

func readCPUInfo(root string) []models.CPUInfo {
	path := "/proc/cpuinfo"
	if root != "" {
		path = root + path
	}
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	var out []models.CPUInfo
	model := ""
	cores := 0
	var mhz int64
	for sc.Scan() {
		line := sc.Text()
		if strings.HasPrefix(line, "model name") {
			if i := strings.Index(line, ":"); i > 0 {
				model = strings.TrimSpace(line[i+1:])
			}
		}
		if strings.HasPrefix(line, "cpu MHz") {
			if i := strings.Index(line, ":"); i > 0 {
				if v, err := strconv.ParseFloat(strings.TrimSpace(line[i+1:]), 64); err == nil {
					mhz = int64(v)
				}
			}
		}
		if strings.HasPrefix(line, "processor") {
			cores++
		}
	}
	if model != "" {
		out = append(out, models.CPUInfo{Model: model, Cores: cores, MHz: mhz})
	}
	return out
}

func readMemInfo(root string) *models.MemoryInfo {
	path := "/proc/meminfo"
	if root != "" {
		path = root + path
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	lines := strings.Split(string(data), "\n")
	var total, avail uint64
	for _, line := range lines {
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		v, err := strconv.ParseUint(parts[1], 10, 64)
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
		return nil
	}
	return &models.MemoryInfo{TotalKB: total, AvailableKB: avail}
}

func readFilesystems(root string) []models.FilesystemInfo {
	path := "/proc/mounts"
	if root != "" {
		path = root + path
	}
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	var out []models.FilesystemInfo
	seen := map[string]bool{}
	for sc.Scan() {
		parts := strings.Fields(sc.Text())
		if len(parts) < 3 {
			continue
		}
		dev, mount, fstype := parts[0], parts[1], parts[2]
		if strings.HasPrefix(fstype, "overlay") || strings.HasPrefix(mount, "/proc") ||
			strings.HasPrefix(mount, "/sys") || strings.HasPrefix(mount, "/dev") {
			continue
		}
		if seen[mount] {
			continue
		}
		seen[mount] = true
		var st statfsT
		statMount := mount
		if root != "" {
			statMount = filepath.Join(root, mount)
		}
		if statfs(statMount, &st) != nil {
			continue
		}
		out = append(out, models.FilesystemInfo{
			Mount: mount, Device: dev, FSType: fstype,
			TotalKB: st.Blocks * uint64(st.Bsize) / 1024,
			FreeKB:  st.Bavail * uint64(st.Bsize) / 1024,
		})
	}
	return out
}

func readNetworks() []models.NetworkInfo {
	ifaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	var out []models.NetworkInfo
	for _, ifc := range ifaces {
		if ifc.Flags&net.FlagLoopback != 0 || strings.HasPrefix(ifc.Name, "veth") ||
			strings.HasPrefix(ifc.Name, "docker") || strings.HasPrefix(ifc.Name, "br-") {
			continue
		}
		addrs, _ := ifc.Addrs()
		var addrList []string
		for _, a := range addrs {
			addrList = append(addrList, a.String())
		}
		out = append(out, models.NetworkInfo{
			Interface: ifc.Name, Addresses: addrList, MAC: ifc.HardwareAddr.String(),
		})
	}
	return out
}

func localIP() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	for _, ifc := range ifaces {
		if ifc.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, _ := ifc.Addrs()
		for _, addr := range addrs {
			ip, _, err := net.ParseCIDR(addr.String())
			if err == nil && ip.IsGlobalUnicast() {
				return ip.String()
			}
		}
	}
	return ""
}
