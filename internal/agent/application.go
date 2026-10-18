package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent/host"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/google/uuid"
)

// Deployment 阶段
const (
	PhasePreparing   = "PREPARING"
	PhaseStopped     = "STOPPED"
	PhaseReplaced    = "REPLACED"
	PhaseStarted     = "STARTED"
	PhaseVerifying   = "VERIFYING"
	PhaseDone        = "DONE"
	PhaseRollingBack = "ROLLING_BACK"
	// PhaseRollbackFailed 回滚已尝试且失败（区别于回滚中途崩溃的 ROLLING_BACK）。
	// 重启恢复时仍会在 superseded/绑定校验通过后尝试重放。
	PhaseRollbackFailed = "ROLLBACK_FAILED"
)

// Deployment 终态成因（journal completed_kind），仅 DONE 行有值。
// 只有“成功部署”产生的 DONE 才构成已被更晚部署取代；准备失败未做任何宿主替换，
// 回滚成功表示该次尝试已回到起点，两者都不代表装了更新版本。
// 恢复阶段判定为不可重放的记录同样落 DONE 终态（见下方 CompletedKindSuperseded 等），
// 只为收敛活动部署集合与重复告警，均不构成取代依据，也不允许重放回滚。
const (
	CompletedKindDeployed      = "deployed"
	CompletedKindRolledBack    = "rolled_back"
	CompletedKindPrepareFailed = "prepare_failed"
	// CompletedKindSuperseded：同应用已有更晚的成功部署，陈旧 backup 不得覆盖。
	CompletedKindSuperseded = "superseded"
	// CompletedKindStaleBinding：journal 绑定与当前应用定义不一致（定义漂移），backup 归属未知。
	CompletedKindStaleBinding = "stale_binding"
	// CompletedKindLegacyUnbound：旧行缺绑定字段（升级前写入），backup 归属未知。
	CompletedKindLegacyUnbound = "legacy_unbound"
	// CompletedKindPrepareInterrupted：准备阶段未完成即中断（未产生宿主副作用），不重放。
	CompletedKindPrepareInterrupted = "prepare_interrupted"
)

// ApplicationManager Agent 应用管理器
type ApplicationManager struct {
	store    *LocalStore
	host     host.HostAdapter
	artifact *ArtifactCache
	logger   *slog.Logger
}

// NewApplicationManager 创建应用管理器
func NewApplicationManager(st *LocalStore, h host.HostAdapter, artifact *ArtifactCache, logger *slog.Logger) *ApplicationManager {
	return &ApplicationManager{store: st, host: h, artifact: artifact, logger: logger}
}

// HandleDeployRequest 处理部署请求
func (am *ApplicationManager) HandleDeployRequest(ctx context.Context, p protocol.DeployRequestPayload, execID string, cb func(protocol.DeployResultPayload)) {
	appDef := am.loadApplication(p.AppID)

	switch p.Operation {
	case "start":
		am.operate(p, "start", cb)
	case "stop":
		am.operate(p, "stop", cb)
	case "restart":
		am.operate(p, "restart", cb)
	case "deploy":
		am.deploy(ctx, p, appDef, execID, cb)
	case "upgrade":
		am.deploy(ctx, p, appDef, execID, cb)
	default:
		am.deploy(ctx, p, appDef, execID, cb)
	}
}

// HandleRunOperation 处理运行操作（来自 Task，含 Agent-owned 调度的应用部署）
func (am *ApplicationManager) HandleRunOperation(ctx context.Context, p protocol.RunExecutionPayload, ex *LocalExecution, cb func(protocol.DeployResultPayload)) {
	op := p.AppOperation
	if op == "" {
		op = "start"
	}
	// 部署/升级：从本地应用定义解析完整部署参数
	if op == "deploy" || op == "upgrade" {
		am.handleDeployOperation(ctx, p, ex, op, cb)
		return
	}
	var hc models.HealthCheck
	req := protocol.DeployRequestPayload{
		AppID:     p.AppID,
		UnitName:  "nodesteer-" + p.AppID,
		Operation: op,
	}
	if def := am.loadApplication(p.AppID); len(def) > 0 {
		var app models.Application
		if json.Unmarshal(def, &app) == nil {
			if app.UnitName != "" {
				req.UnitName = app.UnitName
			}
			req.AppVersion = app.Version
		}
	}
	if len(p.Condition) > 0 {
		json.Unmarshal(p.Condition, &hc)
	}
	am.operate(req, op, cb)
}

