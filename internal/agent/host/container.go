package host

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// ContainerHostAdapter Docker Host Integration 适配器
// HOST_ROOT=/host，逻辑路径映射为 /host/<path>
type ContainerHostAdapter struct {
	hostRoot        string
	allowedPrefixes []string
}

// NewContainerHostAdapter 创建容器宿主适配器
func NewContainerHostAdapter(hostRoot string) *ContainerHostAdapter {
	return NewContainerHostAdapterWithAllowlist(hostRoot, nil)
}

// NewContainerHostAdapterWithAllowlist 创建带宿主路径白名单的容器适配器。
func NewContainerHostAdapterWithAllowlist(hostRoot string, allowed []string) *ContainerHostAdapter {
	if hostRoot == "" {
		hostRoot = "/host"
	}
	if len(allowed) == 0 {
		allowed = []string{"/etc", "/opt", "/usr/local/bin", "/var/lib"}
	}
	return &ContainerHostAdapter{hostRoot: hostRoot, allowedPrefixes: allowed}
}

func (a *ContainerHostAdapter) HostRoot() string { return a.hostRoot }

// MapPath 映射逻辑路径，并做 path clean 与 escape 防护
func (a *ContainerHostAdapter) MapPath(logicalPath string) string {
	if logicalPath == "" {
		return a.hostRoot
	}
	clean := filepath.Clean("/" + strings.TrimPrefix(logicalPath, "/"))
	return filepath.Join(a.hostRoot, clean)
}

// ValidatePath 校验逻辑路径不逃逸 hostRoot（防 ../ 与 symlink escape 前置检查）
func (a *ContainerHostAdapter) ValidatePath(logicalPath string) error {
	for _, part := range strings.Split(strings.ReplaceAll(logicalPath, "\\", "/"), "/") {
		if part == ".." {
			return fmt.Errorf("path escape detected: %s", logicalPath)
		}
	}
	clean := filepath.Clean("/" + strings.TrimPrefix(logicalPath, "/"))
	if clean == "/" || strings.HasPrefix(clean, "/../") || strings.Contains(clean, "/../") {
		return fmt.Errorf("path escape detected: %s", logicalPath)
	}
	allowed := false
	for _, prefix := range a.allowedPrefixes {
		prefix = filepath.Clean("/" + strings.TrimPrefix(prefix, "/"))
		if clean == prefix || strings.HasPrefix(clean, prefix+string(filepath.Separator)) {
			allowed = true
			break
		}
	}
	if !allowed {
		return fmt.Errorf("path is outside host allowlist: %s", logicalPath)
	}
	// 拒绝绝对路径穿越 hostRoot 的软链接
	mapped := a.MapPath(logicalPath)
	if resolved, err := filepath.EvalSymlinks(a.hostRoot); err == nil {
		candidate := mapped
		for {
			if _, statErr := os.Lstat(candidate); statErr == nil {
				break
			}
			parent := filepath.Dir(candidate)
			if parent == candidate {
				break
			}
			candidate = parent
		}
		if evaluated, evalErr := filepath.EvalSymlinks(candidate); evalErr == nil {
			if rel, err := filepath.Rel(resolved, evaluated); err == nil {
				if rel == ".." || strings.HasPrefix(rel, "../") {
					return fmt.Errorf("path escapes host root: %s", logicalPath)
				}
			}
		}
		if rel, err := filepath.Rel(resolved, mapped); err == nil {
			if rel == ".." || strings.HasPrefix(rel, "../") {
				return fmt.Errorf("path escapes host root: %s", logicalPath)
			}
		}
	}
	return nil
}

func (a *ContainerHostAdapter) WriteFile(ctx context.Context, p string, data []byte, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	mapped := a.MapPath(p)
	if err := os.MkdirAll(filepath.Dir(mapped), 0o755); err != nil {
		return err
	}
	return os.WriteFile(mapped, data, mode)
}

func (a *ContainerHostAdapter) AtomicReplace(ctx context.Context, p string, data []byte, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	mapped := a.MapPath(p)
	dir := filepath.Dir(mapped)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := tmp.Write(data); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Chmod(tmpName, mode); err != nil {
		return err
	}
	return os.Rename(tmpName, mapped)
}

func (a *ContainerHostAdapter) Chmod(ctx context.Context, p string, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	return os.Chmod(a.MapPath(p), mode)
}

func (a *ContainerHostAdapter) Chown(ctx context.Context, p, user, group string) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	return chown(a.MapPath(p), user, group)
}

