package monitoring

import (
	"errors"
	"fmt"
	"math"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// 原生指标阈值告警领域模型与纯状态机。
//
// 告警只负责通知与记录，绝不触发任务；任务联动仍由既有 monitoring.Rule
// 执行链负责。所有时间统一使用固定宽度 UTC 文本存储，保证 SQLite 文本比较
// 等价于时间比较。

const (
	AlertStatusOK       = "ok"
	AlertStatusPending  = "pending"
	AlertStatusFiring   = "firing"
	AlertStatusResolved = "resolved"
	AlertStatusSilenced = "silenced"
	AlertStatusUnknown  = "unknown"

	AlertSeverityInfo     = "info"
	AlertSeverityWarning  = "warning"
	AlertSeverityCritical = "critical"

	AlertOperatorGT  = "gt"
	AlertOperatorGTE = "gte"
	AlertOperatorLT  = "lt"
	AlertOperatorLTE = "lte"
	AlertOperatorEQ  = "eq"
	AlertOperatorNE  = "ne"

	AlertChannelWebhook  = "webhook"
	AlertChannelSlack    = "slack"
	AlertChannelDingTalk = "dingtalk"
	AlertChannelWeCom    = "wecom"
	AlertChannelTelegram = "telegram"
	AlertChannelEmail    = "email"

	// DefaultAlertEvaluationInterval 是告警规则评估周期。
	DefaultAlertEvaluationInterval = 30 * time.Second
	// DefaultAlertDispatchInterval 是通知发件箱扫描周期。
	DefaultAlertDispatchInterval = 5 * time.Second

	// alertTimeLayout 固定宽度 UTC 时间，字典序等于时间序。
	alertTimeLayout = "2006-01-02T15:04:05.000000000Z"

	alertMaxRuleName        = 128
	alertMaxDescription     = 1024
	alertMaxNodeID          = 128
	alertMaxSeries          = 1024
	alertMaxDurationSec     = 86400
	alertMaxRecoverySec     = 86400
	alertMaxCooldownSec     = 604800
	alertMaxChannelsPerRule = 32
)

var (
	ErrInvalidAlertRule      = errors.New("invalid alert rule")
	ErrInvalidNotificationCh = errors.New("invalid notification channel")
	ErrInvalidAlertEvent     = errors.New("invalid alert event")
	ErrNotificationConfig    = errors.New("notification channel configuration error")
	ErrChannelInUse          = errors.New("notification channel is referenced by alert rules")
	ErrChannelNameExists     = errors.New("notification channel name already exists")
	ErrAlertConflict         = errors.New("alert conflict")
	ErrAlertChannelNotFound  = errors.New("notification channel not found")
)

// AlertRule 是原生指标阈值规则。NodeID 为空表示所有节点，按 metric+series 匹配。
type AlertRule struct {
	ID           string     `json:"id"`
	Name         string     `json:"name"`
	Description  string     `json:"description,omitempty"`
	NodeID       string     `json:"node_id"`
	Metric       string     `json:"metric"`
	Series       string     `json:"series,omitempty"`
	Operator     string     `json:"operator"`
	Threshold    float64    `json:"threshold"`
	DurationSec  int        `json:"duration_sec"`
	RecoverySec  int        `json:"recovery_sec"`
	CooldownSec  int        `json:"cooldown_sec"`
	Severity     string     `json:"severity"`
	Channels     []string   `json:"channels"`
	Enabled      bool       `json:"enabled"`
	SilenceUntil *time.Time `json:"silenced_until,omitempty"`
	CreatedAt    time.Time  `json:"created_at"`
	UpdatedAt    time.Time  `json:"updated_at"`
}

// Validate 校验规则字段。错误文本只描述业务约束，不含存储层细节。
func (rule AlertRule) Validate() error {
	if strings.TrimSpace(rule.Name) == "" || len(rule.Name) > alertMaxRuleName {
		return fmt.Errorf("%w: name is required and must be at most %d characters", ErrInvalidAlertRule, alertMaxRuleName)
	}
	if len(rule.Description) > alertMaxDescription {
		return fmt.Errorf("%w: description is too long", ErrInvalidAlertRule)
	}
	if len(rule.NodeID) > alertMaxNodeID || containsControlCharacters(rule.NodeID) {
		return fmt.Errorf("%w: invalid node id", ErrInvalidAlertRule)
	}
	if !store.ValidNativeMetric(rule.Metric) {
		return fmt.Errorf("%w: invalid metric", ErrInvalidAlertRule)
	}
	if len(rule.Series) > alertMaxSeries || containsControlCharacters(rule.Series) {
		return fmt.Errorf("%w: invalid series", ErrInvalidAlertRule)
	}
	if rule.Series != "" {
		metric, _, ok := store.ParseSeriesKey(rule.Series)
		if !ok || metric != rule.Metric {
			return fmt.Errorf("%w: series does not belong to metric", ErrInvalidAlertRule)
		}
	}
	if !ValidAlertOperator(rule.Operator) {
		return fmt.Errorf("%w: unsupported operator", ErrInvalidAlertRule)
	}
	if math.IsNaN(rule.Threshold) || math.IsInf(rule.Threshold, 0) || math.Abs(rule.Threshold) >= 1<<53 {
		return fmt.Errorf("%w: invalid threshold", ErrInvalidAlertRule)
	}
	if rule.DurationSec < 0 || rule.DurationSec > alertMaxDurationSec {
		return fmt.Errorf("%w: invalid duration_sec", ErrInvalidAlertRule)
	}
	if rule.RecoverySec < 0 || rule.RecoverySec > alertMaxRecoverySec {
		return fmt.Errorf("%w: invalid recovery_sec", ErrInvalidAlertRule)
	}
	if rule.CooldownSec < 0 || rule.CooldownSec > alertMaxCooldownSec {
		return fmt.Errorf("%w: invalid cooldown_sec", ErrInvalidAlertRule)
	}
	if !ValidAlertSeverity(rule.Severity) {
		return fmt.Errorf("%w: invalid severity", ErrInvalidAlertRule)
	}
	if len(rule.Channels) > alertMaxChannelsPerRule {
		return fmt.Errorf("%w: too many channels", ErrInvalidAlertRule)
	}
	seen := map[string]bool{}
	for _, id := range rule.Channels {
		if id == "" || len(id) > 128 || containsControlCharacters(id) {
			return fmt.Errorf("%w: invalid channel id", ErrInvalidAlertRule)
		}
		if seen[id] {
			return fmt.Errorf("%w: duplicate channel id", ErrInvalidAlertRule)
		}
		seen[id] = true
	}
	return nil
}

// Normalize 去重渠道并补齐默认值，调用前必须已通过 Validate。
func (rule *AlertRule) Normalize() {
	if rule.Channels == nil {
		rule.Channels = []string{}
	}
	if rule.Severity == "" {
		rule.Severity = AlertSeverityWarning
	}
}

// SilenceActive 返回规则在 now 时刻是否处于静默。
func (rule AlertRule) SilenceActive(now time.Time) bool {
	return rule.SilenceUntil != nil && rule.SilenceUntil.After(now)
}

// NotificationChannel 通知渠道。Config 只包含非敏感字段与环境变量名引用。
type NotificationChannel struct {
	ID        string        `json:"id"`
	Name      string        `json:"name"`
	Type      string        `json:"type"`
	Config    ChannelConfig `json:"config"`
	Enabled   bool          `json:"enabled"`
	CreatedAt time.Time     `json:"created_at"`
	UpdatedAt time.Time     `json:"updated_at"`
}

// Validate 校验渠道类型与配置；敏感值必须通过环境变量名引用。
func (channel NotificationChannel) Validate() error {
	if strings.TrimSpace(channel.Name) == "" || len(channel.Name) > 128 {
		return fmt.Errorf("%w: name is required and must be at most 128 characters", ErrInvalidNotificationCh)
	}
	if !ValidAlertChannelType(channel.Type) {
		return fmt.Errorf("%w: unsupported channel type", ErrInvalidNotificationCh)
	}
	return channel.Config.Validate(channel.Type)
}

// AlertState 是同一规则+节点+指标+序列的当前评估状态。
type AlertState struct {
	RuleID          string    `json:"rule_id"`
	NodeID          string    `json:"node_id"`
	Metric          string    `json:"metric"`
	Series          string    `json:"series"`
	Status          string    `json:"status"`
	ConditionSince  time.Time `json:"condition_since"`
	RecoverySince   time.Time `json:"recovery_since"`
	LastValue       *float64  `json:"last_value"`
	LastObservedAt  time.Time `json:"last_observed_at"`
	LastEvaluatedAt time.Time `json:"last_evaluated_at"`
	LastNotifiedAt  time.Time `json:"last_notified_at"`
	EventID         string    `json:"event_id"`
	Message         string    `json:"message"`
	UpdatedAt       time.Time `json:"updated_at"`
}

// AlertEvent 是告警事件历史。活动事件通过部分唯一索引保证去重。
type AlertEvent struct {
	ID             string    `json:"id"`
	RuleID         string    `json:"rule_id"`
	RuleName       string    `json:"rule_name"`
	NodeID         string    `json:"node_id"`
	NodeHostname   string    `json:"node_hostname"`
	Metric         string    `json:"metric"`
	Series         string    `json:"series"`
	Operator       string    `json:"operator"`
	Status         string    `json:"status"`
	Severity       string    `json:"severity"`
	Value          *float64  `json:"value"`
	Threshold      float64   `json:"threshold"`
	StartedAt      time.Time `json:"started_at"`
	FiredAt        time.Time `json:"fired_at"`
	ResolvedAt     time.Time `json:"resolved_at"`
	AcknowledgedAt time.Time `json:"acknowledged_at"`
	AcknowledgedBy string    `json:"acknowledged_by"`
	SilencedUntil  time.Time `json:"silenced_until"`
	Message        string    `json:"message"`
	Error          string    `json:"error"`
	Notified       bool      `json:"notified"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

// AlertEventFilter 事件列表过滤条件。
type AlertEventFilter struct {
	State    string
	Status   string
	RuleID   string
	NodeID   string
	Severity string
	Since    time.Time
	Until    time.Time
	Limit    int
	Offset   int
}

// AlertNotification 是通知发件箱记录。
type AlertNotification struct {
	ID            string    `json:"id"`
	EventID       string    `json:"event_id"`
	ChannelID     string    `json:"channel_id"`
	ChannelType   string    `json:"channel_type"`
	Status        string    `json:"status"`
	Attempts      int       `json:"attempts"`
	LastError     string    `json:"last_error"`
	NextAttemptAt time.Time `json:"next_attempt_at"`
	SentAt        time.Time `json:"sent_at"`
	CreatedAt     time.Time `json:"created_at"`
	UpdatedAt     time.Time `json:"updated_at"`
}

// AlertNotificationAttempt 是一次实际发送尝试。
type AlertNotificationAttempt struct {
	Attempt     int       `json:"attempt"`
	Status      string    `json:"status"`
	Error       string    `json:"error"`
	AttemptedAt time.Time `json:"attempted_at"`
}

// AlertNotificationRecord 是发件箱记录及其尝试明细。
type AlertNotificationRecord struct {
	AlertNotification
	AttemptDetails []AlertNotificationAttempt `json:"attempt_details"`
}

// AlertNotificationFilter 是通知发送记录查询条件。
type AlertNotificationFilter struct {
	EventID   string
	ChannelID string
	Status    string
	Limit     int
	Offset    int
}

// AlertEvaluation 是一次样本评估结果。Available=false 表示 fail-closed：
// 无样本、样本过期、节点离线或查询失败时不触发也不恢复。
type AlertEvaluation struct {
	Available    bool
	Reason       string
	NodeID       string
	NodeHostname string
	Series       string
	Value        float64
	ObservedAt   time.Time
}

// AlertDecision 是状态机输出：新状态、事件变更以及是否发送通知。
type AlertDecision struct {
	State  AlertState
	Event  *AlertEvent
	Notify bool
}

func ValidAlertOperator(operator string) bool {
	switch operator {
	case AlertOperatorGT, AlertOperatorGTE, AlertOperatorLT, AlertOperatorLTE, AlertOperatorEQ, AlertOperatorNE:
		return true
	default:
		return false
	}
}

func ValidAlertSeverity(severity string) bool {
	switch severity {
	case AlertSeverityInfo, AlertSeverityWarning, AlertSeverityCritical:
		return true
	default:
		return false
	}
}

// ValidNotificationStatus 校验发件箱记录状态。
func ValidNotificationStatus(status string) bool {
	switch status {
	case "pending", "sent", "failed":
		return true
	default:
		return false
	}
}

func ValidAlertChannelType(channelType string) bool {
	switch channelType {
	case AlertChannelWebhook, AlertChannelSlack, AlertChannelDingTalk, AlertChannelWeCom, AlertChannelTelegram, AlertChannelEmail:
		return true
	default:
		return false
	}
}

// CompareAlertValue 按操作符比较样本值与阈值。调用方必须先确认值是有限数。
func CompareAlertValue(operator string, value, threshold float64) bool {
	switch operator {
	case AlertOperatorGT:
		return value > threshold
	case AlertOperatorGTE:
		return value >= threshold
	case AlertOperatorLT:
		return value < threshold
	case AlertOperatorLTE:
		return value <= threshold
	case AlertOperatorEQ:
		return value == threshold
	case AlertOperatorNE:
		return value != threshold
	default:
		return false
	}
}

func containsControlCharacters(value string) bool {
	for index := 0; index < len(value); index++ {
		if value[index] < 0x20 || value[index] == 0x7f {
			return true
		}
	}
	return false
}

func formatAlertTime(value time.Time) string {
	if value.IsZero() {
		return ""
	}
	return value.UTC().Format(alertTimeLayout)
}

func parseAlertTime(value string) (time.Time, error) {
	if value == "" {
		return time.Time{}, nil
	}
	return time.Parse(alertTimeLayout, value)
}

// EvaluateAlertDecision 是纯状态机，负责持续时间、恢复时间、冷却、静默与去重。
//
// current 是 state.EventID 指向的活动事件；不存在时传 nil。函数不访问存储、
// 不生成事件 ID，由仓储层在事务内补齐。
func EvaluateAlertDecision(rule AlertRule, state AlertState, current *AlertEvent, evaluation AlertEvaluation, now time.Time) AlertDecision {
	now = now.UTC()
	if state.Status == "" {
		state.Status = AlertStatusOK
	}
	state.RuleID = rule.ID
	state.NodeID = evaluation.NodeID
	state.Metric = rule.Metric
	state.Series = evaluation.Series
	state.LastEvaluatedAt = now
	state.UpdatedAt = now

	decision := AlertDecision{State: state}
	if !evaluation.Available {
		decision.State.Message = "data unavailable: " + evaluation.Reason
		switch state.Status {
		case AlertStatusPending:
			// 连续性被破坏：取消未达持续时间的 pending 事件，但不触发通知。
			if current != nil {
				event := *current
				event.Status = AlertStatusResolved
				event.ResolvedAt = now
				event.Message = "condition cleared: data unavailable"
				event.UpdatedAt = now
				decision.Event = &event
			}
			decision.State.Status = AlertStatusUnknown
			decision.State.ConditionSince = time.Time{}
			decision.State.RecoverySince = time.Time{}
			decision.State.EventID = ""
		case AlertStatusFiring, AlertStatusSilenced:
			// fail-closed：数据不可用不能证明恢复，保持活动状态，等待有效样本。
			decision.State.Status = state.Status
		default:
			decision.State.Status = AlertStatusUnknown
			decision.State.ConditionSince = time.Time{}
			decision.State.RecoverySince = time.Time{}
			decision.State.EventID = ""
		}
		return decision
	}

	state.LastValue = float64Ptr(evaluation.Value)
	state.LastObservedAt = evaluation.ObservedAt.UTC()
	decision.State.LastValue = state.LastValue
	decision.State.LastObservedAt = state.LastObservedAt
	matched := CompareAlertValue(rule.Operator, evaluation.Value, rule.Threshold)
	silenced := rule.SilenceActive(now)

	if matched {
		decision.State.RecoverySince = time.Time{}
		switch state.Status {
		case AlertStatusFiring, AlertStatusSilenced:
			event := eventForDecision(rule, state, current, evaluation, now)
			event.Value = float64Ptr(evaluation.Value)
			event.UpdatedAt = now
			if silenced {
				event.Status = AlertStatusSilenced
				event.Message = "silenced"
				event.SilencedUntil = rule.SilenceUntil.UTC()
				decision.State.Status = AlertStatusSilenced
				decision.State.Message = "silenced until " + formatAlertTime(*rule.SilenceUntil)
			} else {
				event.Status = AlertStatusFiring
				event.Message = "firing"
				event.SilencedUntil = time.Time{}
				decision.State.Status = AlertStatusFiring
				decision.State.Message = ""
			}
			decision.State.EventID = event.ID
			decision.Event = event
			return decision

		case AlertStatusPending:
			if state.ConditionSince.IsZero() {
				state.ConditionSince = now
				decision.State.ConditionSince = now
			}
			if now.Sub(state.ConditionSince) < time.Duration(rule.DurationSec)*time.Second {
				event := eventForDecision(rule, state, current, evaluation, now)
				event.Status = AlertStatusPending
				event.StartedAt = state.ConditionSince
				event.Value = float64Ptr(evaluation.Value)
				event.Message = "condition met, waiting for duration"
				event.UpdatedAt = now
				decision.State.EventID = event.ID
				decision.Event = event
				return decision
			}
			// 持续时间满足：触发。冷却只抑制通知，不抑制事件。
			return alertFireDecision(rule, decision.State, current, evaluation, now, silenced)

		default:
			decision.State.ConditionSince = now
			decision.State.RecoverySince = time.Time{}
			// duration_sec=0 表示条件首次满足即触发，不额外等待一个评估周期。
			if rule.DurationSec <= 0 {
				return alertFireDecision(rule, decision.State, current, evaluation, now, silenced)
			}
			decision.State.Status = AlertStatusPending
			decision.State.Message = "condition met, waiting for duration"
			event := eventForDecision(rule, state, current, evaluation, now)
			event.Status = AlertStatusPending
			event.StartedAt = now
			event.Value = float64Ptr(evaluation.Value)
			event.Message = "condition met, waiting for duration"
			event.UpdatedAt = now
			decision.State.EventID = event.ID
			decision.Event = event
			return decision
		}
	}

	// 条件不满足。
	decision.State.ConditionSince = time.Time{}
	switch state.Status {
	case AlertStatusFiring, AlertStatusSilenced:
		if state.RecoverySince.IsZero() {
			state.RecoverySince = now
			decision.State.RecoverySince = now
		}
		recovery := time.Duration(rule.RecoverySec) * time.Second
		if recovery > 0 && now.Sub(state.RecoverySince) < recovery {
			decision.State.Status = state.Status
			return decision
		}
		event := eventForDecision(rule, state, current, evaluation, now)
		wasSilenced := state.Status == AlertStatusSilenced || event.Status == AlertStatusSilenced
		event.Status = AlertStatusResolved
		event.ResolvedAt = now
		event.Value = float64Ptr(evaluation.Value)
		event.Message = "resolved"
		event.UpdatedAt = now
		decision.Notify = !wasSilenced && event.Notified && len(rule.Channels) > 0
		decision.State.Status = AlertStatusResolved
		decision.State.RecoverySince = time.Time{}
		decision.State.EventID = ""
		decision.State.Message = "resolved"
		decision.Event = event
		return decision

	case AlertStatusPending:
		if current != nil {
			event := *current
			event.Status = AlertStatusResolved
			event.ResolvedAt = now
			event.Value = float64Ptr(evaluation.Value)
			event.Message = "condition cleared before duration"
			event.UpdatedAt = now
			decision.Event = &event
		}
		decision.State.Status = AlertStatusOK
		decision.State.RecoverySince = time.Time{}
		decision.State.EventID = ""
		decision.State.Message = ""
		return decision

	default:
		decision.State.Status = AlertStatusOK
		decision.State.RecoverySince = time.Time{}
		decision.State.EventID = ""
		decision.State.Message = ""
		return decision
	}
}

func alertFireDecision(rule AlertRule, state AlertState, current *AlertEvent, evaluation AlertEvaluation, now time.Time, silenced bool) AlertDecision {
	decision := AlertDecision{State: state}
	event := eventForDecision(rule, state, current, evaluation, now)
	event.StartedAt = state.ConditionSince
	if event.StartedAt.IsZero() {
		event.StartedAt = now
	}
	event.FiredAt = now
	event.Value = float64Ptr(evaluation.Value)
	event.UpdatedAt = now
	decision.State.Status = AlertStatusFiring
	decision.State.Message = ""
	event.Message = "firing"
	if silenced {
		event.Status = AlertStatusSilenced
		event.Message = "silenced"
		event.SilencedUntil = rule.SilenceUntil.UTC()
		decision.State.Status = AlertStatusSilenced
		decision.State.Message = "silenced until " + formatAlertTime(*rule.SilenceUntil)
	} else {
		event.Status = AlertStatusFiring
		event.SilencedUntil = time.Time{}
	}
	decision.Notify = !silenced && len(rule.Channels) > 0 && cooldownElapsed(rule, state, now)
	if decision.Notify {
		decision.State.LastNotifiedAt = now
	}
	event.Notified = decision.Notify
	decision.State.EventID = event.ID
	decision.Event = event
	return decision
}

func cooldownElapsed(rule AlertRule, state AlertState, now time.Time) bool {
	if rule.CooldownSec <= 0 || state.LastNotifiedAt.IsZero() {
		return true
	}
	return now.Sub(state.LastNotifiedAt) >= time.Duration(rule.CooldownSec)*time.Second
}

// eventForDecision 复制现有事件或构造新事件。新事件 ID 为空，由仓储层生成。
func eventForDecision(rule AlertRule, state AlertState, current *AlertEvent, evaluation AlertEvaluation, now time.Time) *AlertEvent {
	var event AlertEvent
	if current != nil {
		event = *current
	} else {
		event = AlertEvent{
			RuleID:       rule.ID,
			RuleName:     rule.Name,
			NodeID:       evaluation.NodeID,
			NodeHostname: evaluation.NodeHostname,
			Metric:       rule.Metric,
			Series:       evaluation.Series,
			Operator:     rule.Operator,
			Severity:     rule.Severity,
			Threshold:    rule.Threshold,
			StartedAt:    state.ConditionSince,
			CreatedAt:    now,
		}
		if event.StartedAt.IsZero() {
			event.StartedAt = now
		}
	}
	event.RuleName = rule.Name
	event.NodeHostname = evaluation.NodeHostname
	event.Operator = rule.Operator
	event.Severity = rule.Severity
	event.Threshold = rule.Threshold
	event.Metric = rule.Metric
	event.Series = evaluation.Series
	event.UpdatedAt = now
	return &event
}

// AlertEventMessage 生成稳定的通知文本。不包含任何渠道配置或密钥。
func AlertEventMessage(event AlertEvent) string {
	status := event.Status
	switch status {
	case AlertStatusSilenced:
		status = "silenced"
	case AlertStatusPending:
		status = "pending"
	}
	node := event.NodeID
	if event.NodeHostname != "" {
		node = event.NodeHostname + " (" + event.NodeID + ")"
	}
	value := "n/a"
	if event.Value != nil {
		value = fmt.Sprintf("%g", *event.Value)
	}
	message := fmt.Sprintf("[%s] rule %q %s on %s: %s = %s (threshold %s %g)",
		strings.ToUpper(event.Severity), event.RuleName, status, node, event.Metric, value, event.Operator, event.Threshold)
	if event.Series != "" && event.Series != event.Metric {
		message += " series=" + event.Series
	}
	if !event.ResolvedAt.IsZero() {
		message += ", resolved_at=" + formatAlertTime(event.ResolvedAt)
	} else if !event.StartedAt.IsZero() {
		message += ", started_at=" + formatAlertTime(event.StartedAt)
	}
	return message
}

func float64Ptr(value float64) *float64 { return &value }
