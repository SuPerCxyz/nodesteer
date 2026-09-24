package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// agentUnitName Agent 自身的 systemd unit（native 形态自升级重启用），
// 与纳管命令生成的 unit 名一致。
const agentUnitName = "nodesteer-agent"

// upgradeTimeout 自升级执行总超时（下载 + 备份 + 替换 + 重启）。
const upgradeTimeout = 10 * time.Minute

// upgradeRunner 自升级执行链依赖（由 Agent.New 绑定默认实现，测试按需覆盖字段注入）。
type upgradeRunner struct {
	// executable 返回当前 Agent 可执行文件路径（默认 os.Executable）
	executable func() (string, error)
	// download 下载二进制到 dst 并校验（对照 Hub 响应头 X-Agent-Binary-SHA256），
	// 失败时移除 dst（默认 downloadAgentBinary）
	download func(ctx context.Context, url, dst string) error
	// backup 备份当前二进制并返回备份路径（默认 backupAgentBinary）
	backup func(exe string) (string, error)
	// replace 用 src 原子替换当前二进制（默认 replaceAgentBinary）
	replace func(src, exe string) error
	// restart 重启 Agent 服务（默认 host adapter RestartService(unit=agentUnitName)）
	restart func(ctx context.Context) error
}

// defaultUpgradeRunner 绑定默认实现。
func (a *Agent) defaultUpgradeRunner() *upgradeRunner {
	return &upgradeRunner{
		executable: os.Executable,
		download:   a.downloadAgentBinary,
		backup:     backupAgentBinary,
		replace:    replaceAgentBinary,
		restart:    func(ctx context.Context) error { return a.host.RestartService(ctx, agentUnitName) },
	}
}

// runAgentUpgrade 执行 Agent 自升级链：下载（Hub binary 端点）→ SHA256 校验 →
// 备份当前可执行文件 → 原子替换 → 重启服务；任一步失败回报 FAILED 终态，
// 替换后的失败步骤回滚到备份版本。调用方（OnRunExecution）已完成 Journal 首写
// （不变量 8）与 READY / 暂停门禁（暂停语义与 T7 一致：BLOCKED + node paused）。
//
// 终态上报策略：重启会由 systemd 终止本进程，成功终态先落盘本地 Journal
// （Synced=false）再发起重启——进程被替换时由新进程重连后的执行对账补齐 Hub 终态；
// 重启调用返回错误（本进程仍存活）时回滚备份、改写 Journal 为 FAILED 并直连上报。
func (a *Agent) runAgentUpgrade(p protocol.RunExecutionPayload) {
	execID := p.ExecutionID
	fail := func(reason string) {
		a.finalizeUpgradeJournal(execID, models.ExecStatusFailed, "", reason)
		a.sendExecutionFinishedByID(execID)
	}

	// 双保险：仅 native 形态支持自升级（Hub 下发前已按 deployment_mode 过滤）。
	if a.cfg.DeploymentMode != models.DeploymentModeNative {
		fail("self-upgrade unsupported for deployment mode " + a.cfg.DeploymentMode)
		return
	}

	// 下载源：Hub 指定的既有 GET /api/agent/binary；未携带时按 HubURL 派生兜底。
	url := p.DownloadURL
	if url == "" {
		if base := agentHTTPBase(a.cfg.HubURL); base != "" {
			url = base + "/api/agent/binary?architecture=" + osArch()
		}
	}
	if url == "" {
		fail("hub download url is not configured")
		return
	}

	ctx, cancel := context.WithTimeout(a.ctx, upgradeTimeout)
	defer cancel()

	exe, err := a.upgrade.executable()
	if err != nil {
		fail("locate agent executable: " + err.Error())
		return
	}
	if resolved, err := filepath.EvalSymlinks(exe); err == nil {
		exe = resolved
	}
	tmp := exe + ".new"
	defer os.Remove(tmp) // 成功替换后 tmp 已被 rename，此处移除无害

	// 下载 + SHA256 必须校验（对照 Hub 响应头）；失败不动当前二进制
	if err := a.upgrade.download(ctx, url, tmp); err != nil {
		fail("download agent binary: " + err.Error())
		return
	}

	// 备份当前二进制（<exe>.bak，供失败回滚）
	bak, err := a.upgrade.backup(exe)
	if err != nil {
		fail("backup current agent binary: " + err.Error())
		return
	}

	// 原子替换（写临时文件 + rename）；失败时原二进制未动
	if err := a.upgrade.replace(tmp, exe); err != nil {
		fail("replace agent binary: " + err.Error())
		return
	}

	// 重启前落盘成功终态：重启将终止本进程，直连上报可能丢失，
	// 由新进程重连后的执行对账补齐 Hub 终态（journal Synced=false）。
	a.finalizeUpgradeJournal(execID, models.ExecStatusSuccess, "agent binary replaced; service restarting", "")

	if err := a.upgrade.restart(ctx); err != nil {
		// 重启失败 → 恢复备份的旧二进制并保持服务可用，改写为 FAILED 终态
		reason := "restart agent service: " + err.Error()
		if rbErr := restoreAgentBinary(bak, exe); rbErr != nil {
			reason += "; rollback failed: " + rbErr.Error()
		} else {
			reason += "; rollback restored previous binary"
		}
		a.finalizeUpgradeJournal(execID, models.ExecStatusFailed, "", reason)
		a.sendExecutionFinishedByID(execID)
		return
	}

	// 重启调用返回且本进程仍存活（测试 fixture / systemctl 未终止本进程）→ 直连上报成功终态
	a.sendExecutionFinishedByID(execID)
}