// CleanupApplication 删除 Hub Desired State 中已移除应用对应的受管控资源。
// 只有登记过的 Unit 才允许被清理，避免误删宿主机上的非 NodeSteer 服务。
func (am *ApplicationManager) CleanupApplication(ctx context.Context, appID string) error {
	def := am.loadApplication(appID)
	if len(def) == 0 {
		return nil
	}
	var app models.Application
	if err := json.Unmarshal(def, &app); err != nil {
		return err
	}
	unit := app.UnitName
	if unit == "" {
		unit = "nodesteer-" + app.ID + ".service"
	}
	if !strings.HasSuffix(unit, ".service") {
		unit += ".service"
	}
	registered, err := am.store.IsUnitRegistered(ctx, app.ID, unit)
	if err != nil || !registered {
		return err
	}
	_ = am.host.StopService(ctx, unit)
	_ = am.host.DisableService(ctx, unit)
	_ = am.host.Remove(ctx, "/etc/systemd/system/"+unit)
	if app.BinaryPath != "" {
		_ = am.host.Remove(ctx, app.BinaryPath)
	}
	configPath := app.ConfigPath
	if configPath == "" {
		configPath = "/etc/" + strings.TrimSuffix(unit, ".service") + ".conf"
	}
	_ = am.host.Remove(ctx, configPath)
	_ = am.host.DaemonReload(ctx)
	return am.store.DeleteUnit(ctx, app.ID)
}

// handleDeployOperation 从本地应用定义构造部署请求并执行（支持 Agent-owned 调度触发）
func (am *ApplicationManager) handleDeployOperation(ctx context.Context, p protocol.RunExecutionPayload, ex *LocalExecution, op string, cb func(protocol.DeployResultPayload)) {
	res := protocol.DeployResultPayload{AppID: p.AppID, Operation: op}
	appDef := am.loadApplication(p.AppID)
	if len(appDef) == 0 {
		res.OK = false
		res.Error = "application definition not synced: " + p.AppID
		if cb != nil {
			cb(res)
		}
		return
	}
	var app models.Application
	if err := json.Unmarshal(appDef, &app); err != nil {
		res.OK = false
		res.Error = "invalid application definition: " + err.Error()
		if cb != nil {
			cb(res)
		}
		return
	}
	res.Version = app.Version
	var hc json.RawMessage
	if app.HealthCheck != nil {
		b, _ := json.Marshal(app.HealthCheck)
		hc = b
	}
	req := protocol.DeployRequestPayload{
		AppID:          app.ID,
		AppVersion:     app.Version,
		AppRevision:    app.Revision,
		ArtifactID:     app.ArtifactID,
		ArtifactURL:    app.ArtifactURL,
		ArtifactSHA256: app.ArtifactSHA256,
		BinaryPath:     app.BinaryPath,
		Arguments:      app.Arguments,
		Environment:    app.Environment,
		Config:         app.Config,
		ConfigPath:     app.ConfigPath,
		UnitName:       app.UnitName,
		HealthCheck:    hc,
		Operation:      op,
	}
	am.deploy(ctx, req, appDef, ex.ID, cb)
}

func (am *ApplicationManager) loadApplication(id string) []byte {
	// 应用定义存储于 applications 表，通过 ListApplications 查
	apps, err := am.store.ListApplications(context.Background())
	if err != nil {
		return nil
	}
	for _, def := range apps {
		var a models.Application
		if json.Unmarshal(def, &a) == nil && a.ID == id {
			return def
		}
	}
	return nil
}

// operate Start/Stop/Restart 操作
func (am *ApplicationManager) operate(p protocol.DeployRequestPayload, op string, cb func(protocol.DeployResultPayload)) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	res := protocol.DeployResultPayload{AppID: p.AppID, Operation: op}
	res.Version = p.AppVersion
	unit := p.UnitName
	if unit == "" {
		unit = "nodesteer-" + p.AppID
	}
	if !strings.HasSuffix(unit, ".service") {
		unit += ".service"
	}
	if err := validateUnitName(unit); err != nil {
		res.Error = err.Error()
		if cb != nil {
			cb(res)
		}
		return
	}
	// systemd Unit Registry：仅允许操作已登记 Unit
	registered, err := am.store.IsUnitRegistered(ctx, p.AppID, unit)
	if err != nil {
		res.OK = false
		res.Error = "unit registry check failed: " + err.Error()
		if cb != nil {
			cb(res)
		}
		return
	}
	if !registered {
		res.OK = false
		res.Error = "unit not registered: " + unit + " (deploy first)"
		if cb != nil {
			cb(res)
		}
		return
	}
	switch op {
	case "start":
		err := am.host.StartService(ctx, unit)
		res.OK = err == nil
		if err != nil {
			res.Error = err.Error()
		}
	case "stop":
		err := am.host.StopService(ctx, unit)
		res.OK = err == nil
		if err != nil {
			res.Error = err.Error()
		}
	case "restart":
		err := am.host.RestartService(ctx, unit)
		res.OK = err == nil
		if err != nil {
			res.Error = err.Error()
		}
	}
	if res.OK {
		if op == "stop" {
			res.Health = "stopped"
		} else {
			res.Health = "healthy"
		}
	} else {
		res.Health = "unhealthy"
	}
	if cb != nil {
		cb(res)
	}
}

