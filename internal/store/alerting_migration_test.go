package store

import "testing"

func TestAlertingMigrationCreatesTablesAndDedupIndex(t *testing.T) {
	s := newTestStore(t)
	var version int
	if err := s.DB().QueryRow(`SELECT MAX(version) FROM schema_migrations`).Scan(&version); err != nil {
		t.Fatalf("migration version: %v", err)
	}
	if version < 24 {
		t.Fatalf("migration version = %d, want >= 24", version)
	}
	for _, table := range []string{
		"alert_rules", "alert_states", "alert_events", "notification_channels",
		"alert_notifications", "alert_notification_attempts",
	} {
		var count int
		if err := s.DB().QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = ?`, table).Scan(&count); err != nil || count != 1 {
			t.Fatalf("table %s count = %d, err = %v", table, count, err)
		}
	}

	insert := func(id, status string) error {
		_, err := s.DB().Exec(`INSERT INTO alert_events
			(id, rule_id, rule_name, node_id, metric, series, operator, status, severity,
			 threshold, started_at, fired_at, message, created_at, updated_at)
			VALUES (?, 'rule-1', 'rule', 'node-1', 'cpu.usage', 'cpu.usage', 'gt', ?, 'warning',
			 90, '2026-01-01T00:00:00.000000000Z', '2026-01-01T00:00:00.000000000Z', '', '2026-01-01T00:00:00.000000000Z', '2026-01-01T00:00:00.000000000Z')`,
			id, status)
		return err
	}
	if err := insert("event-1", "firing"); err != nil {
		t.Fatalf("insert first active event: %v", err)
	}
	if err := insert("event-2", "pending"); err == nil {
		t.Fatal("duplicate active event must violate the dedup index")
	}
	if err := insert("event-3", "resolved"); err != nil {
		t.Fatalf("resolved history event should be allowed: %v", err)
	}
}
