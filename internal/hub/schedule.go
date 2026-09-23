package hub

import (
	"context"
	"fmt"
	"strconv"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/google/uuid"
)

// ScheduleManager Hub 侧调度管理（HUB owner）
type ScheduleManager struct {
	store     store.Store
	revisions *RevisionManager
	syncMgr   *SyncManager
	nodes     *NodeManager
	execMgr   *ExecutionManager
	lastFire  map[string]time.Time
	mu        sync.Mutex
}

// NewScheduleManager 创建调度管理器
func NewScheduleManager(st store.Store, rm *RevisionManager, sm *SyncManager, nm *NodeManager, em *ExecutionManager) *ScheduleManager {
	return &ScheduleManager{
		store:     st,
		revisions: rm,
		syncMgr:   sm,
		nodes:     nm,
		execMgr:   em,
		lastFire:  map[string]time.Time{},
	}
}

// Create 创建 Schedule
func (sm *ScheduleManager) Create(ctx context.Context, s *models.Schedule) error {
	if s.ID == "" {
		s.ID = uuid.NewString()
	}
	s.Revision = 1
	s.CreatedAt = time.Now()
	s.UpdatedAt = time.Now()
	if s.MisfirePolicy == "" {
		s.MisfirePolicy = models.MisfirePolicyRunOnce
	}
	if s.OfflinePolicy == "" {
		s.OfflinePolicy = models.OfflinePolicyAllowOffline
	}
	if s.Timezone == "" {
		s.Timezone = "UTC"
	}
	if _, err := sm.store.GetTask(ctx, s.TaskID); err != nil {
		return fmt.Errorf("referenced task not found")
	}
	if err := validateSchedule(s); err != nil {
		return err
	}
	var rev int64
	if err := runMutationTx(ctx, sm.store, func(txctx context.Context) error {
		if err := sm.store.CreateSchedule(txctx, s); err != nil {
			return err
		}
		var err error
		rev, err = sm.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := sm.revisions.RecordChange(txctx, rev, models.ObjectSchedule, s.ID, s.Revision, "create"); err != nil {
			return err
		}
		return recordMutationAudit(txctx, sm.store, "schedule", s.ID, "create")
	}); err != nil {
		return err
	}
	sm.syncMgr.NotifyChange(ctx, models.ObjectSchedule, s.ID, s.Revision, rev)
	return nil
}

// Update 更新 Schedule
// 部分更新合并：请求体未提供的字段保留 existing 值，避免损坏完整定义
// （enabled 用请求值，允许显式禁用）。
func (sm *ScheduleManager) Update(ctx context.Context, s *models.Schedule) error {
	existing, err := sm.store.GetSchedule(ctx, s.ID)
	if err != nil {
		return err
	}
	if s.TaskID == "" {
		s.TaskID = existing.TaskID
	}
	if s.Type == "" {
		s.Type = existing.Type
	}
	if s.Expression == "" {
		s.Expression = existing.Expression
	}
	if s.RunAt.IsZero() {
		s.RunAt = existing.RunAt
	}
	if s.IntervalSec == 0 {
		s.IntervalSec = existing.IntervalSec
	}
	if s.Timezone == "" {
		s.Timezone = existing.Timezone
	}
	if s.ExecutionOwner == "" {
		s.ExecutionOwner = existing.ExecutionOwner
	}
	if s.OfflinePolicy == "" {
		s.OfflinePolicy = existing.OfflinePolicy
	}
	if s.MisfirePolicy == "" {
		s.MisfirePolicy = existing.MisfirePolicy
	}
	if _, err := sm.store.GetTask(ctx, s.TaskID); err != nil {
		return fmt.Errorf("referenced task not found")
	}
	if err := validateSchedule(s); err != nil {
		return err
	}
	s.Revision = existing.Revision + 1
	s.CreatedAt = existing.CreatedAt
	s.UpdatedAt = time.Now()
	var rev int64
	if err := runMutationTx(ctx, sm.store, func(txctx context.Context) error {
		if err := sm.store.UpdateSchedule(txctx, s, existing.Revision); err != nil {
			return err
		}
		var err error
		rev, err = sm.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := sm.revisions.RecordChange(txctx, rev, models.ObjectSchedule, s.ID, s.Revision, "update"); err != nil {
			return err
		}
		return recordMutationAudit(txctx, sm.store, "schedule", s.ID, "update")
	}); err != nil {
		return err
	}
	sm.syncMgr.NotifyChange(ctx, models.ObjectSchedule, s.ID, s.Revision, rev)
	return nil
}

// Delete 删除 Schedule
func (sm *ScheduleManager) Delete(ctx context.Context, id string) error {
	var rev int64
	if err := runMutationTx(ctx, sm.store, func(txctx context.Context) error {
		if err := sm.store.DeleteSchedule(txctx, id); err != nil {
			return err
		}
		var err error
		rev, err = sm.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := sm.revisions.RecordChange(txctx, rev, models.ObjectSchedule, id, 0, "delete"); err != nil {
			return err
		}
		if err := sm.store.RecordTombstone(txctx, models.ObjectSchedule, id, rev); err != nil {
			return err
		}
		detail := ""
		if sc, err := sm.store.GetSchedule(ctx, id); err == nil {
			detail = sc.Expression
			if detail == "" && sc.IntervalSec > 0 {
				detail = fmt.Sprintf("interval %ds", sc.IntervalSec)
			}
		}
		return recordMutationAuditDetail(txctx, sm.store, "schedule", id, "delete", detail)
	}); err != nil {
		return err
	}
	sm.mu.Lock()
	delete(sm.lastFire, id)
	sm.mu.Unlock()
	sm.syncMgr.NotifyChange(ctx, models.ObjectSchedule, id, 0, rev)
	return nil
}

