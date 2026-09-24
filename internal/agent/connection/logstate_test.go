package connection

import (
	"testing"
	"time"
)

// base 固定基准时间：整分钟起点，便于构造时钟分钟边界。
func baseTime() time.Time {
	return time.Date(2026, 1, 1, 10, 0, 0, 0, time.UTC)
}

// TestRecordFailureFirstErrorUnconditional 首失败无条件立即放行，不吞首错。
func TestRecordFailureFirstErrorUnconditional(t *testing.T) {
	s := NewConnectionLogState()
	now := baseTime()
	snap, ok := s.RecordFailure("dial tcp refused", now)
	if !ok {
		t.Fatal("first failure must be logged unconditionally")
	}
	if snap.Error != "dial tcp refused" || snap.FailuresThisMinute != 1 || snap.FailuresTotal != 1 || snap.FailDuration != 0 {
		t.Fatalf("unexpected first snapshot: %+v", snap)
	}
	if !s.underFailure() {
		t.Fatal("state machine should enter failure state on first failure")
	}
}

// TestRecordFailureMinuteThrottle 60s 内第二次不放行、第 61s 放行、分钟计数正确。
func TestRecordFailureMinuteThrottle(t *testing.T) {
	s := NewConnectionLogState()
	now := baseTime()
	if _, ok := s.RecordFailure("err", now); !ok {
		t.Fatal("first failure must log")
	}
	snap, ok := s.RecordFailure("err", now.Add(30*time.Second))
	if ok {
		t.Fatal("second failure within 60s must be throttled")
	}
	if snap.FailuresThisMinute != 2 || snap.FailuresTotal != 2 {
		t.Fatalf("minute counters wrong: %+v", snap)
	}
	snap, ok = s.RecordFailure("err", now.Add(61*time.Second))
	if !ok {
		t.Fatal("failure at +61s must be logged")
	}
	if snap.FailuresThisMinute != 1 || snap.FailuresTotal != 3 {
		t.Fatalf("counters after window roll wrong: %+v", snap)
	}
}

// TestRecordFailureLongFailingSwitchesToHourly 超过 60 分钟后转为每小时最多 1 行。
func TestRecordFailureLongFailingSwitchesToHourly(t *testing.T) {
	s := NewConnectionLogState()
	now := baseTime()
	if _, ok := s.RecordFailure("err", now); !ok {
		t.Fatal("first failure must log")
	}
	if _, ok := s.RecordFailure("err", now.Add(time.Minute)); !ok {
		t.Fatal("minute boundary failure must log")
	}
	if _, ok := s.RecordFailure("err", now.Add(30*time.Minute)); !ok {
		t.Fatal("failure at +30m must log (still minute interval)")
	}
	// 超窗：failDuration=61m > 60m，interval=1h；距上次放行 31m < 1h → 不放行
	if _, ok := s.RecordFailure("err", now.Add(61*time.Minute)); ok {
		t.Fatal("failure within one hour after window switch must be throttled")
	}
	// 距上次放行 61m >= 1h → 放行
	if _, ok := s.RecordFailure("err", now.Add(91*time.Minute)); !ok {
		t.Fatal("failure one hour after last log must be logged")
	}
}

// TestRecordFailureErrorChangeImmediate 跨出上一行所在时钟分钟后错误文本变化立即放行。
func TestRecordFailureErrorChangeImmediate(t *testing.T) {
	s := NewConnectionLogState()
	// base 相位 10:00:30：+40s = 10:01:10 跨分钟且滚动间隔 40s < 60s（due=false）
	now := baseTime().Add(30 * time.Second)
	if _, ok := s.RecordFailure("err A", now); !ok {
		t.Fatal("first failure must log")
	}
	if _, ok := s.RecordFailure("err A", now.Add(20*time.Second)); ok {
		t.Fatal("same error within minute must be throttled")
	}
	snap, ok := s.RecordFailure("err B", now.Add(40*time.Second))
	if !ok {
		t.Fatal("error change crossing minute boundary must log immediately")
	}
	if snap.Error != "err B" {
		t.Fatalf("logged error must be the latest text, got %q", snap.Error)
	}
	// 放行后重新进入分钟节流
	if _, ok := s.RecordFailure("err B", now.Add(50*time.Second)); ok {
		t.Fatal("failure right after change log must be throttled")
	}
}