// deploy 部署/升级
func (am *ApplicationManager) deploy(ctx context.Context, p protocol.DeployRequestPayload, appDef []byte, execID string, cb func(protocol.DeployResultPayload)) {
	res := protocol.DeployResultPayload{AppID: p.AppID, Operation: p.Operation, Version: p.AppVersion}
	var app models.Application
	if len(appDef) > 0 {
		if err := json.Unmarshal(appDef, &app); err != nil {
			res.Error = "invalid application definition: " + err.Error()
			cb(res)
			return
		}
	}
	// from_version 记录本节点实际安装的版本（最近一次成功部署的 to_version），
	// 而不是 Hub 当前应用定义的期望版本（缺陷 OBS-20261016-JOURNAL-FROM-VERSION）。
	fromVersion, err := am.store.InstalledApplicationVersion(ctx, p.AppID)
	if err != nil {
		res.Error = "read installed application version failed: " + err.Error()
		cb(res)
		return
	}
	deploymentID := uuid.NewString()
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if err := am.store.CreateDeployment(ctx, &DeploymentRow{
		ID: deploymentID, ApplicationID: p.AppID, FromVersion: fromVersion, ToVersion: p.AppVersion,
		Phase: PhasePreparing, StartedAt: now, UpdatedAt: now,
	}); err != nil {
		res.Error = "create deployment journal failed: " + err.Error()
		cb(res)
		return
	}
	// 准备阶段失败还没有修改宿主文件，不能调用破坏性的回滚。
	prepareFailed := func(reason string) {
		res.Error = reason
		if err := am.store.CompleteDeployment(ctx, deploymentID, CompletedKindPrepareFailed, ""); err != nil {
			res.Error += "; finish preparation journal failed: " + err.Error()
		}
		cb(res)
	}
	artifactPath := ""
	if p.ArtifactURL != "" {
		if p.ArtifactSHA256 == "" || am.artifact == nil {
			prepareFailed("artifact sha256/cache is missing")
			return
		}
		if err := am.artifact.Prefetch(ctx, p.ArtifactURL, p.ArtifactSHA256); err != nil {
			prepareFailed("artifact download failed: " + err.Error())
			return
		}
		artifactPath = am.artifact.Path(p.ArtifactSHA256)
	}
	unitName := p.UnitName
	if unitName == "" {
		unitName = "nodesteer-" + p.AppID + ".service"
	}
	if !strings.HasSuffix(unitName, ".service") {
		unitName += ".service"
	}
	if err := validateUnitName(unitName); err != nil {
		prepareFailed(err.Error())
		return
	}
	binaryPath := p.BinaryPath
	if binaryPath == "" {
		binaryPath = app.BinaryPath
	}
	if binaryPath == "" {
		prepareFailed("binary path is required")
		return
	}
	configPath := p.ConfigPath
	if configPath == "" {
		configPath = "/etc/" + strings.TrimSuffix(unitName, ".service") + ".conf"
	}
	p.ConfigPath = configPath
	for _, path := range []string{binaryPath, configPath} {
		if err := am.host.ValidatePath(path); err != nil {
			prepareFailed("invalid deployment path: " + err.Error())
			return
		}
		// 路径会原样写进 systemd Unit（ExecStart / EnvironmentFile），控制字符会破坏
		// Unit 语法（换行即注入新指令行），必须在产生任何宿主副作用前拒绝。
		if err := validateUnitEmbeddedPath(path); err != nil {
			prepareFailed(err.Error())
			return
		}
	}
	var hc models.HealthCheck
	if len(p.HealthCheck) > 0 {
		if err := json.Unmarshal(p.HealthCheck, &hc); err != nil {
			prepareFailed("invalid health check: " + err.Error())
			return
		}
	} else if app.HealthCheck != nil {
		hc = *app.HealthCheck
	}
	// 部署上下文绑定：记录当时的 app revision 与解析后的三路径。
	// 必须早于任何宿主文件副作用（STOPPED 之前），恢复时据此拒绝陈旧 backup 重放。
	appRevision := p.AppRevision
	if appRevision == 0 {
		appRevision = app.Revision
	}
	if err := am.store.SetDeploymentBinding(ctx, deploymentID, appRevision, binaryPath, configPath, unitName); err != nil {
		prepareFailed("save deployment binding failed: " + err.Error())
		return
	}

	backupDir, err := filepath.Abs(filepath.Join(am.store.Dir(), "applications"))
	if err != nil {
		prepareFailed("resolve backup directory failed: " + err.Error())
		return
	}
	backupRoot := filepath.Join(backupDir, "backup-"+uuid.NewString())
	if err := os.MkdirAll(backupDir, 0o755); err != nil {
		prepareFailed("create backup directory failed: " + err.Error())
		return
	}
	// 新 applications 目录本身的目录项也必须先持久化到数据目录。
	parent, err := os.Open(filepath.Dir(backupDir))
	if err != nil {
		prepareFailed("open backup parent failed: " + err.Error())
		return
	}
	err = parent.Sync()
	parent.Close()
	if err != nil {
		prepareFailed("sync backup parent failed: " + err.Error())
		return
	}
	backupPath, err := am.backupFile(ctx, binaryPath, backupRoot+".binary")
	if err != nil {
		prepareFailed("backup binary failed: " + err.Error())
		return
	}
	backupConfigPath, err := am.backupFile(ctx, configPath, backupRoot+".conf")
	if err != nil {
		prepareFailed("backup config failed: " + err.Error())
		return
	}
	backupUnitPath, err := am.backupFile(ctx, "/etc/systemd/system/"+unitName, backupRoot+".unit")
	if err != nil {
		prepareFailed("backup unit failed: " + err.Error())
		return
	}
	if err := am.store.SetDeploymentBackups(ctx, deploymentID, backupPath, backupConfigPath, backupUnitPath); err != nil {
		prepareFailed("save deployment backups failed: " + err.Error())
		return
	}
	// STOPPED 先落盘：从这里开始可能产生副作用，重启时有完整恢复依据。
	if err := am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseStopped, backupPath); err != nil {
		prepareFailed("save stopped phase failed: " + err.Error())
		return
	}
	// restoredHealthCheck 回滚复检使用的健康定义：只做“被恢复的 Unit 确实在跑”的版本无关
	// 存活判定，绝不复用失败版本的健康定义。失败版本的健康定义（HTTP 端口、新命令语义等）
	// 与被还原的旧版本不匹配，用它复检必然再失败，会把“文件其实已还原”误报成
	// ROLLBACK_FAILED（缺陷 APPUI-20261016-ROLLBACK-FALSE-FAILURE）。
	// ROLLBACK_FAILED 只保留给真的回滚失败（备份不可读、还原/启动失败等）。
	failed := func(reason string) {
		res.Error, res.Health, res.Rollback = reason, "unhealthy", true
		journalErr := am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseRollingBack, backupPath)
		if !am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath) {
			res.Error += "; rollback failed"
			// 回滚已尝试且失败：落终态 ROLLBACK_FAILED（不再是遗留 ROLLING_BACK）。
			// 重启恢复仍需经 superseded/绑定校验后才允许重放。
			if err := am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseRollbackFailed, backupPath); journalErr == nil {
				journalErr = err
			}
		} else if journalErr == nil {
			// 回滚成功：该次尝试已回到起点，终态成因记为 rolled_back，
			// 不构成“已被更晚成功部署取代”的依据。
			journalErr = am.store.CompleteDeployment(ctx, deploymentID, CompletedKindRolledBack, backupPath)
		}
		if journalErr != nil {
			res.Error += "; rollback journal failed: " + journalErr.Error()
		}
		cb(res)
	}
	if _, err := am.host.ServiceStatus(ctx, unitName); err == nil {
		if err := am.host.StopService(ctx, unitName); err != nil {
			failed("stop service failed: " + err.Error())
			return
		}
	}
	if artifactPath != "" {
		data, err := os.ReadFile(artifactPath)
		if err != nil {
			failed("read artifact failed: " + err.Error())
			return
		}
		if err := am.host.AtomicReplace(ctx, binaryPath, data, 0o755); err != nil {
			failed("install binary failed: " + err.Error())
			return
		}
		if err := am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseReplaced, backupPath); err != nil {
			failed("save replaced phase failed: " + err.Error())
			return
		}
	}
	if p.Config != "" {
		if err := am.host.AtomicReplace(ctx, configPath, []byte(p.Config), 0o644); err != nil {
			failed("config write failed: " + err.Error())
			return
		}
	}
	if err := am.host.InstallUnit(ctx, unitName, am.buildUnit(unitName, p, binaryPath)); err != nil {
		failed("install unit failed: " + err.Error())
		return
	}
	if err := am.host.DaemonReload(ctx); err != nil {
		failed("daemon reload failed: " + err.Error())
		return
	}
	if err := am.host.EnableService(ctx, unitName); err != nil {
		failed("enable service failed: " + err.Error())
		return
	}
	if err := am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseStarted, backupPath); err != nil {
		failed("save started phase failed: " + err.Error())
		return
	}
	if err := am.host.StartService(ctx, unitName); err != nil {
		failed("start service failed: " + err.Error())
		return
	}
	if err := am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseVerifying, backupPath); err != nil {
		failed("save verifying phase failed: " + err.Error())
		return
	}
	if !am.checkHealth(ctx, hc, unitName) {
		failed("health check failed")
		return
	}
	if err := am.store.RegisterUnit(ctx, p.AppID, unitName); err != nil {
		failed("register unit failed: " + err.Error())
		return
	}
	if err := am.store.CompleteDeployment(ctx, deploymentID, CompletedKindDeployed, backupPath); err != nil {
		failed("finish deployment journal failed: " + err.Error())
		return
	}
	res.OK, res.Health = true, "healthy"
	cb(res)
}

