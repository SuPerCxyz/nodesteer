package hubserver

import (
	"context"
	"log/slog"
	"time"
)

// nativeMetricMaintenanceInterval 是原生指标聚合/清理周期。
const nativeMetricMaintenanceInterval = time.Minute

// nativeMetricInitialDelay 延迟 Hub 启动后的首轮聚合，先让 Agent HELLO/心跳
// 完成，避免启动瞬间的聚合写锁与连接写路径竞争。
const nativeMetricInitialDelay = 30 * time.Second

// startNativeMetricMaintenance 启动周期聚合与清理任务，返回在退出时关闭的 done。
// interval<=0 使用默认周期；首轮不延迟（测试/显式调用）。
func startNativeMetricMaintenance(
	ctx context.Context,
	interval time.Duration,
	aggregate func(context.Context, time.Time) error,
	prune func(context.Context, time.Time) error,
	logger *slog.Logger,
) chan struct{} {
	return startNativeMetricMaintenanceWithDelay(ctx, interval, 0, aggregate, prune, logger)
}

// startNativeMetricMaintenanceWithDelay 允许 Hub 延迟首轮聚合；ctx 取消后停止，
// 调用方可等待 done 确认 goroutine 已退出。单个任务失败只记录日志，不影响后续周期。
func startNativeMetricMaintenanceWithDelay(
	ctx context.Context,
	interval time.Duration,
	initialDelay time.Duration,
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
		if initialDelay > 0 {
			timer := time.NewTimer(initialDelay)
			select {
			case <-ctx.Done():
				timer.Stop()
				return
			case now := <-timer.C:
				run(now.UTC())
			}
		} else {
			run(time.Now().UTC())
		}
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
