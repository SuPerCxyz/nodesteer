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
	deploymentID := uuid.NewString()
	res := protocol.DeployResultPayload{AppID: p.AppID, Operation: p.Operation}
	res.Version = p.AppVersion
	var app models.Application
	if len(appDef) > 0 {
		_ = json.Unmarshal(appDef, &app)
	}

	// 写 Deployment Journal
	now := time.Now().UTC().Format(time.RFC3339Nano)
	if err := am.store.CreateDeployment(ctx, &DeploymentRow{
		ID: deploymentID, ApplicationID: p.AppID, FromVersion: app.Version, ToVersion: p.AppVersion,
		Phase: PhasePreparing, StartedAt: now, UpdatedAt: now,
	}); err != nil {
		res.OK = false
		res.Error = "create deployment journal failed: " + err.Error()
		cb(res)
		return
	}

	// 下载 Artifact
	artifactPath := ""
	if p.ArtifactURL != "" {
		if p.ArtifactSHA256 == "" || am.artifact == nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
			res.OK = false
			res.Error = "artifact sha256/cache is missing"
			cb(res)
			return
		}
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhasePreparing, "")
		if err := am.artifact.Prefetch(ctx, p.ArtifactURL, p.ArtifactSHA256); err != nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
			res.OK = false
			res.Error = "artifact download failed: " + err.Error()
			cb(res)
			return
		}
		artifactPath = am.artifact.Path(p.ArtifactSHA256)
	}

	// unitName 规范化（用于备份停止、安装与操作）
	unitName := p.UnitName
	if unitName == "" {
		unitName = "nodesteer-" + p.AppID + ".service"
	}
	if !strings.HasSuffix(unitName, ".service") {
		unitName += ".service"
	}
	if err := validateUnitName(unitName); err != nil {
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
		res.Error = err.Error()
		cb(res)
		return
	}

	// Backup（升级场景）：备份二进制 + config + unit，供失败回滚
	backupPath := ""
	backupConfigPath := ""
	backupUnitPath := ""
	binaryPath := p.BinaryPath
	if binaryPath == "" {
		binaryPath = app.BinaryPath
	}
	if binaryPath == "" {
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
		res.Error = "binary path is required"
		cb(res)
		return
	}
	configPath := p.ConfigPath
	if configPath == "" {
		configPath = "/etc/" + strings.TrimSuffix(unitName, ".service") + ".conf"
	}
	p.ConfigPath = configPath
	if err := am.host.ValidatePath(binaryPath); err != nil {
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
		res.OK = false
		res.Error = "invalid binary path: " + err.Error()
		cb(res)
		return
	}
	if err := am.host.ValidatePath(configPath); err != nil {
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
		res.OK = false
		res.Error = "invalid config path: " + err.Error()
		cb(res)
		return
	}
	backupRoot := filepath.Join(am.store.Dir(), "applications", "backup-"+uuid.NewString())
	if err := os.MkdirAll(filepath.Dir(backupRoot), 0o755); err != nil {
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
		res.OK = false
		res.Error = "create backup directory failed: " + err.Error()
		cb(res)
		return
	}
	if _, err := am.host.Stat(ctx, binaryPath); err == nil {
		data, readErr := am.host.ReadFile(ctx, binaryPath)
		if readErr != nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
			res.OK = false
			res.Error = "backup binary failed: " + readErr.Error()
			cb(res)
			return
		}
		backupPath = backupRoot + filepath.Ext(binaryPath)
		if err := os.MkdirAll(filepath.Dir(backupPath), 0o755); err != nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
			res.OK = false
			res.Error = "create backup directory failed: " + err.Error()
			cb(res)
			return
		}
		if err := os.WriteFile(backupPath, data, 0o755); err != nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, "")
			res.OK = false
			res.Error = "backup binary failed: " + err.Error()
			cb(res)
			return
		}
	}
	// config/unit 即使没有旧二进制也要分别记录，避免首次部署回滚时误删旧文件。
	if _, err := am.host.Stat(ctx, configPath); err == nil {
		data, readErr := am.host.ReadFile(ctx, configPath)
		if readErr != nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
			res.OK = false
			res.Error = "backup config failed: " + readErr.Error()
			cb(res)
			return
		}
		backupConfigPath = backupRoot + ".conf"
		if err := os.WriteFile(backupConfigPath, data, 0o644); err != nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
			res.OK = false
			res.Error = "backup config failed: " + err.Error()
			cb(res)
			return
		}
	}
	unitPath := "/etc/systemd/system/" + unitName
	if _, err := am.host.Stat(ctx, unitPath); err == nil {
		data, readErr := am.host.ReadFile(ctx, unitPath)
		if readErr != nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
			res.OK = false
			res.Error = "backup unit failed: " + readErr.Error()
			cb(res)
			return
		}
		backupUnitPath = backupRoot + ".unit"
		if err := os.WriteFile(backupUnitPath, data, 0o644); err != nil {
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
			res.OK = false
			res.Error = "backup unit failed: " + err.Error()
			cb(res)
			return
		}
	}
	// 升级/重新部署场景：先停止已运行服务，确保替换后新进程生效。
	if _, err := am.host.ServiceStatus(ctx, unitName); err == nil {
		am.host.StopService(ctx, unitName)
		am.logger.Info("stopped service before replace", "unit", unitName)
	}
	if backupPath != "" || backupConfigPath != "" || backupUnitPath != "" {
		am.store.SetDeploymentBackups(ctx, deploymentID, backupPath, backupConfigPath, backupUnitPath)
	}

	// 安装新二进制
	if artifactPath != "" && binaryPath != "" {
		data, err := os.ReadFile(artifactPath)
		if err != nil {
			am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, models.HealthCheck{})
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
			res.OK = false
			res.Error = "read artifact failed: " + err.Error()
			cb(res)
			return
		}
		if err := am.host.AtomicReplace(ctx, binaryPath, data, 0o755); err != nil {
			am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, models.HealthCheck{})
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
			res.OK = false
			res.Error = "install binary failed: " + err.Error()
			cb(res)
			return
		}
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseReplaced, backupPath)
	}

	// 配置
	if p.Config != "" {
		if err := am.host.AtomicReplace(ctx, configPath, []byte(p.Config), 0o644); err != nil {
			am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, models.HealthCheck{})
			am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
			res.OK = false
			res.Error = "config write failed: " + err.Error()
			cb(res)
			return
		}
	}

	// systemd Unit
	unitContent := am.buildUnit(unitName, p, binaryPath)
	if err := am.host.InstallUnit(ctx, unitName, unitContent); err != nil {
		am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, models.HealthCheck{})
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
		res.OK = false
		res.Error = "install unit failed: " + err.Error()
		cb(res)
		return
	}
	if err := am.host.DaemonReload(ctx); err != nil {
		am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, models.HealthCheck{})
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
		res.OK = false
		res.Error = "daemon reload failed: " + err.Error()
		cb(res)
		return
	}
	if err := am.host.EnableService(ctx, unitName); err != nil {
		am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, models.HealthCheck{})
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
		res.OK = false
		res.Error = "enable service failed: " + err.Error()
		cb(res)
		return
	}

	// Start + Health Check
	am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseStarted, backupPath)
	if err := am.host.StartService(ctx, unitName); err != nil {
		am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, models.HealthCheck{})
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
		res.OK = false
		res.Error = "start service failed: " + err.Error()
		cb(res)
		return
	}

	// Health Check
	am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseVerifying, backupPath)
	var hc models.HealthCheck
	if len(p.HealthCheck) > 0 {
		json.Unmarshal(p.HealthCheck, &hc)
	} else if app.HealthCheck != nil {
		hc = *app.HealthCheck
	}
	healthy := am.checkHealth(ctx, hc, unitName)

	if !healthy {
		// Rollback：恢复 binary/config/unit，回滚后 Health Check
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseRollingBack, backupPath)
		rollbackHealthy := am.rollback(ctx, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath, hc)
		res.OK = false
		res.Rollback = true
		res.Health = "unhealthy"
		res.Error = "health check failed; rolled back"
		if !rollbackHealthy {
			res.Error += " (rollback health check also failed)"
		}
		am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
		cb(res)
		return
	}

	am.store.UpdateDeploymentPhase(ctx, deploymentID, PhaseDone, backupPath)
	// 登记 Managed Unit（systemd Unit Registry）
	if err := am.store.RegisterUnit(ctx, p.AppID, unitName); err != nil {
		am.logger.Warn("register unit failed", "app", p.AppID, "unit", unitName, "error", err)
	}
	res.OK = true
	res.Health = "healthy"
	res.Version = p.AppVersion
	cb(res)
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
		b.WriteString("EnvironmentFile=" + unitQuote("-"+p.ConfigPath) + "\n")
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