// Get 获取
func (sm *ScheduleManager) Get(ctx context.Context, id string) (*models.Schedule, error) {
	return sm.store.GetSchedule(ctx, id)
}

// List 列表
func (sm *ScheduleManager) List(ctx context.Context) ([]*models.Schedule, error) {
	return sm.store.ListSchedules(ctx)
}

// RunDueHUB 运行到期的 HUB owner Schedule
func (sm *ScheduleManager) RunDueHUB(ctx context.Context, now time.Time) error {
	schedules, err := sm.store.ListSchedules(ctx)
	if err != nil {
		return err
	}
	for _, s := range schedules {
		if !s.Enabled || s.ExecutionOwner != models.ExecutionOwnerHub {
			continue
		}
		next, due, err := sm.computeNextRun(s, now)
		if err != nil {
			continue
		}
		if !due {
			continue
		}
		task, err := sm.store.GetTask(ctx, s.TaskID)
		if err != nil || !task.Enabled {
			continue
		}
		nodeIDs, err := sm.nodes.ResolveTarget(ctx, task.Target)
		if err != nil {
			continue
		}
		if _, err := sm.execMgr.RunScheduledHub(ctx, task, nodeIDs, next); err != nil {
			// 幂等冲突忽略
		}
		sm.mu.Lock()
		sm.lastFire[s.ID] = now
		sm.mu.Unlock()
	}
	return nil
}

// StartHubScheduler 启动 Hub 侧调度循环（间隔每 tick 从 settings 动态读取，最小 5s）
func (sm *ScheduleManager) StartHubScheduler(ctx context.Context, interval time.Duration) {
	go func() {
		// 读取运行期配置的间隔（revision_check_interval_sec），未配置或非法时用默认
		getInterval := func() time.Duration {
			if v, err := sm.store.GetSetting(ctx, "revision_check_interval_sec"); err == nil && v != "" {
				if n, err := strconv.Atoi(v); err == nil && n >= 5 {
					return time.Duration(n) * time.Second
				}
			}
			return interval
		}
		cur := getInterval()
		t := time.NewTicker(cur)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-t.C:
				_ = sm.RunDueHUB(ctx, time.Now())
				// 若配置的间隔变化，动态调整 ticker
				if next := getInterval(); next != cur {
					cur = next
					t.Reset(cur)
				}
			}
		}
	}()
}

// computeNextRun 计算 Schedule 是否到期（基于 lastFire 状态，供 Hub 调度使用）
func (sm *ScheduleManager) computeNextRun(s *models.Schedule, now time.Time) (next time.Time, due bool, err error) {
	loc, err := time.LoadLocation(s.Timezone)
	if err != nil {
		loc = time.UTC
	}
	now = now.In(loc)
	switch s.Type {
	case models.ScheduleTypeInterval:
		sm.mu.Lock()
		anchor := sm.lastFire[s.ID]
		sm.mu.Unlock()
		if anchor.IsZero() {
			anchor = s.CreatedAt.In(loc)
		}
		if anchor.IsZero() {
			anchor = now
		}
		period := time.Duration(s.IntervalSec) * time.Second
		if period <= 0 {
			return time.Time{}, false, nil
		}
		elapsed := now.Sub(anchor)
		if elapsed < 0 {
			return anchor, false, nil
		}
		count := elapsed / period
		lastDue := anchor.Add(count * period)
		next = anchor.Add((count + 1) * period)
		if count >= 1 {
			// 存在错过的触发点：SKIP 则跳过，RUN_ONCE 则补跑最近一次
			if count > 1 && s.MisfirePolicy == models.MisfirePolicySkip {
				sm.mu.Lock()
				sm.lastFire[s.ID] = now
				sm.mu.Unlock()
				return next, false, nil
			}
			return lastDue, true, nil
		}
		return next, false, nil
	case models.ScheduleTypeCron:
		sm.mu.Lock()
		lastFire := sm.lastFire[s.ID]
		sm.mu.Unlock()
		next, due, err := nextCronRun(s.Expression, loc, lastFire, s.CreatedAt, now)
		if due && s.MisfirePolicy == models.MisfirePolicySkip && next.Before(now) {
			sm.mu.Lock()
			sm.lastFire[s.ID] = now
			sm.mu.Unlock()
			return next, false, err
		}
		return next, due, err
	case models.ScheduleTypeOneTime:
		if s.RunAt.IsZero() {
			return time.Time{}, false, nil
		}
		t := s.RunAt.In(loc)
		sm.mu.Lock()
		alreadyFired := !sm.lastFire[s.ID].IsZero()
		sm.mu.Unlock()
		if alreadyFired {
			return t, false, nil
		}
		if now.After(t) {
			if s.MisfirePolicy == models.MisfirePolicyRunOnce {
				return t, true, nil
			}
			sm.mu.Lock()
			sm.lastFire[s.ID] = now
			sm.mu.Unlock()
			return time.Time{}, false, nil
		}
		return t, false, nil
	}
	return time.Time{}, false, nil
}
