package scheduler

import (
	"context"
	"log/slog"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/robfig/cron/v3"
)

// Executor 调度触发执行器
type Executor interface {
	TriggerSchedule(ctx context.Context, sch *models.Schedule, scheduledAt time.Time) error
}

// Scheduler Agent 本地调度器
type Scheduler struct {
	executor   Executor
	logger     *slog.Logger
	schedules  map[string]*models.Schedule
	cronParser cron.Parser
	lastFire   map[string]time.Time // 各 Schedule 上次触发时间
	lastRun    map[string]time.Time // slot 幂等 key（保留）
	paused     bool
	online     func() bool
	mu         chan struct{} // 简单互斥
}

// New 创建调度器
func New(exec Executor, logger *slog.Logger) *Scheduler {
	parser := cron.NewParser(
		cron.SecondOptional | cron.Minute | cron.Hour | cron.Dom | cron.Month | cron.Dow | cron.Descriptor,
	)
	return &Scheduler{
		executor:   exec,
		logger:     logger,
		schedules:  map[string]*models.Schedule{},
		cronParser: parser,
		lastFire:   map[string]time.Time{},
		lastRun:    map[string]time.Time{},
		online:     func() bool { return true },
		mu:         make(chan struct{}, 1),
	}
}

// SetOnlineFunc 注入 Hub 连接状态，用于执行 owner=agent 且要求 Hub 在线的调度。
func (s *Scheduler) SetOnlineFunc(fn func() bool) {
	s.mu <- struct{}{}
	if fn == nil {
		s.online = func() bool { return true }
	} else {
		s.online = fn
	}
	<-s.mu
}

// SetPaused 在节点维护/禁用期间暂停 Agent-owned 调度。
func (s *Scheduler) SetPaused(paused bool) {
	s.mu <- struct{}{}
	s.paused = paused
	<-s.mu
}

// IsPaused 返回当前暂停态（权威来源：Hub 下发 NODE_STATUS 经 OnNodeStatus → SetPaused）。
// 供执行/部署指令的暂停态拒收门禁使用。
func (s *Scheduler) IsPaused() bool {
	s.mu <- struct{}{}
	defer func() { <-s.mu }()
	return s.paused
}

// UpdateSchedules 替换本地调度表
func (s *Scheduler) UpdateSchedules(schedules []*models.Schedule) {
	s.mu <- struct{}{}
	defer func() { <-s.mu }()
	s.schedules = map[string]*models.Schedule{}
	for _, sch := range schedules {
		if sch.Enabled {
			s.schedules[sch.ID] = sch
		}
	}
	if s.logger != nil {
		s.logger.Debug("scheduler updated", "count", len(s.schedules))
		for id := range s.schedules {
			s.logger.Debug("  sched", "id", id)
		}
	}
}

// GetSchedules 获取当前调度表
func (s *Scheduler) GetSchedules() []*models.Schedule {
	s.mu <- struct{}{}
	defer func() { <-s.mu }()
	out := make([]*models.Schedule, 0, len(s.schedules))
	for _, v := range s.schedules {
		out = append(out, v)
	}
	return out
}

// RemoveSchedule 移除调度（Tombstone）
func (s *Scheduler) RemoveSchedule(id string) {
	s.mu <- struct{}{}
	defer func() { <-s.mu }()
	delete(s.schedules, id)
	delete(s.lastRun, id)
	delete(s.lastFire, id)
}

// Start 启动调度循环
func (s *Scheduler) Start(ctx context.Context, tick time.Duration) {
	go func() {
		t := time.NewTicker(tick)
		defer t.Stop()
		s.checkDue(ctx, time.Now())
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-t.C:
				s.checkDue(ctx, now)
			}
		}
	}()
}

