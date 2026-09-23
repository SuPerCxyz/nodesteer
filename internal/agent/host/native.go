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

// NativeHostAdapter 原生宿主
type NativeHostAdapter struct{}

// NewNativeHostAdapter 创建原生适配器
func NewNativeHostAdapter() *NativeHostAdapter { return &NativeHostAdapter{} }

func (a *NativeHostAdapter) HostRoot() string        { return "/" }
func (a *NativeHostAdapter) MapPath(p string) string { return p }

func (a *NativeHostAdapter) ValidatePath(p string) error {
	if p == "" || !filepath.IsAbs(p) || strings.ContainsRune(p, 0) || filepath.Clean(p) != p {
		return fmt.Errorf("invalid absolute path: %s", p)
	}
	return nil
}

func (a *NativeHostAdapter) WriteFile(ctx context.Context, p string, data []byte, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	return os.WriteFile(p, data, mode)
}

func (a *NativeHostAdapter) AtomicReplace(ctx context.Context, p string, data []byte, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	if existing, err := os.Lstat(p); err == nil && existing.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("destination is a symlink: %s", p)
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(p), ".tmp-*")
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
	return os.Rename(tmpName, p)
}

func (a *NativeHostAdapter) Chmod(ctx context.Context, p string, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	return os.Chmod(p, mode)
}

func (a *NativeHostAdapter) Chown(ctx context.Context, p, user, group string) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	return chown(p, user, group)
}

func (a *NativeHostAdapter) MkdirAll(ctx context.Context, p string, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	return os.MkdirAll(p, mode)
}

func (a *NativeHostAdapter) Remove(ctx context.Context, p string) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	return os.Remove(p)
}

func (a *NativeHostAdapter) ReadFile(ctx context.Context, p string) ([]byte, error) {
	if err := a.ValidatePath(p); err != nil {
		return nil, err
	}
	return os.ReadFile(p)
}

func (a *NativeHostAdapter) OpenRead(ctx context.Context, p string) (io.ReadCloser, os.FileInfo, error) {
	if err := a.ValidatePath(p); err != nil {
		return nil, nil, err
	}
	f, err := os.Open(p)
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

func (a *NativeHostAdapter) AtomicReplaceReader(ctx context.Context, p string, r io.Reader, mode os.FileMode) error {
	if err := a.ValidatePath(p); err != nil {
		return err
	}
	if existing, err := os.Lstat(p); err == nil && existing.Mode()&os.ModeSymlink != 0 {
		return fmt.Errorf("destination is a symlink: %s", p)
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(p), ".nodesteer-transfer-*")
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
	return os.Rename(tmpName, p)
}

func (a *NativeHostAdapter) Stat(ctx context.Context, p string) (os.FileInfo, error) {
	if err := a.ValidatePath(p); err != nil {
		return nil, err
	}
	return os.Stat(p)
}

func (a *NativeHostAdapter) systemdCtx(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(ctx, 60*1000_000_000/1000) // 60s
}

func (a *NativeHostAdapter) systemctl(ctx context.Context, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, "systemctl", args...)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func (a *NativeHostAdapter) InstallUnit(ctx context.Context, unitName, content string) error {
	p := filepath.Join("/etc/systemd/system", ensureUnitSuffix(unitName))
	if err := a.WriteFile(ctx, p, []byte(content), 0o644); err != nil {
		return err
	}
	_, err := a.systemctl(ctx, "daemon-reload")
	return err
}

// ensureUnitSuffix 确保 unit 名称以 .service 结尾
func ensureUnitSuffix(name string) string {
	if !strings.HasSuffix(name, ".service") {
		return name + ".service"
	}
	return name
}

func (a *NativeHostAdapter) UpdateUnit(ctx context.Context, unitName, content string) error {
	return a.InstallUnit(ctx, unitName, content)
}

func (a *NativeHostAdapter) DaemonReload(ctx context.Context) error {
	_, err := a.systemctl(ctx, "daemon-reload")
	return err
}

func (a *NativeHostAdapter) EnableService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "enable", unitName)
	return err
}

func (a *NativeHostAdapter) DisableService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "disable", unitName)
	return err
}

func (a *NativeHostAdapter) StartService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "start", unitName)
	return err
}

func (a *NativeHostAdapter) StopService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "stop", unitName)
	return err
}

func (a *NativeHostAdapter) RestartService(ctx context.Context, unitName string) error {
	_, err := a.systemctl(ctx, "restart", unitName)
	return err
}

func (a *NativeHostAdapter) ServiceStatus(ctx context.Context, unitName string) (string, error) {
	out, err := a.systemctl(ctx, "is-active", unitName)
	return out, err
}

func (a *NativeHostAdapter) RunCommand(ctx context.Context, name string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func (a *NativeHostAdapter) RunCommandWithEnv(ctx context.Context, name string, env []string, args ...string) (string, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = append(os.Environ(), env...)
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// chown 使用 os.Chown（需 root）
func chown(p, user, group string) error {
	// 解析 user/group 需要 os/user；简化：通过 exec chown
	if user == "" && group == "" {
		return nil
	}
	arg := user
	if group != "" {
		arg = fmt.Sprintf("%s:%s", user, group)
	}
	cmd := exec.Command("chown", arg, p)
	return cmd.Run()
}