// finalizeUpgradeJournal 自升级终态落盘（不上报）：字段整体覆盖写入，Synced=false。
// 供「重启前标记成功 / 重启失败回滚后改写失败」两阶段使用。
func (a *Agent) finalizeUpgradeJournal(execID, status, stdout, blockReason string) {
	ex, err := a.store.GetExecution(a.ctx, execID)
	if err != nil || ex == nil {
		a.logger.Error("upgrade journal missing", "id", execID)
		return
	}
	ex.Status = status
	ex.ExitCode = 0
	if status != models.ExecStatusSuccess {
		ex.ExitCode = -1
	}
	ex.Stdout = truncate("", stdout, a.maxLogBytes())
	ex.Stderr = ""
	ex.StdoutTruncated = false
	ex.StderrTruncated = false
	ex.EndTime = time.Now().UTC().Format(time.RFC3339Nano)
	ex.BlockReason = blockReason
	ex.Synced = false
	if err := a.store.UpdateExecution(a.ctx, ex); err != nil {
		a.logger.Error("update upgrade journal failed", "id", execID, "error", err)
	}
}

// sendExecutionFinishedByID 读取 journal 并上报执行终态（连接断开时不发送，靠重连对账）。
func (a *Agent) sendExecutionFinishedByID(execID string) {
	ex, err := a.store.GetExecution(a.ctx, execID)
	if err != nil || ex == nil {
		return
	}
	a.sendExecutionFinished(ex)
}

// downloadAgentBinary 下载 Agent 二进制到 dst，并强制校验响应头 X-Agent-Binary-SHA256；
// 头缺失、HTTP 非 200、摘要不匹配均视为失败并移除 dst（不变量 10：校验失败禁止安装）。
func (a *Agent) downloadAgentBinary(ctx context.Context, url, dst string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return err
	}
	resp, err := a.conn.HTTPClient().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download status %d", resp.StatusCode)
	}
	headerSHA := strings.ToLower(strings.TrimSpace(resp.Header.Get("X-Agent-Binary-SHA256")))
	if headerSHA == "" {
		return errors.New("hub did not provide agent binary sha256")
	}
	f, err := os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o755)
	if err != nil {
		return err
	}
	hash := sha256.New()
	_, copyErr := io.Copy(io.MultiWriter(f, hash), resp.Body)
	if closeErr := f.Close(); copyErr == nil {
		copyErr = closeErr
	}
	if copyErr != nil {
		os.Remove(dst)
		return copyErr
	}
	if hex.EncodeToString(hash.Sum(nil)) != headerSHA {
		os.Remove(dst)
		return errors.New("sha256 mismatch")
	}
	return nil
}

// backupAgentBinary 备份当前二进制到 <exe>.bak（覆盖旧备份），返回备份路径。
func backupAgentBinary(exe string) (string, error) {
	src, err := os.Open(exe)
	if err != nil {
		return "", err
	}
	defer src.Close()
	info, err := src.Stat()
	if err != nil {
		return "", err
	}
	mode := info.Mode().Perm()
	if mode == 0 {
		mode = 0o755
	}
	bak := exe + ".bak"
	dst, err := os.OpenFile(bak, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, mode)
	if err != nil {
		return "", err
	}
	if _, err := io.Copy(dst, src); err != nil {
		dst.Close()
		os.Remove(bak)
		return "", err
	}
	if err := dst.Close(); err != nil {
		return "", err
	}
	return bak, nil
}

// replaceAgentBinary 原子替换：新二进制置可执行后 rename 覆盖当前可执行文件
// （Linux 上运行中的旧 inode 不受影响，直至进程被替换）。
func replaceAgentBinary(src, exe string) error {
	if err := os.Chmod(src, 0o755); err != nil {
		return err
	}
	return os.Rename(src, exe)
}

// restoreAgentBinary 回滚：把备份 rename 覆盖回当前可执行文件（原子换回旧版本）。
func restoreAgentBinary(bak, exe string) error {
	return os.Rename(bak, exe)
}

// agentHTTPBase 由 Hub WebSocket 地址派生 HTTP 基址（ws→http、wss→https）；
// 仅当升级指令未携带 DownloadURL 时兜底，无法派生时返回空串。
func agentHTTPBase(hubURL string) string {
	switch {
	case strings.HasPrefix(hubURL, "wss://"):
		return "https://" + strings.TrimPrefix(hubURL, "wss://")
	case strings.HasPrefix(hubURL, "ws://"):
		return "http://" + strings.TrimPrefix(hubURL, "ws://")
	default:
		return ""
	}
}