func (s *Scheduler) checkDue(ctx context.Context, now time.Time) {
	schs := s.GetSchedules()
	if len(schs) == 0 {
		return
	}
	for _, sch := range schs {
		if sch.ExecutionOwner != models.ExecutionOwnerAgent {
			continue
		}
		s.mu <- struct{}{}
		paused := s.paused
		online := s.online()
		<-s.mu
		if paused || (sch.OfflinePolicy == models.OfflinePolicyHubOnlineRequired && !online) {
			continue
		}
		scheduledAt, due, err := s.nextRun(sch, now)
		if err != nil {
			continue
		}
		if !due {
			// nextRun 已处理 Interval+SKIP 的 lastFire 推进；无需额外操作
			continue
		}
		// 幂等：同一 Slot 只触发一次
		key := sch.ID + "@" + scheduledAt.UTC().Format(time.RFC3339Nano)
		s.mu <- struct{}{}
		if last, ok := s.lastRun[key]; ok && !last.IsZero() {
			<-s.mu
			continue
		}
		s.lastRun[key] = now
		s.lastFire[sch.ID] = now
		<-s.mu

		if s.logger != nil {
			s.logger.Info("schedule trigger", "schedule", sch.ID, "task", sch.TaskID, "at", scheduledAt)
		}
		if err := s.executor.TriggerSchedule(ctx, sch, scheduledAt); err != nil && s.logger != nil {
			s.logger.Warn("schedule trigger failed", "schedule", sch.ID, "error", err)
		}
	}
}

// intervalAnchor 返回 interval 调度的当前 anchor（上次触发或创建时刻）
func intervalAnchor(s *Scheduler, sch *models.Schedule, now time.Time) time.Time {
	s.mu <- struct{}{}
	anchor := s.lastFire[sch.ID]
	<-s.mu
	if anchor.IsZero() {
		anchor = sch.CreatedAt
	}
	if anchor.IsZero() {
		anchor = now
	}
	return anchor
}

// nextRun 判断当前时刻是否触发某 Schedule
func (s *Scheduler) nextRun(sch *models.Schedule, now time.Time) (time.Time, bool, error) {
	loc, err := time.LoadLocation(sch.Timezone)
	if err != nil {
		loc = time.UTC
	}
	now = now.In(loc)
	switch sch.Type {
	case models.ScheduleTypeCron:
		spec, err := s.cronParser.Parse(sch.Expression)
		if err != nil {
			return time.Time{}, false, err
		}
		// 基于上次触发时间判断本次是否到点
		s.mu <- struct{}{}
		last := s.lastFire[sch.ID]
		<-s.mu
		if last.IsZero() {
			last = sch.CreatedAt.In(loc)
			if last.IsZero() {
				last = now.Add(-time.Second)
			}
		}
		next := spec.Next(last)
		if !next.After(now) {
			// 已到/越过触发点
			return next, true, nil
		}
		return next, false, nil
	case models.ScheduleTypeInterval:
		// anchor 以上次触发为准（重启后回退到创建时刻），每次触发后推进，保证周期性
		anchor := intervalAnchor(s, sch, now)
		period := time.Duration(sch.IntervalSec) * time.Second
		if period <= 0 {
			return time.Time{}, false, nil
		}
		elapsed := now.Sub(anchor)
		if elapsed < period {
			// 未到下一周期
			return anchor.Add(period), false, nil
		}
		// 已到/越过一个或多个周期
		count := elapsed / period
		lastDue := anchor.Add(count * period)
		// 精确到点（或首个错过的 slot）
		if elapsed < period*2 {
			// 当前周期刚到达 → 触发
			return lastDue, true, nil
		}
		// 错过多个周期：SKIP 跳过（推进 lastFire 到当前周期起点），RUN_ONCE 补跑最近一次
		if sch.MisfirePolicy == models.MisfirePolicySkip {
			// 推进 lastFire，使下一轮按当前周期边界正常触发（避免死循环）
			s.mu <- struct{}{}
			s.lastFire[sch.ID] = now.Add(-(elapsed % period))
			<-s.mu
			return lastDue, false, nil
		}
		return lastDue, true, nil
	case models.ScheduleTypeOneTime:
		if sch.RunAt.IsZero() {
			return time.Time{}, false, nil
		}
		t := sch.RunAt.In(loc)
		s.mu <- struct{}{}
		alreadyFired := !s.lastFire[sch.ID].IsZero()
		<-s.mu
		if alreadyFired {
			return t, false, nil
		}
		if now.After(t) {
			if sch.MisfirePolicy == models.MisfirePolicyRunOnce {
				return t, true, nil
			}
			s.mu <- struct{}{}
			s.lastFire[sch.ID] = now
			<-s.mu
			return time.Time{}, false, nil
		}
		return t, false, nil
	case models.ScheduleTypeOnStart:
		// on_start 不走本地周期 tick：触发由 Agent 进程启动一次性路径负责，
		// 这里显式返回未到期，避免被当作非法类型。
		return time.Time{}, false, nil
	}
	return time.Time{}, false, nil
}

var _ = protocol.MsgRunExecution
