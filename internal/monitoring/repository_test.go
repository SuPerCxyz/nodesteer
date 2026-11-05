package monitoring

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestRepositoryTargetAndObservations(t *testing.T) {
	db, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	repo := NewRepository(db.DB())
	ctx := context.Background()
	target := Target{Name: "service", URL: "https://example.test", JSONPath: []string{"value"}, CredentialEnv: "NODESTEER_MONITOR_SECRET_API", IntervalSec: 60, TimeoutSec: 5, Enabled: true}
	if err := repo.Create(ctx, &target); err != nil {
		t.Fatal(err)
	}
	got, err := repo.Get(ctx, target.ID)
	if err != nil || got.CredentialEnv != target.CredentialEnv {
		t.Fatalf("target = %+v, err = %v", got, err)
	}
	if err := repo.AddObservation(ctx, target.ID, HTTPObservation{ObservedAt: time.Now(), StatusCode: 200, HasValue: true, Value: json.Number("0")}, nil); err != nil {
		t.Fatal(err)
	}
	observations, err := repo.Observations(ctx, target.ID, 10)
	if err != nil || len(observations) != 1 || string(observations[0].Value) != "0" {
		t.Fatalf("observations = %+v, err = %v", observations, err)
	}
	got.Enabled = false
	if err := repo.Update(ctx, got); err != nil {
		t.Fatal(err)
	}
	if err := repo.Delete(ctx, target.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := repo.Get(ctx, target.ID); !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("deleted target: %v", err)
	}
	if err := repo.AddObservation(ctx, target.ID, HTTPObservation{}, nil); !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("orphan observation: %v", err)
	}
	observations, err = repo.Observations(ctx, target.ID, 10)
	if err != nil || len(observations) != 0 {
		t.Fatalf("observations after delete = %+v, err = %v", observations, err)
	}
}

func TestTargetValidation(t *testing.T) {
	target := Target{Name: "service", URL: "http://example.test", CredentialEnv: "NODESTEER_MONITOR_SECRET_API", IntervalSec: 60, TimeoutSec: 5}
	if err := target.Validate(); err == nil {
		t.Fatal("credentials over HTTP allowed")
	}
	target.URL = "https://example.test"
	target.CredentialEnv = "HOME"
	if err := target.Validate(); err == nil {
		t.Fatal("arbitrary environment reference allowed")
	}
	target.CredentialEnv = ""
	if err := target.Validate(); err != nil {
		t.Fatal(err)
	}
}
