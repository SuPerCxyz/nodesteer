package monitoring

import (
	"context"
	"strings"
	"testing"
	"time"
)

func TestListNotificationsFiltersPaginationAndAttempts(t *testing.T) {
	st, repo := newAlertTestRepository(t)
	ctx := context.Background()
	channel := createAlertTestChannel(t, repo)
	rule := createAlertTestRule(t, repo, channel.ID)
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	event := testNotificationEvent()
	event.ID = ""
	event.RuleID = rule.ID
	event.RuleName = rule.Name
	if err := repo.ApplyDecision(ctx, rule, AlertDecision{
		State: AlertState{
			RuleID: rule.ID, NodeID: event.NodeID, Metric: rule.Metric, Series: rule.Series,
			Status: AlertStatusFiring, LastEvaluatedAt: now, LastNotifiedAt: now, UpdatedAt: now,
		},
		Event:  &event,
		Notify: true,
	}); err != nil {
		t.Fatalf("enqueue: %v", err)
	}
	var notificationID, eventID string
	if err := st.DB().QueryRow(`SELECT id, event_id FROM alert_notifications LIMIT 1`).Scan(&notificationID, &eventID); err != nil {
		t.Fatalf("notification: %v", err)
	}
	if err := repo.RecordNotificationAttempt(ctx, notificationID, 1, "failed", "notification endpoint returned status 500", false, now.Add(time.Second), now); err != nil {
		t.Fatalf("attempt 1: %v", err)
	}
	if err := repo.RecordNotificationAttempt(ctx, notificationID, 2, "failed", "notification endpoint returned status 500", true, time.Time{}, now.Add(time.Second)); err != nil {
		t.Fatalf("attempt 2: %v", err)
	}
	// 历史或外部写入的错误文本也必须脱敏后返回。
	if _, err := st.DB().Exec(`UPDATE alert_notification_attempts SET error = ? WHERE notification_id = ? AND attempt = 1`,
		"https://secret.example/token=abc", notificationID); err != nil {
		t.Fatalf("seed unsafe attempt error: %v", err)
	}
	if _, err := st.DB().Exec(`UPDATE alert_notifications SET last_error = ? WHERE id = ?`,
		"https://secret.example/token=abc", notificationID); err != nil {
		t.Fatalf("seed unsafe notification error: %v", err)
	}

	records, total, err := repo.ListNotifications(ctx, AlertNotificationFilter{EventID: eventID})
	if err != nil {
		t.Fatalf("list: %v", err)
	}
	if total != 1 || len(records) != 1 {
		t.Fatalf("records = %d/%d", total, len(records))
	}
	record := records[0]
	if record.ID != notificationID || record.Status != "failed" || record.Attempts != 2 {
		t.Fatalf("record = %+v", record)
	}
	if record.LastError != "notification failed" {
		t.Fatalf("last_error = %q", record.LastError)
	}
	if len(record.AttemptDetails) != 2 {
		t.Fatalf("attempt details = %+v", record.AttemptDetails)
	}
	if record.AttemptDetails[0].Attempt != 1 || record.AttemptDetails[0].Error != "notification failed" ||
		record.AttemptDetails[0].AttemptedAt.IsZero() {
		t.Fatalf("attempt 1 = %+v", record.AttemptDetails[0])
	}
	if record.AttemptDetails[1].Attempt != 2 || !strings.Contains(record.AttemptDetails[1].Error, "status 500") {
		t.Fatalf("attempt 2 = %+v", record.AttemptDetails[1])
	}

	if records, total, err = repo.ListNotifications(ctx, AlertNotificationFilter{ChannelID: channel.ID, Status: "failed"}); err != nil ||
		total != 1 || len(records) != 1 {
		t.Fatalf("channel filter = %d/%d, %v", total, len(records), err)
	}
	if records, total, err = repo.ListNotifications(ctx, AlertNotificationFilter{Status: "pending"}); err != nil ||
		total != 0 || len(records) != 0 {
		t.Fatalf("pending filter = %d/%d, %v", total, len(records), err)
	}
	if records, total, err = repo.ListNotifications(ctx, AlertNotificationFilter{Limit: 1, Offset: 0}); err != nil ||
		total != 1 || len(records) != 1 {
		t.Fatalf("page 1 = %d/%d, %v", total, len(records), err)
	}
	if records, total, err = repo.ListNotifications(ctx, AlertNotificationFilter{Limit: 1, Offset: 1}); err != nil ||
		total != 1 || len(records) != 0 {
		t.Fatalf("page 2 = %d/%d, %v", total, len(records), err)
	}
}
