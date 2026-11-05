package monitoring

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"log/slog"
	"math"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// AlertEvaluator 周期扫描启用的原生指标阈值规则。
//
// 设计约束：
//   - fail-closed：无样本、样本过期、节点离线或查询失败时不触发、不恢复；
//   - 评估只写状态/事件/发件箱，通知由独立循环发送，不阻塞评估；
//   - 所有状态按 rule+node+metric+series 去重。
type AlertEvaluator struct {
	repo             *AlertRepository
	st               store.Store
	notifier         *AlertNotifier
	logger           *slog.Logger
	now              func() time.Time
	interval         time.Duration
	dispatchInterval time.Duration
}

func NewAlertEvaluator(repo *AlertRepository, st store.Store, notifier *AlertNotifier, logger *slog.Logger) *AlertEvaluator {
	return &AlertEvaluator{
		repo:             repo,
		st:               st,
		notifier:         notifier,
		logger:           logger,
		now:              func() time.Time { return time.Now().UTC() },
		interval:         DefaultAlertEvaluationInterval,
		dispatchInterval: DefaultAlertDispatchInterval,
	}
}

// SetInterval 注入评估周期（测试用）。
func (e *AlertEvaluator) SetInterval(interval time.Duration) {
	if interval > 0 {
		e.interval = interval
	}
}

// SetDispatchInterval 注入通知发件箱扫描周期（测试用）。
func (e *AlertEvaluator) SetDispatchInterval(interval time.Duration) {
	if interval > 0 {
		e.dispatchInterval = interval
	}
}

// SetClock 注入时钟（测试用）。
func (e *AlertEvaluator) SetClock(now func() time.Time) {
	if now != nil {
		e.now = now
	}
}

// Run 启动评估与通知循环，ctx 取消后返回。
//
// 评估与通知使用独立 goroutine：单次通知的网络超时或重试不会阻塞下一轮评估。
func (e *AlertEvaluator) Run(ctx context.Context) {
	if e.interval <= 0 {
		e.interval = DefaultAlertEvaluationInterval
	}
	if e.dispatchInterval <= 0 {
		e.dispatchInterval = DefaultAlertDispatchInterval
	}
	e.runEvaluation(ctx)

	var dispatchWG sync.WaitGroup
	dispatchWG.Add(1)
	go func() {
		defer dispatchWG.Done()
		e.runDispatchLoop(ctx)
	}()

	evaluationTicker := time.NewTicker(e.interval)
	defer evaluationTicker.Stop()
	for {
		select {
		case <-ctx.Done():
			dispatchWG.Wait()
			return
		case <-evaluationTicker.C:
			e.runEvaluation(ctx)
		}
	}
}

func (e *AlertEvaluator) runDispatchLoop(ctx context.Context) {
	e.runDispatch(ctx)
	ticker := time.NewTicker(e.dispatchInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			e.runDispatch(ctx)
		}
	}
}

func (e *AlertEvaluator) runEvaluation(ctx context.Context) {
	if err := e.EvaluateOnce(ctx); err != nil && ctx.Err() == nil && e.logger != nil {
		e.logger.Error("alert evaluation failed", "error", err)
	}
}

func (e *AlertEvaluator) runDispatch(ctx context.Context) {
	if e.notifier == nil {
		return
	}
	if err := e.notifier.ProcessPending(ctx); err != nil && ctx.Err() == nil && e.logger != nil {
		e.logger.Error("alert notification dispatch failed", "error", normalizeNotificationError(err))
	}
}

// EvaluateOnce 执行一轮完整评估。单条规则失败不会阻止其他规则。
func (e *AlertEvaluator) EvaluateOnce(ctx context.Context) error {
	rules, err := e.repo.EnabledAlertRules(ctx)
	if err != nil {
		return err
	}
	now := e.now()
	var failures []error
	for _, rule := range rules {
		if err := ctx.Err(); err != nil {
			return nil
		}
		if err := e.evaluateRule(ctx, rule, now); err != nil {
			failures = append(failures, fmt.Errorf("rule %s: %w", rule.ID, err))
			if e.logger != nil {
				e.logger.Error("alert rule evaluation failed", "rule", rule.ID, "error", err)
			}
		}
	}
	return errors.Join(failures...)
}

