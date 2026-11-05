package main

import (
	"testing"
	"time"

	"gopkg.in/yaml.v3"
)

func TestMetricsConfigDefaults(t *testing.T) {
	cfg := DefaultConfig()
	if !cfg.MetricsEnabled {
		t.Fatal("metrics_enabled must default to true")
	}
	if cfg.MetricsInterval != 30*time.Second {
		t.Fatalf("metrics_interval default = %v, want 30s", cfg.MetricsInterval)
	}
}

func TestMetricsConfigYAML(t *testing.T) {
	cfg := DefaultConfig()
	if err := yaml.Unmarshal([]byte("metrics_enabled: false\nmetrics_interval: 45s\n"), &cfg); err != nil {
		t.Fatalf("parse yaml: %v", err)
	}
	if cfg.MetricsEnabled {
		t.Fatal("metrics_enabled: false was ignored")
	}
	if cfg.MetricsInterval != 45*time.Second {
		t.Fatalf("metrics_interval = %v, want 45s", cfg.MetricsInterval)
	}
}

func TestMetricsConfigEnv(t *testing.T) {
	t.Setenv("NODESTEER_METRICS_ENABLED", "false")
	t.Setenv("NODESTEER_METRICS_INTERVAL", "15s")
	cfg := DefaultConfig()
	if err := applyEnv(&cfg); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if cfg.MetricsEnabled {
		t.Fatal("NODESTEER_METRICS_ENABLED=false was ignored")
	}
	if cfg.MetricsInterval != 15*time.Second {
		t.Fatalf("metrics interval = %v, want 15s", cfg.MetricsInterval)
	}
}

func TestMetricsConfigEnvInvalidKeepsDefaults(t *testing.T) {
	t.Setenv("NODESTEER_METRICS_ENABLED", "not-a-bool")
	t.Setenv("NODESTEER_METRICS_INTERVAL", "not-a-duration")
	cfg := DefaultConfig()
	if err := applyEnv(&cfg); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if !cfg.MetricsEnabled {
		t.Fatal("invalid metrics_enabled must keep default true")
	}
	if cfg.MetricsInterval != 30*time.Second {
		t.Fatalf("invalid metrics_interval must keep default 30s, got %v", cfg.MetricsInterval)
	}
}
