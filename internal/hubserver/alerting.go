package hubserver

import (
	"context"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

// alertEvaluationInterval 是阈值告警评估周期；评估器内部也提供 SetInterval
// 供测试注入更短周期。
const alertEvaluationInterval = monitoring.DefaultAlertEvaluationInterval

type alertEvaluationRunner interface {
	Run(context.Context)
}

// startAlertEvaluation 启动告警评估/通知循环，返回退出时关闭的 done。
// ctx 取消后循环停止，调用方可等待 done 确认 goroutine 已退出。
func startAlertEvaluation(ctx context.Context, runner alertEvaluationRunner) chan struct{} {
	done := make(chan struct{})
	if runner == nil {
		close(done)
		return done
	}
	go func() {
		defer close(done)
		runner.Run(ctx)
	}()
	return done
}
