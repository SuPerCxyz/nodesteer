package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// oldUpgradeBinary 测试用「当前二进制」初始内容。
const oldUpgradeBinary = "#!/bin/sh\necho old-agent\n"

// newUpgradeTestAgent 构造 ready 态测试 Agent：可执行文件指向临时 fixture，
// 重启注入为记录型 no-op（防止任何用例误触真实 systemctl）。
func newUpgradeTestAgent(t *testing.T, mode string) (*Agent, string) {
	t.Helper()
	exePath := filepath.Join(t.TempDir(), "nodesteer-agent")
	if err := os.WriteFile(exePath, []byte(oldUpgradeBinary), 0o755); err != nil {
		t.Fatalf("write fixture binary: %v", err)
	}
	a, err := New(Config{
		HubURL:            "ws://127.0.0.1:8443",
		RegistrationToken: "test-token",
		NodeName:          "upgrade-test-node",
		DataDir:           t.TempDir(),
		DeploymentMode:    mode,
		AgentVersion:      "v0.0.1-old",
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatalf("new agent: %v", err)
	}
	t.Cleanup(func() { _ = a.Close() })
	a.ready = true
	a.upgrade.executable = func() (string, error) { return exePath, nil }
	a.upgrade.restart = func(ctx context.Context) error { return nil }
	return a, exePath
}

// triggerUpgrade 以 RUN_EXECUTION 下发 agent_upgrade 并等待 Journal 达到终态。
func triggerUpgrade(t *testing.T, a *Agent, execID, downloadURL string) *LocalExecution {
	t.Helper()
	a.OnRunExecution(protocol.NewEnvelope(protocol.MsgRunExecution, execID, protocol.RunExecutionPayload{
		ExecutionID: execID,
		TaskID:      models.AgentUpgradeTaskID,
		Type:        models.TaskTypeAgentUpgrade,
		DownloadURL: downloadURL,
		TriggerType: models.TriggerManual,
	}))
	deadline := time.Now().Add(5 * time.Second)
	for {
		ex, err := a.store.GetExecution(a.ctx, execID)
		if err != nil {
			t.Fatalf("get journal %s: %v", execID, err)
		}
		if ex == nil {
			t.Fatalf("journal %s missing", execID)
		}
		if ex.Status != models.ExecStatusRunning && ex.Status != models.ExecStatusPending {
			return ex
		}
		if time.Now().After(deadline) {
			t.Fatalf("upgrade did not finish in time: status=%q reason=%q", ex.Status, ex.BlockReason)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// binarySHA 计算内容 SHA256（十六进制）。
func binarySHA(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// TestAgentUpgradeFullChainSuccess 全链成功：fake 下载源（正确 SHA256 头）→
// 备份旧二进制 → 原子替换 → 重启（可重启 fixture）→ Journal SUCCESS 终态。
func TestAgentUpgradeFullChainSuccess(t *testing.T) {
	a, exePath := newUpgradeTestAgent(t, models.DeploymentModeNative)
	newBinary := []byte("#!/bin/sh\necho new-agent-v2\n")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Agent-Binary-SHA256", binarySHA(newBinary))
		_, _ = w.Write(newBinary)
	}))
	defer srv.Close()
	restarts := 0
	a.upgrade.restart = func(ctx context.Context) error { restarts++; return nil }

	ex := triggerUpgrade(t, a, "exec-upgrade-ok", srv.URL)
	if ex.Status != models.ExecStatusSuccess {
		t.Fatalf("status = %q, want SUCCESS (reason=%q)", ex.Status, ex.BlockReason)
	}
	if !strings.Contains(ex.Stdout, "service restarting") {
		t.Fatalf("stdout = %q, want restart note", ex.Stdout)
	}
	if ex.Synced {
		t.Fatalf("journal must stay unsynced until Hub ack/reconcile")
	}
	if restarts != 1 {
		t.Fatalf("restarts = %d, want 1", restarts)
	}
	got, err := os.ReadFile(exePath)
	if err != nil || string(got) != string(newBinary) {
		t.Fatalf("binary not replaced: %q err=%v", got, err)
	}
	bak, err := os.ReadFile(exePath + ".bak")
	if err != nil || string(bak) != oldUpgradeBinary {
		t.Fatalf("backup must hold previous binary: %q err=%v", bak, err)
	}
	if _, err := os.Stat(exePath + ".new"); !os.IsNotExist(err) {
		t.Fatalf("temp file must be cleaned up: err=%v", err)
	}
	if !a.capabilities()[models.CapAgentUpgrade] {
		t.Fatal("native agent must report agent_upgrade capability")
	}
}

// TestAgentUpgradeSHA256MismatchKeepsBinary 下载校验失败：SHA256 与响应头不匹配 →
// 原二进制未动、无备份产生、不重启，Journal FAILED 且原因可见。
func TestAgentUpgradeSHA256MismatchKeepsBinary(t *testing.T) {
	a, exePath := newUpgradeTestAgent(t, models.DeploymentModeNative)
	newBinary := []byte("tampered-payload")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// 响应头声明另一内容的摘要 → 校验必然失败
		w.Header().Set("X-Agent-Binary-SHA256", binarySHA([]byte("expected-payload")))
		_, _ = w.Write(newBinary)
	}))
	defer srv.Close()
	restarted := false
	a.upgrade.restart = func(ctx context.Context) error { restarted = true; return nil }

	ex := triggerUpgrade(t, a, "exec-upgrade-sha", srv.URL)
	if ex.Status != models.ExecStatusFailed {
		t.Fatalf("status = %q, want FAILED", ex.Status)
	}
	if !strings.Contains(ex.BlockReason, "sha256 mismatch") {
		t.Fatalf("reason = %q, want sha256 mismatch", ex.BlockReason)
	}
	got, err := os.ReadFile(exePath)
	if err != nil || string(got) != oldUpgradeBinary {
		t.Fatalf("original binary must be untouched: %q err=%v", got, err)
	}
	if _, err := os.Stat(exePath + ".bak"); !os.IsNotExist(err) {
		t.Fatalf("backup must not exist before verification passed: err=%v", err)
	}
	if _, err := os.Stat(exePath + ".new"); !os.IsNotExist(err) {
		t.Fatalf("temp download must be cleaned up: err=%v", err)
	}
	if restarted {
		t.Fatal("service must not restart on sha256 mismatch")
	}
}

