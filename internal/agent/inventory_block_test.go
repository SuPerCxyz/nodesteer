package agent

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func TestReadBlockDevicesHotplug(t *testing.T) {
	root := t.TempDir()
	makeDir := func(path string) {
		t.Helper()
		if err := os.MkdirAll(filepath.Join(root, path), 0755); err != nil {
			t.Fatal(err)
		}
	}
	for _, name := range []string{"vda", "vda2", "nvme0n1", "nvme0n1p1", "dm-0", "md0", "missing"} {
		makeDir("sys/class/block/" + name)
	}
	for _, name := range []string{"vda", "nvme0n1", "dm-0", "md0"} {
		makeDir("sys/block/" + name)
	}
	for _, name := range []string{"vda2", "nvme0n1p1"} {
		if err := os.WriteFile(filepath.Join(root, "sys/class/block", name, "partition"), []byte("1"), 0644); err != nil {
			t.Fatal(err)
		}
	}
	check := func(want map[string]string) {
		t.Helper()
		got := map[string]string{}
		for _, d := range readBlockDevices(root) {
			got[d.Name] = d.Kind
		}
		for name, kind := range want {
			if got[name] != kind {
				t.Errorf("%s = %q, want %q", name, got[name], kind)
			}
		}
	}
	check(map[string]string{"vda": "disk", "vda2": "partition", "nvme0n1": "disk", "nvme0n1p1": "partition", "dm-0": "disk", "md0": "disk", "missing": "unknown"})
	makeDir("sys/class/block/newdisk")
	makeDir("sys/block/newdisk")
	check(map[string]string{"newdisk": "disk"})
	if readBlockDevices(t.TempDir()) != nil {
		t.Fatal("missing sysfs must be unknown")
	}
}

func TestInventoryBlockJSONCompatibility(t *testing.T) {
	old := []byte(`{"os":"linux"}`)
	var legacy protocol.Inventory
	if err := json.Unmarshal(old, &legacy); err != nil || legacy.BlockDevices != nil {
		t.Fatalf("old inventory: %+v %v", legacy, err)
	}
	original := &models.Inventory{Hostname: "actual-host", BlockDevices: []models.BlockDeviceInfo{{Name: "dm-0", Kind: "disk"}, {Name: "vda2", Kind: "partition"}}}
	payload := convertToProtocolInventory(original)
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	var roundtrip protocol.Inventory
	if err := json.Unmarshal(data, &roundtrip); err != nil || len(roundtrip.BlockDevices) != 2 || roundtrip.Hostname != "actual-host" || roundtrip.BlockDevices[0].Name != "dm-0" || roundtrip.BlockDevices[1].Kind != "partition" {
		t.Fatalf("roundtrip: %+v %v", roundtrip, err)
	}
}

func TestHostIntegrationInventoryUsesHostRoot(t *testing.T) {
	root := t.TempDir()
	for _, dir := range []string{"sys/class/block/dm-0", "sys/block/dm-0"} {
		if err := os.MkdirAll(filepath.Join(root, dir), 0755); err != nil {
			t.Fatal(err)
		}
	}
	inv := collectInventory(models.DeploymentModeDockerHostInt, false, root)
	if inv == nil || len(inv.BlockDevices) != 1 || inv.BlockDevices[0].Name != "dm-0" || inv.BlockDevices[0].Kind != "disk" {
		t.Fatalf("host-root inventory: %+v", inv)
	}
}

func TestHostNameReadsOnlyHostRoot(t *testing.T) {
	root := t.TempDir()
	path := filepath.Join(root, "proc/sys/kernel")
	if err := os.MkdirAll(path, 0755); err != nil {
		t.Fatal(err)
	}
	if got := readHostName(root); got != "" {
		t.Fatalf("missing hostname = %q", got)
	}
	if err := os.WriteFile(filepath.Join(path, "hostname"), []byte("real-host\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if got := readHostName(root); got != "real-host" {
		t.Fatalf("hostname = %q", got)
	}
}

func TestReadBlockDevicesRelativeSysfsLinks(t *testing.T) {
	root := t.TempDir()
	dirs := []string{"sys/class/block", "sys/block", "sys/devices/virtual/block/dm-0", "sys/devices/virtual/block/dm-0/dm-0p1"}
	for _, dir := range dirs {
		if err := os.MkdirAll(filepath.Join(root, dir), 0755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(root, "sys/devices/virtual/block/dm-0/dm-0p1/partition"), []byte("1"), 0644); err != nil {
		t.Fatal(err)
	}
	for name, target := range map[string]string{"dm-0": "../../devices/virtual/block/dm-0", "dm-0p1": "../../devices/virtual/block/dm-0/dm-0p1", "broken": "../../devices/missing"} {
		if err := os.Symlink(target, filepath.Join(root, "sys/class/block", name)); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Symlink("../devices/virtual/block/dm-0", filepath.Join(root, "sys/block/dm-0")); err != nil {
		t.Fatal(err)
	}
	found := map[string]string{}
	for _, d := range readBlockDevices(root) {
		found[d.Name] = d.Kind
	}
	for name, want := range map[string]string{"dm-0": "disk", "dm-0p1": "partition", "broken": "unknown"} {
		if found[name] != want {
			t.Errorf("%s = %q, want %q", name, found[name], want)
		}
	}
}

func TestReadBlockDevicesNativeUsesAbsoluteSysfs(t *testing.T) {
	if _, err := os.Stat("/sys/class/block"); err != nil {
		t.Skip("sysfs unavailable")
	}
	expected, err := os.ReadDir("/sys/class/block")
	if err != nil {
		t.Skip("sysfs unreadable")
	}
	got := readBlockDevices("")
	if len(got) != len(expected) {
		t.Fatalf("native sysfs devices = %d, want %d", len(got), len(expected))
	}
}