// DispatchOnce 立即处理一轮通知发件箱（测试用）。
func (e *AlertEvaluator) DispatchOnce(ctx context.Context) error {
	if e.notifier == nil {
		return nil
	}
	return e.notifier.ProcessPending(ctx)
}

func (e *AlertEvaluator) evaluateRule(ctx context.Context, rule AlertRule, now time.Time) error {
	metricStore, ok := e.st.(store.NativeMetricStore)
	if !ok {
		return errors.New("native metric store is unavailable")
	}
	states, err := e.repo.ListAlertStates(ctx, rule.ID)
	if err != nil {
		return err
	}
	stateIndex := make(map[alertStateKey]AlertState, len(states))
	for _, state := range states {
		stateIndex[alertStateKey{ruleID: state.RuleID, nodeID: state.NodeID, metric: state.Metric, series: state.Series}] = state
	}
	touched := map[alertStateKey]bool{}

	var nodes []*models.Node
	if rule.NodeID != "" {
		node, err := e.st.GetNode(ctx, rule.NodeID)
		if errors.Is(err, sql.ErrNoRows) {
			return e.markUnavailable(ctx, rule, rule.NodeID, "", rule.Series, "node_not_found", stateIndex, touched, now)
		}
		if err != nil {
			return err
		}
		nodes = []*models.Node{node}
	} else {
		nodes, err = e.st.ListNodes(ctx)
		if err != nil {
			return err
		}
	}
	if len(nodes) == 0 {
		return e.markUnavailable(ctx, rule, "", "", rule.Series, "no_nodes", stateIndex, touched, now)
	}

	var failures []error
	for _, node := range nodes {
		if ctx.Err() != nil {
			return nil
		}
		if nativeNodeUnavailable(node.Status) {
			if err := e.markUnavailable(ctx, rule, node.ID, node.Hostname, rule.Series, "offline", stateIndex, touched, now); err != nil {
				failures = append(failures, err)
			}
			continue
		}
		samples, err := metricStore.LatestNodeMetricSamples(ctx, node.ID)
		if err != nil {
			// 查询失败按 fail-closed 处理：只记录状态，不触发/恢复。
			if err := e.markUnavailable(ctx, rule, node.ID, node.Hostname, rule.Series, "query_failed", stateIndex, touched, now); err != nil {
				failures = append(failures, err)
			}
			failures = append(failures, fmt.Errorf("node %s latest samples: %w", node.ID, err))
			continue
		}
		interval, err := metricStore.NodeMetricSampleInterval(ctx, node.ID)
		if err != nil {
			interval = store.NativeRawStep
		}
		matched := filterAlertSamples(rule, samples)
		if len(matched) == 0 {
			if err := e.markUnavailable(ctx, rule, node.ID, node.Hostname, rule.Series, "no_data", stateIndex, touched, now); err != nil {
				failures = append(failures, err)
			}
			continue
		}
		for _, sample := range matched {
			series := store.BuildSeriesKey(sample.Metric, sample.Dimensions)
			evaluation := AlertEvaluation{
				NodeID:       node.ID,
				NodeHostname: node.Hostname,
				Series:       series,
			}
			switch {
			case sample.Quality != store.NativeQualityValid || sample.Value == nil ||
				math.IsNaN(*sample.Value) || math.IsInf(*sample.Value, 0):
				evaluation.Reason = "invalid_sample"
			case alertSampleStale(sample.Timestamp, now, interval):
				evaluation.Reason = "stale"
			default:
				evaluation.Available = true
				evaluation.Value = *sample.Value
				evaluation.ObservedAt = sample.Timestamp.UTC()
			}
			if err := e.applyEvaluation(ctx, rule, evaluation, stateIndex, touched, now); err != nil {
				failures = append(failures, err)
			}
		}
	}

	// 上一轮存在、本轮未覆盖的状态：节点离线/无样本/规则范围变化。
	for key, state := range stateIndex {
		if touched[key] {
			continue
		}
		reason := "no_data"
		if rule.NodeID != "" && state.NodeID != rule.NodeID {
			reason = "node_not_found"
		} else if node := findNode(nodes, state.NodeID); node == nil {
			reason = "node_not_found"
		} else if nativeNodeUnavailable(node.Status) {
			reason = "offline"
		}
		if err := e.applyEvaluation(ctx, rule, AlertEvaluation{
			NodeID: state.NodeID, NodeHostname: nodeHostname(nodes, state.NodeID),
			Series: state.Series, Reason: reason,
		}, stateIndex, touched, now); err != nil {
			failures = append(failures, err)
		}
	}
	return errors.Join(failures...)
}