// backupAbsent 仅表示本次已确证不存在；旧记录空字符串不具有此含义。
const backupAbsent = "absent:"

func (am *ApplicationManager) backupFile(ctx context.Context, path, backup string) (string, error) {
	info, err := am.host.Stat(ctx, path)
	if os.IsNotExist(err) {
		return backupAbsent, nil
	}
	if err != nil {
		return "", err
	}
	data, err := am.host.ReadFile(ctx, path)
	if err != nil {
		return "", err
	}
	f, err := os.OpenFile(backup, os.O_WRONLY|os.O_CREATE|os.O_EXCL, info.Mode().Perm())
	if err != nil {
		return "", err
	}
	if _, err = f.Write(data); err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return "", err
	}
	if closeErr != nil {
		return "", closeErr
	}
	dir, err := os.Open(filepath.Dir(backup))
	if err != nil {
		return "", err
	}
	err = dir.Sync()
	dir.Close()
	return backup, err
}

// buildUnit 构造 systemd Unit
func (am *ApplicationManager) buildUnit(unitName string, p protocol.DeployRequestPayload, binaryPath string) string {
	var b strings.Builder
	b.WriteString("[Unit]\n")
	b.WriteString("Description=NodeSteer managed application " + p.AppID + "\n")
	b.WriteString("After=network.target\n\n[Service]\n")
	b.WriteString("ExecStart=" + unitQuote(binaryPath))
	for _, arg := range p.Arguments {
		b.WriteString(" " + unitQuote(arg))
	}
	b.WriteString("\nRestart=on-failure\n")
	b.WriteString("RestartSec=5\n")
	if p.Environment != nil {
		for k, v := range p.Environment {
			b.WriteString("Environment=" + unitQuote(k+"="+v) + "\n")
		}
	}
	if p.ConfigPath != "" {
		// EnvironmentFile 的值必须是**未加引号**的 "-<绝对路径>"（实测 systemd 259）：
		// ① 前导 "-" 表示文件缺失时不报错，systemd 只识别值首字符，引号包住后不再识别；
		// ② 该指令不做词拆分（空格也属路径），引号会让 systemd 报
		//    "EnvironmentFile= path is not absolute, ignoring" 并静默忽略整个文件，
		//    导致应用「配置内容」运行期完全不生效（缺陷 APPUI-20261016-ENVFILE-QUOTED-DASH）。
		b.WriteString("EnvironmentFile=-" + p.ConfigPath + "\n")
	}
	b.WriteString("\n[Install]\nWantedBy=multi-user.target\n")
	return b.String()
}

