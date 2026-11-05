package monitoring

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestSchedulerFairWhenWorkersOverloaded(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.DB().SetMaxOpenConns(1) // :memory: databases are per connection
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	repo := NewRepository(db.DB())
	for i := 0; i < 100; i++ {
		target := Target{Name: fmt.Sprintf("target-%d", i), URL: "http://example.test", SourceNodeID: "agent", IntervalSec: 5, TimeoutSec: 4, Enabled: true}
		if err := repo.Create(ctx, &target); err != nil {
			t.Fatal(err)
		}
	}
	scheduler := NewScheduler(repo, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	scheduler.tickInterval = 5 * time.Millisecond
	var mu sync.Mutex
	seen := map[string]bool{}
	active, maxActive := 0, 0
	all := make(chan struct{})
	var once sync.Once
	scheduler.SetRemoteProbe(func(ctx context.Context, target Target) (HTTPObservation, error) {
		mu.Lock()
		active++
		if active > maxActive {
			maxActive = active
		}
		mu.Unlock()
		select {
		case <-time.After(20 * time.Millisecond):
		case <-ctx.Done():
		}
		mu.Lock()
		active--
		seen[target.ID] = true
		if len(seen) == 100 {
			once.Do(func() { close(all) })
		}
		mu.Unlock()
		return HTTPObservation{ObservedAt: time.Now().UTC(), StatusCode: 200}, nil
	})
	done := make(chan struct{})
	go func() { scheduler.Run(ctx); close(done) }()
	select {
	case <-all:
	case <-time.After(5 * time.Second):
		mu.Lock()
		count := len(seen)
		mu.Unlock()
		t.Fatalf("only %d of 100 targets sampled", count)
	}
	cancel()
	<-done
	mu.Lock()
	defer mu.Unlock()
	if maxActive > 8 {
		t.Fatalf("active=%d", maxActive)
	}
}

func TestLatestObservationsBoundedPages(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	repo := NewRepository(db.DB())
	tx, err := db.DB().Begin()
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 1001; i++ {
		id := fmt.Sprintf("target-%04d", i)
		if _, err = tx.Exec(`INSERT INTO monitoring_http_targets(id,config_json,updated_at) VALUES(?, '{}', '')`, id); err != nil {
			t.Fatal(err)
		}
		if _, err = tx.Exec(`INSERT INTO monitoring_http_observations(target_id,observed_at,status_code,duration_ns,value_json,error) VALUES(?, '', 200,0,'null','')`, id); err != nil {
			t.Fatal(err)
		}
	}
	if err = tx.Commit(); err != nil {
		t.Fatal(err)
	}
	first, next, err := repo.LatestObservations(ctx, "", 1000)
	if err != nil || len(first) != 1000 || next == "" {
		t.Fatalf("first=%d next=%q err=%v", len(first), next, err)
	}
	second, next, err := repo.LatestObservations(ctx, next, 1000)
	if err != nil || len(second) != 1 || next != "" || second[0].TargetID == first[999].TargetID {
		t.Fatalf("second=%d next=%q err=%v", len(second), next, err)
	}
}
