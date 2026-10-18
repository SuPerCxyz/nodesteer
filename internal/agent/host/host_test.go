package host

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNativeAtomicReplace(t *testing.T) {
	a := NewNativeHostAdapter()
	ctx := context.Background()
	dir := t.TempDir()
	p := filepath.Join(dir, "bin", "test-app")

	if err := a.AtomicReplace(ctx, p, []byte("#!/bin/sh\necho hi\n"), 0o755); err != nil {
		t.Fatalf("atomic replace: %v", err)
	}
	data, err := a.ReadFile(ctx, p)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if string(data) != "#!/bin/sh\necho hi\n" {
		t.Fatalf("content mismatch")
	}
	fi, err := os.Stat(a.MapPath(p))
	if err != nil {
		t.Fatalf("stat: %v", err)
	}
	if fi.Mode().Perm()&0o111 == 0 {
		t.Fatalf("expected executable bit")
	}
}

func TestContainerPathMapping(t *testing.T) {
	a := NewContainerHostAdapter("/host")
	if a.HostRoot() != "/host" {
		t.Fatalf("host root mismatch")
	}
	if got := a.MapPath("/usr/local/bin/foo"); got != "/host/usr/local/bin/foo" {
		t.Fatalf("map path mismatch: %s", got)
	}
	// 防 ../ escape
	if got := a.MapPath("../../etc/passwd"); got != "/host/etc/passwd" {
		t.Fatalf("path escape not prevented: %s", got)
	}
	// 空路径
	if got := a.MapPath(""); got != "/host" {
		t.Fatalf("empty path: %s", got)
	}
}

func TestValidatePathEscape(t *testing.T) {
	a := NewContainerHostAdapter("/host")
	// 正常路径
	if err := a.ValidatePath("/usr/local/bin/foo"); err != nil {
		t.Fatalf("valid path rejected: %v", err)
	}
	// 逃逸路径应被拒绝
	for _, p := range []string{"../../etc/passwd", "/../../etc/passwd", "/usr/../etc/passwd"} {
		if err := a.ValidatePath(p); err == nil {
			t.Errorf("expected path to be rejected: %s", p)
		}
	}
}

func TestContainerAtomicReplace(t *testing.T) {
	// 用临时目录模拟 hostRoot
	tmpRoot := t.TempDir()
	a := NewContainerHostAdapter(tmpRoot)
	a.hostView = tmpRoot // 同一临时目录代表真实宿主视角。
	ctx := context.Background()
	logical := "/usr/local/bin/test-app"

	if err := a.AtomicReplace(ctx, logical, []byte("data"), 0o644); err != nil {
		t.Fatalf("atomic replace: %v", err)
	}
	// 映射后文件存在
	mapped := filepath.Join(tmpRoot, "usr", "local", "bin", "test-app")
	if _, err := os.Stat(mapped); err != nil {
		t.Fatalf("mapped file missing: %v", err)
	}
	data, err := a.ReadFile(ctx, logical)
	if err != nil {
		t.Fatalf("read via adapter: %v", err)
	}
	if string(data) != "data" {
		t.Fatalf("content mismatch")
	}
}

func TestContainerAllowlist(t *testing.T) {
	a := NewContainerHostAdapter(t.TempDir())
	if err := a.ValidatePath("/home/user/app"); err == nil {
		t.Fatal("path outside allowlist should be rejected")
	}
	if err := a.ValidatePath("/etc/systemd/system/app.service"); err != nil {
		t.Fatalf("managed unit path rejected: %v", err)
	}
}

func TestContainerMappingMustMatchActualHost(t *testing.T) {
	for _, name := range []string{"correct", "missing", "wrong", "unavailable"} {
		t.Run(name, func(t *testing.T) {
			mappedRoot := t.TempDir()
			actualRoot := mappedRoot
			if name != "correct" {
				actualRoot = t.TempDir()
			}
			for _, root := range []string{mappedRoot, actualRoot} {
				if err := os.MkdirAll(filepath.Join(root, "opt", "owned"), 0o755); err != nil {
					t.Fatal(err)
				}
			}
			if name == "missing" {
				os.RemoveAll(filepath.Join(mappedRoot, "opt"))
			}
			if name == "unavailable" {
				actualRoot = filepath.Join(actualRoot, "absent-view")
			}
			a := NewContainerHostAdapter(mappedRoot)
			a.hostView = actualRoot
			logical := "/opt/owned/new/file"
			err := a.AtomicReplaceReader(t.Context(), logical, strings.NewReader("new payload"), 0o644)
			if name == "correct" {
				if err != nil {
					t.Fatal(err)
				}
				if got, err := os.ReadFile(filepath.Join(actualRoot, logical)); err != nil || string(got) != "new payload" {
					t.Fatalf("host did not receive file: %q %v", got, err)
				}
			} else {
				if err == nil {
					t.Fatal("unproven mapping accepted")
				}
				for _, root := range []string{mappedRoot, actualRoot} {
					if _, err := os.Stat(filepath.Join(root, "opt/owned/new")); !os.IsNotExist(err) {
						t.Fatalf("invalid mapping created directory: %s %v", root, err)
					}
				}
			}
		})
	}
}

func TestContainerCommandsAttachNsenterRoot(t *testing.T) {
	bin := t.TempDir()
	argsFile := filepath.Join(t.TempDir(), "arguments")
	script := "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$NODESTEER_TEST_ARGS\"\nprintf active\n"
	if err := os.WriteFile(filepath.Join(bin, "nsenter"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("NODESTEER_TEST_ARGS", argsFile)
	a := NewContainerHostAdapter("/host")
	for _, run := range []func() (string, error){
		func() (string, error) { return a.ServiceStatus(t.Context(), "owned.service") },
		func() (string, error) { return a.RunCommand(t.Context(), "true") },
		func() (string, error) { return a.RunCommandWithEnv(t.Context(), "true", []string{"OWNED_TEST=1"}) },
	} {
		if _, err := run(); err != nil {
			t.Fatal(err)
		}
		args, err := os.ReadFile(argsFile)
		if err != nil || !strings.Contains(string(args), "--root=/proc/1/root\n") || strings.Contains(string(args), "-r\n/proc/1/root\n") {
			t.Fatalf("root argument not attached: %q %v", args, err)
		}
	}
}