// checkHealth 健康检查
func (am *ApplicationManager) checkHealth(ctx context.Context, hc models.HealthCheck, unitName string) bool {
	if hc.Type == "" {
		return true
	}
	attempts := hc.Attempts
	if attempts <= 0 {
		attempts = 3
	}
	interval := time.Duration(hc.Interval) * time.Second
	if interval <= 0 {
		interval = 2 * time.Second
	}
	timeout := time.Duration(hc.Timeout) * time.Second
	if timeout <= 0 {
		timeout = 5 * time.Second
	}

	for i := 0; i < attempts; i++ {
		ok := am.checkOnce(ctx, hc, unitName, timeout)
		if ok {
			return true
		}
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return false
		case <-timer.C:
		}
	}
	return false
}

func (am *ApplicationManager) checkOnce(ctx context.Context, hc models.HealthCheck, unitName string, timeout time.Duration) bool {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	switch hc.Type {
	case models.HealthTypeSystemd:
		status, err := am.host.ServiceStatus(ctx, unitName)
		if err != nil || status != "active" {
			return false
		}
		// systemctl start may return while a restart-on-failure unit is only
		// briefly active. Confirm the service remains running before declaring
		// a deployment healthy.
		timer := time.NewTimer(250 * time.Millisecond)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return false
		case <-timer.C:
		}
		status, err = am.host.ServiceStatus(ctx, unitName)
		return err == nil && status == "active"
	case models.HealthTypeTCP:
		conn, err := net.DialTimeout("tcp", hc.Target, timeout)
		if err != nil {
			return false
		}
		conn.Close()
		return true
	case models.HealthTypeHTTP:
		client := &http.Client{Timeout: timeout}
		resp, err := client.Get(hc.Target)
		if err != nil {
			return false
		}
		defer resp.Body.Close()
		return resp.StatusCode >= 200 && resp.StatusCode < 400
	case models.HealthTypeCommand:
		_, err := am.host.RunCommand(ctx, "/bin/sh", "-c", hc.Target)
		return err == nil
	}
	return true
}