// TestAgentUpgradeRestartFailureRollsBack 重启失败 → 恢复备份的旧二进制、
// Journal FAILED 且原因可见（含回滚结果）。
func TestAgentUpgradeRestartFailureRollsBack(t *testing.T) {
	a, exePath := newUpgradeTestAgent(t, models.DeploymentModeNative)
	newBinary := []byte("new-binary-should-be-rolled-back")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Agent-Binary-SHA256", binarySHA(newBinary))
		_, _ = w.Write(newBinary)
	}))
	defer srv.Close()
	a.upgrade.restart = func(ctx context.Context) error {
		return errors.New("unit nodesteer-agent failed to start")
	}

	ex := triggerUpgrade(t, a, "exec-upgrade-restart-fail", srv.URL)
	if ex.Status != models.ExecStatusFailed {
		t.Fatalf("status = %q, want FAILED", ex.Status)
	}
	if !strings.Contains(ex.BlockReason, "restart agent service") ||
		!strings.Contains(ex.BlockReason, "rollback restored previous binary") {
		t.Fatalf("reason = %q, want restart failure + rollback restored", ex.BlockReason)
	}
	got, err := os.ReadFile(exePath)
	if err != nil || string(got) != oldUpgradeBinary {
		t.Fatalf("rollback must restore previous binary: %q err=%v", got, err)
	}
}

// TestAgentUpgradeReplaceFailureKeepsBinary 替换失败 → 原二进制未动、FAILED 终态。
func TestAgentUpgradeReplaceFailureKeepsBinary(t *testing.T) {
	a, exePath := newUpgradeTestAgent(t, models.DeploymentModeNative)
	newBinary := []byte("new-binary")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Agent-Binary-SHA256", binarySHA(newBinary))
		_, _ = w.Write(newBinary)
	}))
	defer srv.Close()
	restarted := false
	a.upgrade.restart = func(ctx context.Context) error { restarted = true; return nil }
	a.upgrade.replace = func(src, exe string) error { return errors.New("rename: permission denied") }

	ex := triggerUpgrade(t, a, "exec-upgrade-replace-fail", srv.URL)
	if ex.Status != models.ExecStatusFailed || !strings.Contains(ex.BlockReason, "replace agent binary") {
		t.Fatalf("status=%q reason=%q, want FAILED replace error", ex.Status, ex.BlockReason)
	}
	got, err := os.ReadFile(exePath)
	if err != nil || string(got) != oldUpgradeBinary {
		t.Fatalf("original binary must be untouched: %q err=%v", got, err)
	}
	if restarted {
		t.Fatal("service must not restart when replace failed")
	}
}