func (a *ContainerHostAdapter) MkdirAll(ctx context.Context, p string, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	return os.MkdirAll(a.MapPath(p), mode)
}

func (a *ContainerHostAdapter) Remove(ctx context.Context, p string) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	return os.Remove(a.MapPath(p))
}

func (a *ContainerHostAdapter) ReadFile(ctx context.Context, p string) ([]byte, error) {
	if err := a.ValidatePath(p); err != nil {
		return nil, err
	}
	return os.ReadFile(a.MapPath(p))
}

func (a *ContainerHostAdapter) OpenRead(ctx context.Context, p string) (io.ReadCloser, os.FileInfo, error) {
	if err := a.ValidatePath(p); err != nil {
		return nil, nil, err
	}
	f, err := os.Open(a.MapPath(p))
	if err != nil {
		return nil, nil, err
	}
	info, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, nil, err
	}
	return f, info, nil
}

func (a *ContainerHostAdapter) AtomicReplaceReader(ctx context.Context, p string, r io.Reader, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	mapped := a.MapPath(p)
	if existing, err := os.Lstat(mapped); err == nil && existing.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("destination is a symlink: %s", p)
	}
	dir := filepath.Dir(mapped)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".nodesteer-transfer-*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	if _, err := io.Copy(tmp, r); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Chmod(mode); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmpName, mapped)
}

func (a *ContainerHostAdapter) Stat(ctx context.Context, p string) (os.FileInfo, error) {
	if err := a.ValidatePath(p); err != nil {
		return nil, err
	}
	return os.Stat(a.MapPath(p))
}

func (a *ContainerHostAdapter) systemctl(ctx context.Context, args ...string) (string, error) {
	// 通过 nsenter/systemctl 访问宿主 systemd
	// 推荐：nsenter -t 1 -m -u -i -n -p systemctl
	nsenterArgs := append([]string{"-t", "1", "-m", "-u", "-i", "-n", "-p", "-r", "/proc/1/root", "systemctl"}, args...)
	cmd := exec.CommandContext(ctx, "nsenter", nsenterArgs...)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func (a *ContainerHostAdapter) InstallUnit(ctx context.Context, unitName, content string) error {
	p := filepath.Join("/etc/systemd/system", ensureUnitSuffix(unitName))
	if err := a.WriteFile(ctx, p, []byte(content), 0o644); err != nil {
		return err
	}
	_, err := a.systemctl(ctx, "daemon-reload")
	return err
}

func (a *ContainerHostAdapter) UpdateUnit(ctx context.Context, unitName, content string) error {
	return a.InstallUnit(ctx, unitName, content)
}

func (a *ContainerHostAdapter) DaemonReload(ctx context.Context) error {
	_, err := a.systemctl(ctx, "daemon-reload")
	return err
}

func (a *ContainerHostAdapter) EnableService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "enable", unitName)
	return err
}

func (a *ContainerHostAdapter) DisableService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "disable", unitName)
	return err
}

func (a *ContainerHostAdapter) StartService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "start", unitName)
	return err
}

func (a *ContainerHostAdapter) StopService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "stop", unitName)
	return err
}

func (a *ContainerHostAdapter) RestartService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "restart", unitName)
	return err
}

func (a *ContainerHostAdapter) ServiceStatus(ctx context.Context, unitName string) (string, error) {
	out, err := a.systemctl(ctx, "is-active", unitName)
	return out, err
}

func (a *ContainerHostAdapter) RunCommand(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, "nsenter", append([]string{"-t", "1", "-m", "-u", "-i", "-n", "-p", "-r", "/proc/1/root"}, append([]string{name}, args...)...)...)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func (a *ContainerHostAdapter) RunCommandWithEnv(ctx context.Context, name string, env []string, args ...string) (string, error) {
	nsenterArgs := append([]string{"-t", "1", "-m", "-u", "-i", "-n", "-p", "-r", "/proc/1/root"}, name)
	nsenterArgs = append(nsenterArgs, args...)
	cmd := exec.CommandContext(ctx, "nsenter", nsenterArgs...)
	cmd.Env = append(os.Environ(), env...)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

var _ HostAdapter = (*NativeHostAdapter)(nil)
var _ HostAdapter = (*ContainerHostAdapter)(nil)

// DefaultHostAdapter 根据模式返回默认适配器
func DefaultHostAdapter(mode string) HostAdapter {
	if mode == "docker_host_integration" || mode == "container" {
		return NewContainerHostAdapter("/host")
	}
	return NewNativeHostAdapter()
}

var _ = fmt.Sprintf