// rollback 回滚：恢复二进制 + config + unit，daemon-reload 后启动并做 Health Check
func (am *ApplicationManager) rollback(ctx context.Context, binaryPath, backupPath, unitName, configPath, backupConfigPath, backupUnitPath string, hc models.HealthCheck) bool {
	// 先停止当前运行的新版本进程，避免其继续占用端口
	am.host.StopService(ctx, unitName)
	cleaned := true
	// 首次部署没有备份：删除本次创建的二进制，不能把失败的新文件留下。
	if backupPath == "" && binaryPath != "" {
		if err := am.host.Remove(ctx, binaryPath); err != nil && !os.IsNotExist(err) {
			cleaned = false
		}
	}
	// 恢复二进制
	restoredBinary := false
	if backupPath != "" {
		if data, err := os.ReadFile(backupPath); err == nil && binaryPath != "" {
			if err := am.host.AtomicReplace(ctx, binaryPath, data, 0o755); err == nil {
				restoredBinary = true
			} else {
				cleaned = false
			}
		} else {
			cleaned = false
		}
	}
	// 恢复 config
	if backupConfigPath != "" && configPath != "" {
		if data, err := os.ReadFile(backupConfigPath); err == nil {
			if err := am.host.AtomicReplace(ctx, configPath, data, 0o644); err != nil {
				am.logger.Warn("rollback config failed", "path", configPath, "error", err)
				cleaned = false
			}
		} else {
			cleaned = false
		}
	} else if configPath != "" {
		if err := am.host.Remove(ctx, configPath); err != nil && !os.IsNotExist(err) {
			cleaned = false
		}
	}
	// 恢复 unit 内容
	if backupUnitPath != "" {
		if data, err := os.ReadFile(backupUnitPath); err == nil {
			if err := am.host.InstallUnit(ctx, unitName, string(data)); err != nil {
				am.logger.Warn("rollback unit failed", "unit", unitName, "error", err)
				cleaned = false
			}
		} else {
			cleaned = false
		}
	} else if unitName != "" {
		_ = am.host.DisableService(ctx, unitName)
		unitPath := "/etc/systemd/system/" + unitName
		if err := am.host.Remove(ctx, unitPath); err != nil && !os.IsNotExist(err) {
			cleaned = false
		}
	}
	am.host.DaemonReload(ctx)
	if restoredBinary {
		am.host.StartService(ctx, unitName)
	}
	// 回滚后 Health Check
	if hc.Type != "" {
		return am.checkHealth(ctx, hc, unitName)
	}
	return cleaned
}

// RecoverDeployments 启动恢复半完成部署
func (am *ApplicationManager) RecoverDeployments(ctx context.Context) {
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil {
		return
	}
	for _, d := range active {
		if d.Phase == PhaseDone {
			continue
		}
		// 半完成部署 → 回滚
		am.logger.Info("recovering incomplete deployment", "id", d.ID, "app", d.ApplicationID, "phase", d.Phase)
		appDef := am.loadApplication(d.ApplicationID)
		var app models.Application
		if len(appDef) > 0 {
			json.Unmarshal(appDef, &app)
		}
		unitName := "nodesteer-" + d.ApplicationID + ".service"
		if app.UnitName != "" {
			unitName = app.UnitName
		}
		var hc models.HealthCheck
		if app.HealthCheck != nil {
			hc = *app.HealthCheck
		}
		configPath := app.ConfigPath
		if configPath == "" {
			configPath = "/etc/" + strings.TrimSuffix(unitName, ".service") + ".conf"
		}
		am.rollback(ctx, app.BinaryPath, d.BackupPath, unitName, configPath, d.BackupConfigPath, d.BackupUnitPath, hc)
		am.store.UpdateDeploymentPhase(ctx, d.ID, PhaseDone, d.BackupPath)
	}
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
