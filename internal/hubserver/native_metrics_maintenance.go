package hubserver

import (
	"context"
	"log/slog"
	"time"
)

// nativeMetricMaintenanceInterval 是原生指标聚合/清理周期。
const nativeMetricMaintenanceInterval = time.Minute

// startNativeMetricMaintenance 启动周期聚合与清理任务，返回在退出时关闭的 done。
// 启动后立即执行一次，避免重启后等待完整周期；ctx 取消后停止，调用方可等待 done
// 确认 goroutine 已退出。单个任务失败只记录日志，不影响后续周期或主服务。
func startNativeMetricMaintenance(
	ctx context.Context,
	interval time.Duration,
	aggregate func(context.Context, time.Time) error,
	prune func(context.Context, time.Time) error,
	logger *slog.Logger,
) chan struct{} {
	done := make(chan struct{})
	if interval <= 0 {
		interval = nativeMetricMaintenanceInterval
	}
	go func() {
		defer close(done)
		run := func(now time.Time) {
			if aggregate != nil {
				if err := aggregate(ctx, now); err != nil {
					if ctx.Err() == nil && logger != nil {
						logger.Error("native metric aggregation failed", "error", err)
					}
					// 聚合失败时跳过本轮清理，避免在 rollup 未收敛时删除源数据。
					return
				}
			}
			if prune != nil {
				if err := prune(ctx, now); err != nil && ctx.Err() == nil && logger != nil {
					logger.Error("native metric pruning failed", "error", err)
				}
			}
		}
		run(time.Now().UTC())
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-ticker.C:
				run(now.UTC())
			}
		}
	}()
	return done
}