// rollback 仅依据已持久的备份或明确不存在标记恢复；缺失信息不推断归属。
// 还原后只做版本无关的存活复检（systemd active），不复用失败版本的健康定义：
// 被还原的旧版本与失败版本的健康语义（端口、命令等）不同，用它复检必然再失败，
// 会把已成功的回滚误判为 ROLLBACK_FAILED（缺陷 APPUI-20261016-ROLLBACK-FALSE-FAILURE）。
func (am *ApplicationManager) rollback(ctx context.Context, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath string) bool {
	for _, backup := range []string{backupPath, backupConfigPath, backupUnitPath} {
		if backup == backupAbsent {
			continue
		}
		if backup == "" || !filepath.IsAbs(backup) {
			return false
		}
		if _, err := os.ReadFile(backup); err != nil {
			return false
		}
	}
	unitPath := "/etc/systemd/system/" + unitName
	if _, err := am.host.Stat(ctx, unitPath); err == nil {
		if err := am.host.StopService(ctx, unitName); err != nil {
			return false
		}
	} else if !os.IsNotExist(err) {
		return false
	}
	cleaned := true
	restore := func(path, backup string, mode os.FileMode) {
		if backup == backupAbsent {
			if err := am.host.Remove(ctx, path); err != nil && !os.IsNotExist(err) {
				cleaned = false
			}
			return
		}
		data, err := os.ReadFile(backup)
		if err != nil {
			cleaned = false
			return
		}
		if err := am.host.AtomicReplace(ctx, path, data, mode); err != nil {
			cleaned = false
		}
	}
	restore(binaryPath, backupPath, 0o755)
	restore(configPath, backupConfigPath, 0o644)
	if backupUnitPath == backupAbsent {
		if _, err := am.host.Stat(ctx, unitPath); err == nil {
			if err := am.host.DisableService(ctx, unitName); err != nil {
				cleaned = false
			}
		}
		restore(unitPath, backupUnitPath, 0o644)
	} else {
		data, err := os.ReadFile(backupUnitPath)
		if err != nil || am.host.InstallUnit(ctx, unitName, string(data)) != nil {
			cleaned = false
		}
	}
	if err := am.host.DaemonReload(ctx); err != nil {
		cleaned = false
	}
	if !cleaned {
		return false
	}
	if backupPath != backupAbsent && backupUnitPath != backupAbsent {
		if err := am.host.StartService(ctx, unitName); err != nil {
			return false
		}
		// 版本无关存活复检：恢复后的 Unit 必须 active 且保持 active。
		return am.checkHealth(ctx, models.HealthCheck{Type: models.HealthTypeSystemd}, unitName)
	}
	return true
}

