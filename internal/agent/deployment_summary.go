package agent

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"time"
)

// 部署 journal 只读查询面（CR-3 后续）：
// 恢复完成后把 journal 的「非成功终态」与仍活动行原子写入
// <data_dir>/deployments-summary.json，供运维随时审计四类不可重放行
// （superseded / stale_binding / legacy_unbound / prepare_interrupted）
// 及其它非 deployed 终态。
//
// 取舍（最小侵入）：不改 Hub schema、不改心跳/同步协议、不新增持久化结构；
// 文件是恢复时刻的只读快照，每次恢复（Agent 启动）覆盖刷新。
// 写入失败只记日志，不影响恢复语义与 journal 状态（下次恢复重试）。
const deploymentsSummaryFileName = "deployments-summary.json"

// deploymentsSummary 只读汇总文件结构（运维消费契约）。
type deploymentsSummary struct {
	// GeneratedAt 本次刷新时间（RFC3339，UTC）
	GeneratedAt string `json:"generated_at"`
	// DoneByKind 非成功终态（DONE 且 completed_kind != deployed）按成因计数；
	// 旧行缺 completed_kind 归入 "unknown"。成功部署（deployed）不计入。
	DoneByKind map[string]int `json:"done_by_kind"`
	// Active 恢复后仍留在活动集合的行（无法判定 / 重放或写入失败）
	Active []deploymentsSummaryActive `json:"active"`
}

// deploymentsSummaryActive 仍活动的部署行（id / 应用 / 阶段）。
type deploymentsSummaryActive struct {
	ID            string `json:"id"`
	ApplicationID string `json:"application_id"`
	Phase         string `json:"phase"`
	StartedAt     string `json:"started_at"`
}

// buildDeploymentsSummary 从 journal 生成汇总快照。
func (am *ApplicationManager) buildDeploymentsSummary(ctx context.Context) (*deploymentsSummary, error) {
	counts, err := am.store.CountClosedDeploymentsByKind(ctx)
	if err != nil {
		return nil, err
	}
	byKind := make(map[string]int, len(counts))
	for kind, n := range counts {
		if kind == "" {
			kind = "unknown"
		}
		byKind[kind] = n
	}
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil {
		return nil, err
	}
	rows := make([]deploymentsSummaryActive, 0, len(active))
	for _, d := range active {
		rows = append(rows, deploymentsSummaryActive{
			ID:            d.ID,
			ApplicationID: d.ApplicationID,
			Phase:         d.Phase,
			StartedAt:     d.StartedAt,
		})
	}
	return &deploymentsSummary{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		DoneByKind:  byKind,
		Active:      rows,
	}, nil
}

// writeDeploymentsSummary 原子刷新只读汇总文件（先写 .tmp 再 rename，读方不会看到半截文件）。
// 成功写入不打日志（保持既有“重启不重复告警”的日志契约，文件内容与 mtime 即证据）；
// 任一步失败记错误日志并返回，不影响恢复主流程，下次恢复重试。
func (am *ApplicationManager) writeDeploymentsSummary(ctx context.Context) {
	summary, err := am.buildDeploymentsSummary(ctx)
	if err != nil {
		am.logger.Error("build deployments summary failed", "error", err)
		return
	}
	data, err := json.MarshalIndent(summary, "", "  ")
	if err != nil {
		am.logger.Error("encode deployments summary failed", "error", err)
		return
	}
	path := filepath.Join(am.store.Dir(), deploymentsSummaryFileName)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o644); err != nil {
		am.logger.Error("write deployments summary failed", "path", path, "error", err)
		return
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		am.logger.Error("replace deployments summary failed", "path", path, "error", err)
	}
}