func (e *AlertEvaluator) markUnavailable(
	ctx context.Context,
	rule AlertRule,
	nodeID, hostname, series, reason string,
	states map[alertStateKey]AlertState,
	touched map[alertStateKey]bool,
	now time.Time,
) error {
	found := false
	for _, state := range states {
		if state.NodeID != nodeID {
			continue
		}
		found = true
		if err := e.applyEvaluation(ctx, rule, AlertEvaluation{
			NodeID: nodeID, NodeHostname: hostname, Series: state.Series, Reason: reason,
		}, states, touched, now); err != nil {
			return err
		}
	}
	if found {
		return nil
	}
	return e.applyEvaluation(ctx, rule, AlertEvaluation{
		NodeID: nodeID, NodeHostname: hostname, Series: series, Reason: reason,
	}, states, touched, now)
}

func (e *AlertEvaluator) applyEvaluation(
	ctx context.Context,
	rule AlertRule,
	evaluation AlertEvaluation,
	states map[alertStateKey]AlertState,
	touched map[alertStateKey]bool,
	now time.Time,
) error {
	key := alertStateKey{ruleID: rule.ID, nodeID: evaluation.NodeID, metric: rule.Metric, series: evaluation.Series}
	touched[key] = true
	state := states[key]
	if state.RuleID == "" {
		state = AlertState{
			RuleID: rule.ID, NodeID: evaluation.NodeID, Metric: rule.Metric,
			Series: evaluation.Series, Status: AlertStatusOK,
		}
	}
	var current *AlertEvent
	if state.EventID != "" {
		event, err := e.repo.GetAlertEvent(ctx, state.EventID)
		if err == nil {
			current = &event
		} else if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
	}
	decision := EvaluateAlertDecision(rule, state, current, evaluation, now)
	if err := e.repo.ApplyDecision(ctx, rule, decision); err != nil {
		return err
	}
	states[key] = decision.State
	if e.logger != nil && decision.Event != nil && (current == nil || current.Status != decision.Event.Status) {
		e.logger.Info("alert event updated",
			"rule", rule.ID,
			"node", evaluation.NodeID,
			"series", evaluation.Series,
			"status", decision.Event.Status,
			"notify", decision.Notify)
	}
	return nil
}

func filterAlertSamples(rule AlertRule, samples []store.NativeSample) []store.NativeSample {
	matched := make([]store.NativeSample, 0, 4)
	for _, sample := range samples {
		if sample.Metric != rule.Metric {
			continue
		}
		series := store.BuildSeriesKey(sample.Metric, sample.Dimensions)
		if rule.Series != "" && series != rule.Series {
			continue
		}
		matched = append(matched, sample)
	}
	return matched
}

func alertSampleStale(timestamp, now time.Time, interval time.Duration) bool {
	if timestamp.IsZero() || timestamp.After(now) {
		return true
	}
	if interval <= 0 {
		interval = store.NativeRawStep
	}
	return now.Sub(timestamp) > 3*interval
}

func nativeNodeUnavailable(status string) bool {
	return status != "" && status != models.NodeStatusOnline
}

func findNode(nodes []*models.Node, id string) *models.Node {
	for _, node := range nodes {
		if node.ID == id {
			return node
		}
	}
	return nil
}

func nodeHostname(nodes []*models.Node, id string) string {
	if node := findNode(nodes, id); node != nil {
		return node.Hostname
	}
	return ""
}

type alertStateKey struct {
	ruleID string
	nodeID string
	metric string
	series string
}