// RecoverDeployments 启动恢复半完成部署。旧空备份没有删除文件的授权含义。
// 恢复回滚前有双重防护：①同应用已存在更晚的“成功部署”（superseded：phase=DONE 且
// completed_kind=deployed）→ 不重放；②journal 绑定的 app revision 与三路径与当前定义
// 不一致（含旧行缺失绑定字段，视为未知）→ 不重放。防止用陈旧 backup 覆盖 Hub 已推进的
// Desired State；准备失败、回滚成功等非成功 DONE 不构成取代，半完成行仍按安全恢复重放。
//
// 判定为不可重放的记录（被取代、绑定失效、旧行缺绑定、准备阶段中断）不再永久悬挂
// （CR-3）：本次恢复即落终态（DONE + completed_kind），使其离开活动部署集合、重启不再重复告警；
// 这些终态成因均不等于 deployed，不会反过来构成取代依据，也不改变“不重放”的安全语义。
// 无法判定或终态写入失败的行保持活动，恢复末尾汇总输出，便于人工介入。
func (am *ApplicationManager) RecoverDeployments(ctx context.Context) {
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil {
		am.logger.Error("read deployment recovery journal failed", "error", err)
		return
	}
	histories := map[string][]*DeploymentRow{}
	closed := map[string]int{}
	var pending []string
	for _, d := range active {
		am.logger.Info("recovering incomplete deployment", "id", d.ID, "app", d.ApplicationID, "phase", d.Phase)
		superseded, err := am.supersededByLaterDoneDeployment(ctx, d, histories)
		if err != nil {
			// 无法核对是否被取代时保守处理：不触碰任何文件。
			am.logger.Error("deployment supersession check failed; skip replay", "id", d.ID, "error", err)
			pending = append(pending, d.ID)
			continue
		}
		if superseded {
			if am.closeUnreplayable(ctx, d, CompletedKindSuperseded,
				"superseded by a later successful deployment; stale backup not replayed") {
				closed[CompletedKindSuperseded]++
			} else {
				pending = append(pending, d.ID)
			}
			continue
		}
		var app models.Application
		if def := am.loadApplication(d.ApplicationID); len(def) == 0 || json.Unmarshal(def, &app) != nil || app.BinaryPath == "" {
			am.logger.Error("deployment recovery lacks application definition", "id", d.ID)
			pending = append(pending, d.ID)
			continue
		}
		unitName := app.UnitName
		if unitName == "" {
			unitName = "nodesteer-" + d.ApplicationID + ".service"
		}
		if !strings.HasSuffix(unitName, ".service") {
			unitName += ".service"
		}
		if err := validateUnitName(unitName); err != nil {
			am.logger.Error("invalid recovery unit", "id", d.ID, "error", err)
			pending = append(pending, d.ID)
			continue
		}
		configPath := app.ConfigPath
		if configPath == "" {
			configPath = "/etc/" + strings.TrimSuffix(unitName, ".service") + ".conf"
		}
		if d.Phase == PhasePreparing {
			// 准备阶段及旧记录均不能证明已经替换；保留所有文件，只尝试唤醒登记的旧服务。
			if registered, err := am.store.IsUnitRegistered(ctx, app.ID, unitName); err == nil && registered {
				if _, err := am.host.Stat(ctx, app.BinaryPath); err == nil {
					if state, _ := am.host.ServiceStatus(ctx, unitName); state != "active" {
						if err := am.host.StartService(ctx, unitName); err != nil {
							am.logger.Error("restore preparing service failed", "id", d.ID, "error", err)
						}
					}
				}
			}
			if am.closeUnreplayable(ctx, d, CompletedKindPrepareInterrupted,
				"preparing phase interrupted; files preserved, no replay") {
				closed[CompletedKindPrepareInterrupted]++
			} else {
				pending = append(pending, d.ID)
			}
			continue
		}
		if !deploymentBindingMatches(d, &app, unitName, configPath) {
			// 旧行缺绑定字段或定义已漂移：不得推断 backup 归属，不重放。
			kind, detail := CompletedKindStaleBinding,
				"journal binding does not match the current application definition; backup not replayed"
			if !hasDeploymentBinding(d) {
				kind, detail = CompletedKindLegacyUnbound,
					"legacy journal lacks deployment binding; backup ownership unknown, not replayed"
			}
			if am.closeUnreplayable(ctx, d, kind, detail) {
				closed[kind]++
			} else {
				pending = append(pending, d.ID)
			}
			continue
		}
		if err := am.store.UpdateDeploymentPhase(ctx, d.ID, PhaseRollingBack, d.BackupPath); err != nil {
			am.logger.Error("save recovery phase failed", "id", d.ID, "error", err)
			pending = append(pending, d.ID)
			continue
		}
		// 重放以 journal 绑定路径为准（已校验与当前定义一致）。
		if !am.rollback(ctx, d.BinaryPath, d.BackupPath, d.UnitName, d.ConfigPath, d.BackupConfigPath, d.BackupUnitPath) {
			if err := am.store.UpdateDeploymentPhase(ctx, d.ID, PhaseRollbackFailed, d.BackupPath); err != nil {
				am.logger.Error("mark rollback failed journal failed", "id", d.ID, "error", err)
			}
			am.logger.Error("deployment rollback failed; journal retained", "id", d.ID)
			pending = append(pending, d.ID)
			continue
		}
		if err := am.store.CompleteDeployment(ctx, d.ID, CompletedKindRolledBack, d.BackupPath); err != nil {
			am.logger.Error("finish recovery journal failed", "id", d.ID, "error", err)
			pending = append(pending, d.ID)
		}
	}
	am.logRecoverySummary(closed, pending)
	// 恢复完成后刷新部署 journal 只读汇总文件（CR-3 查询面），失败不影响恢复结果。
	am.writeDeploymentsSummary(ctx)
}

// closeUnreplayable 把恢复阶段判定为不可重放的 journal 行落终态（DONE + completed_kind），
// 使其离开活动部署集合、重启不再重复告警；终态成因均不等于 deployed，不会构成取代依据。
// 返回 false 表示写入失败，行保持活动，下次启动重试。
func (am *ApplicationManager) closeUnreplayable(ctx context.Context, d *DeploymentRow, kind, detail string) bool {
	if err := am.store.CompleteDeployment(ctx, d.ID, kind, d.BackupPath); err != nil {
		am.logger.Error("close unreplayable deployment journal failed", "id", d.ID, "app", d.ApplicationID, "kind", kind, "error", err)
		return false
	}
	am.logger.Error("unreplayable deployment journal closed", "id", d.ID, "app", d.ApplicationID, "kind", kind, "detail", detail)
	return true
}

