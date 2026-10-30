package hub

import (
	"fmt"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// DefaultExecutionTimeout 调度或 Manual Run 未提供超时时的默认秒数。
const DefaultExecutionTimeout = 300

// ExecutionStrategy 执行策略：调度计划或 Manual Run 请求在下发执行时使用的
// Timeout/Retry/RunUser/OfflinePolicy。Task 不再持有这些字段。
type ExecutionStrategy struct {
	Timeout       int    `json:"timeout"`
	Retry         int    `json:"retry"`
	RunUser       string `json:"run_user,omitempty"`
	OfflinePolicy string `json:"offline_policy"`
}

// StrategyFromSchedule 从 Schedule 构造执行策略。
func StrategyFromSchedule(s *models.Schedule) ExecutionStrategy {
	if s == nil {
		return ExecutionStrategy{}
	}
	return ExecutionStrategy{
		Timeout:       s.Timeout,
		Retry:         s.Retry,
		RunUser:       s.RunUser,
		OfflinePolicy: s.OfflinePolicy,
	}
}

// Normalize 补默认值并校验策略。Timeout 0 归一为 300，负数拒绝；
// Retry 负数拒绝；OfflinePolicy 空值归一为 Hub Online Required。
func (s ExecutionStrategy) Normalize() (ExecutionStrategy, error) {
	if s.Timeout < 0 {
		return s, fmt.Errorf("timeout cannot be negative")
	}
	if s.Timeout == 0 {
		s.Timeout = DefaultExecutionTimeout
	}
	if s.Retry < 0 {
		return s, fmt.Errorf("retry cannot be negative")
	}
	if s.OfflinePolicy == "" {
		s.OfflinePolicy = models.OfflinePolicyHubOnlineRequired
	}
	if s.OfflinePolicy != models.OfflinePolicyHubOnlineRequired && s.OfflinePolicy != models.OfflinePolicyAllowOffline {
		return s, fmt.Errorf("invalid offline_policy")
	}
	return s, nil
}