// TestAgentUpgradeRejectedForDockerMode docker 形态双保险：即使绕过 Hub 过滤
// 收到升级指令，Agent 侧也直接拒绝（不下载、不重启），Journal FAILED 且原因可见。
func TestAgentUpgradeRejectedForDockerMode(t *testing.T) {
	a, exePath := newUpgradeTestAgent(t, models.DeploymentModeDocker)
	downloadCalled := false
	restarted := false
	a.upgrade.download = func(ctx context.Context, url, dst string) error {
		downloadCalled = true
		return nil
	}
	a.upgrade.restart = func(ctx context.Context) error { restarted = true; return nil }

	ex := triggerUpgrade(t, a, "exec-upgrade-docker", "http://unused.example/api/agent/binary")
	if ex.Status != models.ExecStatusFailed {
		t.Fatalf("status = %q, want FAILED", ex.Status)
	}
	if !strings.Contains(ex.BlockReason, "unsupported for deployment mode docker") {
		t.Fatalf("reason = %q, want docker unsupported", ex.BlockReason)
	}
	if downloadCalled || restarted {
		t.Fatalf("docker upgrade must be rejected before download/restart (download=%v restart=%v)", downloadCalled, restarted)
	}
	got, err := os.ReadFile(exePath)
	if err != nil || string(got) != oldUpgradeBinary {
		t.Fatalf("binary must be untouched: %q err=%v", got, err)
	}
	if a.capabilities()[models.CapAgentUpgrade] {
		t.Fatal("docker agent must not report agent_upgrade capability")
	}
}

// TestAgentUpgradeRejectedWhenPaused 暂停语义与 T7 一致：paused 收到升级指令
// → BLOCKED + node paused，不启动升级链（不下载、不重启）。
func TestAgentUpgradeRejectedWhenPaused(t *testing.T) {
	a, _ := newUpgradeTestAgent(t, models.DeploymentModeNative)
	a.sch.SetPaused(true)
	downloadCalled := false
	restarted := false
	a.upgrade.download = func(ctx context.Context, url, dst string) error {
		downloadCalled = true
		return nil
	}
	a.upgrade.restart = func(ctx context.Context) error { restarted = true; return nil }

	ex := triggerUpgrade(t, a, "exec-upgrade-paused", "http://unused.example/api/agent/binary")
	if ex.Status != models.ExecStatusBlocked {
		t.Fatalf("status = %q, want BLOCKED", ex.Status)
	}
	if ex.BlockReason != "node paused" {
		t.Fatalf("reason = %q, want %q", ex.BlockReason, "node paused")
	}
	if downloadCalled || restarted {
		t.Fatalf("paused upgrade must not start (download=%v restart=%v)", downloadCalled, restarted)
	}
}

// TestUpgradeReportsNewVersionOnHello 升级后版本上报：HELLO agent_version 取编译注入
// 的 cfg.AgentVersion —— 旧进程上报旧值，升级重启后的新二进制（注入新版本）上报新值；
// 升级链本身不伪造版本。
func TestUpgradeReportsNewVersionOnHello(t *testing.T) {
	oldProc, _ := newUpgradeTestAgent(t, models.DeploymentModeNative)
	if got := oldProc.helloFn().AgentVersion; got != "v0.0.1-old" {
		t.Fatalf("old process hello version = %q, want v0.0.1-old", got)
	}

	// 升级成功（真实链路跑通）
	newBinary := []byte("new-binary-v002")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Agent-Binary-SHA256", binarySHA(newBinary))
		_, _ = w.Write(newBinary)
	}))
	defer srv.Close()
	ex := triggerUpgrade(t, oldProc, "exec-upgrade-version", srv.URL)
	if ex.Status != models.ExecStatusSuccess {
		t.Fatalf("upgrade status = %q, want SUCCESS", ex.Status)
	}

	// 重启后的「新进程」：同一新二进制、ldflags 注入新版本 → HELLO 上报新值
	newProc, err := New(Config{
		HubURL:            "ws://127.0.0.1:8443",
		RegistrationToken: "test-token",
		NodeName:          "upgrade-test-node",
		DataDir:           t.TempDir(),
		DeploymentMode:    models.DeploymentModeNative,
		AgentVersion:      "v0.0.2-new",
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatalf("new process agent: %v", err)
	}
	t.Cleanup(func() { _ = newProc.Close() })
	if got := newProc.helloFn().AgentVersion; got != "v0.0.2-new" {
		t.Fatalf("upgraded process hello version = %q, want v0.0.2-new", got)
	}
}