// TestRecordFailureChangeWithinMinuteSuppressed 双行叠加裁决：同一时钟分钟内的
// 错误文本变化（hello rejected ↔ connection lost 交替）被分钟硬顶压制，
// 每时钟分钟合计最多 1 行。
func TestRecordFailureChangeWithinMinuteSuppressed(t *testing.T) {
	s := NewConnectionLogState()
	now := baseTime()
	// 同一分钟内 R/L 交替 20 次（模拟 20 个重连周期的双行叠加）
	if _, ok := s.RecordFailure("hello rejected: invalid credential", now); !ok {
		t.Fatal("first failure must log")
	}
	for i := 1; i < 20; i++ {
		text := "read: connection reset"
		if i%2 == 1 {
			text = "hello rejected: invalid credential"
		}
		at := now.Add(time.Duration(i) * 100 * time.Millisecond)
		if _, ok := s.RecordFailure(text, at); ok {
			t.Fatalf("in-minute alternating error #%d must be suppressed, want 1 line per minute", i)
		}
	}
	// 下一时钟分钟：放行 1 行，随后同分钟交替继续被压制
	if _, ok := s.RecordFailure("hello rejected: invalid credential", now.Add(61*time.Second)); !ok {
		t.Fatal("first failure of next minute must log")
	}
	if _, ok := s.RecordFailure("read: connection reset", now.Add(62*time.Second)); ok {
		t.Fatal("second line within same clock minute must be suppressed")
	}
}

// TestRecordSuccessResetsWindowAndSummarizes 恢复成功返回失败摘要并重置窗口/计数；
// 成功后再入失败时 failStart 重建、计数从 1 重新累计。
func TestRecordSuccessResetsWindowAndSummarizes(t *testing.T) {
	s := NewConnectionLogState()
	now := baseTime()
	// 首次成功：全零摘要
	if sum := s.RecordSuccess(now); sum.Recovered || sum.Failures != 0 || sum.Duration != 0 {
		t.Fatalf("first connect summary must be zero: %+v", sum)
	}
	// 再入失败：3 次（首错放行，随后同文本 60s 内被节流但计数照常累计）
	for i := 0; i < 3; i++ {
		_, ok := s.RecordFailure("err", now.Add(time.Duration(i+1)*time.Second))
		if i == 0 && !ok {
			t.Fatal("failure #1 must log (first after success)")
		}
		if i > 0 && ok {
			t.Fatalf("failure #%d within minute must be throttled", i+1)
		}
	}
	// 恢复成功：摘要含时长与次数，窗口与计数重置
	sum := s.RecordSuccess(now.Add(10 * time.Second))
	if !sum.Recovered || sum.Failures != 3 || sum.Duration != 9*time.Second {
		t.Fatalf("recovery summary wrong: %+v", sum)
	}
	if s.underFailure() {
		t.Fatal("success must clear failure state")
	}
	// 成功→失败再入：failStart 重建、计数从 1 起
	snap, ok := s.RecordFailure("new err", now.Add(20*time.Second))
	if !ok {
		t.Fatal("first failure after recovery must log")
	}
	if snap.FailuresTotal != 1 || snap.FailDuration != 0 {
		t.Fatalf("failure window must be rebuilt, got %+v", snap)
	}
}

// TestStatusSnapshotCadence 状态快照：未 accepted 恒不返回；accepted 到每小时节奏
// 返回 uptime 与计数；失败态进入（断开）后不再返回。
func TestStatusSnapshotCadence(t *testing.T) {
	s := NewConnectionLogState()
	now := baseTime()
	// 未 accepted
	if _, ok := s.TakeStatus(now); ok {
		t.Fatal("status must not be produced before accepted")
	}
	s.IncHeartbeatSent()
	s.IncInventoryReports()
	s.IncRevisionChecks()
	s.RecordSuccess(now)
	// accepted 后未到一小时
	if _, ok := s.TakeStatus(now.Add(59 * time.Minute)); ok {
		t.Fatal("status must wait for the hourly cadence")
	}
	snap, ok := s.TakeStatus(now.Add(time.Hour))
	if !ok {
		t.Fatal("hourly status must be produced while accepted")
	}
	if snap.Uptime != time.Hour || snap.Heartbeats != 1 || snap.InventoryReports != 1 || snap.RevisionChecks != 1 {
		t.Fatalf("unexpected status snapshot: %+v", snap)
	}
	// 刚打过，立即再取不得重复
	if _, ok := s.TakeStatus(now.Add(time.Hour + time.Second)); ok {
		t.Fatal("status must not repeat within the interval")
	}
	// 失败态进入（断开）→ connectedAt 清零 → 恒不返回
	s.RecordFailure("connection lost", now.Add(2*time.Hour))
	if _, ok := s.TakeStatus(now.Add(3 * time.Hour)); ok {
		t.Fatal("status must be skipped once failure state is entered")
	}
}