// logRecoverySummary 汇总本次恢复的处置结果：被关闭的不可重放记录按成因计数，
// 仍留在活动集合（无法判定或重放/写入失败）的记录列出 ID，避免静默悬挂。
func (am *ApplicationManager) logRecoverySummary(closed map[string]int, pending []string) {
	var parts []string
	for _, kind := range []string{
		CompletedKindSuperseded,
		CompletedKindStaleBinding,
		CompletedKindLegacyUnbound,
		CompletedKindPrepareInterrupted,
	} {
		if n := closed[kind]; n > 0 {
			parts = append(parts, fmt.Sprintf("%s=%d", kind, n))
		}
	}
	if len(parts) > 0 {
		am.logger.Warn("unreplayable deployment journals closed by recovery", "closed", strings.Join(parts, ","))
	}
	if len(pending) > 0 {
		am.logger.Warn("deployment journals still active after recovery", "ids", strings.Join(pending, ","), "count", len(pending))
	}
}

// deploymentBindingMatches 校验 journal 记录（app revision 与三路径）与当前应用定义一致。
// 记录缺失或任一项不一致都返回 false：旧行缺新字段按“未知→不重放”处理，不可误覆盖。
func deploymentBindingMatches(d *DeploymentRow, app *models.Application, unitName, configPath string) bool {
	if !hasDeploymentBinding(d) {
		return false
	}
	return d.AppRevision == app.Revision &&
		d.BinaryPath == app.BinaryPath &&
		d.ConfigPath == configPath &&
		d.UnitName == unitName
}

// hasDeploymentBinding 判断行是否带有完整绑定字段；缺任一字段（旧行）视为“未知”。
func hasDeploymentBinding(d *DeploymentRow) bool {
	return d.AppRevision > 0 && d.BinaryPath != "" && d.ConfigPath != "" && d.UnitName != ""
}

// supersededByLaterDoneDeployment 判定半完成部署是否已被同应用更晚的成功部署取代。
// 只有终态成因（completed_kind）为 deployed 的 DONE 行构成取代：prepare_failed 未做任何
// 宿主替换，rolled_back 表示该次尝试已回到起点，两者都不代表装了更新版本；旧行缺该字段
// 视为未知，同样不构成取代（其绑定列也为空，恢复侧由 binding 校验兜住不重放）。
// histories 以 application_id 为键缓存部署历史，避免逐行重复查询。
func (am *ApplicationManager) supersededByLaterDoneDeployment(ctx context.Context, d *DeploymentRow, histories map[string][]*DeploymentRow) (bool, error) {
	history, ok := histories[d.ApplicationID]
	if !ok {
		var err error
		history, err = am.store.ListDeploymentsByApplication(ctx, d.ApplicationID)
		if err != nil {
			return false, err
		}
		histories[d.ApplicationID] = history
	}
	for _, other := range history {
		if other.ID == d.ID || other.Phase != PhaseDone || other.CompletedKind != CompletedKindDeployed {
			continue
		}
		if deploymentStartedAfter(other.StartedAt, d.StartedAt) {
			return true, nil
		}
	}
	return false, nil
}

// deploymentStartedAfter 解析后比较 started_at。不能直接字符串比较：
// time.RFC3339Nano 省略尾随零（如 ".5Z" 字典序小于 "Z"），会得出错误先后关系。
func deploymentStartedAfter(a, b string) bool {
	ta, errA := time.Parse(time.RFC3339Nano, a)
	tb, errB := time.Parse(time.RFC3339Nano, b)
	if errA == nil && errB == nil {
		return ta.After(tb)
	}
	return a > b
}

// validateUnitEmbeddedPath 校验会写入 systemd Unit 的路径：控制字符（含换行）
// 会破坏 Unit 语法，必须拒绝。
func validateUnitEmbeddedPath(path string) error {
	for _, r := range path {
		if r < 0x20 || r == 0x7f {
			return fmt.Errorf("invalid deployment path: control character in path")
		}
	}
	return nil
}

func validateUnitName(name string) error {
	if name == "" || strings.ContainsAny(name, "/\\") || strings.Contains(name, "..") {
		return fmt.Errorf("invalid systemd unit name: %s", name)
	}
	for _, r := range name {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') ||
			(r >= '0' && r <= '9') || r == '-' || r == '_' || r == '.' || r == '@' {
			continue
		}
		return fmt.Errorf("invalid systemd unit name: %s", name)
	}
	return nil
}

func unitQuote(value string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range value {
		if r == '\\' || r == '"' {
			b.WriteByte('\\')
		}
		b.WriteRune(r)
	}
	b.WriteByte('"')
	return b.String()
}
