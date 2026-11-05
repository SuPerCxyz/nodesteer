package main

import (
	"strings"
	"testing"
	"time"

	"gopkg.in/yaml.v3"
)

func TestInboundYAMLConfig(t *testing.T) {
	var cfg Config
	raw := `
inbound_enabled: true
inbound_ca_file: /pki/ca.pem
inbound_ca_key_file: /pki/ca-key.pem
inbound_dial_concurrency: 8
inbound_dial_timeout: 25s
inbound_backoff_initial: 4s
inbound_backoff_max: 90s
inbound_allowed_ports: [18444, 28443]
inbound_denied_cidrs: ["10.9.0.0/16"]
inbound_allow_loopback: true
`
	if err := yaml.Unmarshal([]byte(raw), &cfg); err != nil {
		t.Fatalf("yaml unmarshal: %v", err)
	}
	if !cfg.InboundEnabled || cfg.InboundCAFile != "/pki/ca.pem" || cfg.InboundCAKeyFile != "/pki/ca-key.pem" ||
		cfg.InboundDialConcurrency != 8 || cfg.InboundDialTimeout != 25*time.Second ||
		cfg.InboundBackoffInitial != 4*time.Second || cfg.InboundBackoffMax != 90*time.Second ||
		len(cfg.InboundAllowedPorts) != 2 || len(cfg.InboundDeniedCIDRs) != 1 || !cfg.InboundAllowLoopback {
		t.Fatalf("yaml inbound config = %+v", cfg)
	}
	if err := validateInboundConfig(cfg); err != nil {
		t.Fatalf("yaml inbound config rejected: %v", err)
	}
}

func TestInboundConfigDefaultsAndValidation(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.InboundEnabled {
		t.Fatal("inbound must default to disabled")
	}
	if len(cfg.InboundAllowedPorts) != 1 || cfg.InboundAllowedPorts[0] != 18444 {
		t.Fatalf("default allowed ports = %v", cfg.InboundAllowedPorts)
	}
	if cfg.InboundDialConcurrency != 16 || cfg.InboundDialTimeout != 20*time.Second ||
		cfg.InboundBackoffInitial != 2*time.Second || cfg.InboundBackoffMax != 60*time.Second {
		t.Fatalf("unexpected inbound dial defaults: %+v", cfg)
	}
	if err := validateInboundConfig(cfg); err != nil {
		t.Fatalf("default inbound config rejected: %v", err)
	}

	cfg.InboundEnabled = true
	if err := validateInboundConfig(cfg); err != nil {
		t.Fatalf("enabled inbound config rejected: %v", err)
	}
	cfg.InboundDialConcurrency = 257
	if err := validateInboundConfig(cfg); err == nil {
		t.Fatal("concurrency 257 must be rejected")
	}
	cfg = DefaultConfig()
	cfg.InboundCAFile = "/tmp/ca.pem"
	if err := validateInboundConfig(cfg); err == nil || !strings.Contains(err.Error(), "together") {
		t.Fatalf("partial CA config err = %v", err)
	}
	cfg = DefaultConfig()
	cfg.InboundDeniedCIDRs = []string{"not-a-cidr"}
	if err := validateInboundConfig(cfg); err == nil {
		t.Fatal("invalid denied CIDR must be rejected even when inbound is disabled")
	}
	cfg = DefaultConfig()
	cfg.InboundEnabled = true
	cfg.InboundBackoffMax = time.Second
	cfg.InboundBackoffInitial = 2 * time.Second
	if err := validateInboundConfig(cfg); err == nil {
		t.Fatal("backoff max below initial must be rejected")
	}
}

func TestInboundConfigEnvOverrides(t *testing.T) {
	t.Setenv("NODESTEER_INBOUND_ENABLED", "true")
	t.Setenv("NODESTEER_INBOUND_CA_FILE", "/pki/ca.pem")
	t.Setenv("NODESTEER_INBOUND_CA_KEY_FILE", "/pki/ca-key.pem")
	t.Setenv("NODESTEER_INBOUND_CLIENT_CERT_FILE", "/pki/client.pem")
	t.Setenv("NODESTEER_INBOUND_CLIENT_KEY_FILE", "/pki/client-key.pem")
	t.Setenv("NODESTEER_INBOUND_DIAL_CONCURRENCY", "4")
	t.Setenv("NODESTEER_INBOUND_DIAL_TIMEOUT", "15s")
	t.Setenv("NODESTEER_INBOUND_BACKOFF_INITIAL", "3s")
	t.Setenv("NODESTEER_INBOUND_BACKOFF_MAX", "45s")
	t.Setenv("NODESTEER_INBOUND_ALLOWED_PORTS", "18444, 28443")
	t.Setenv("NODESTEER_INBOUND_DENIED_CIDRS", "10.1.0.0/16, 192.168.50.0/24")
	t.Setenv("NODESTEER_INBOUND_ALLOW_LOOPBACK", "true")

	cfg := DefaultConfig()
	if err := applyEnv(&cfg); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if !cfg.InboundEnabled || cfg.InboundDialConcurrency != 4 || cfg.InboundDialTimeout != 15*time.Second ||
		cfg.InboundBackoffInitial != 3*time.Second || cfg.InboundBackoffMax != 45*time.Second ||
		!cfg.InboundAllowLoopback || len(cfg.InboundAllowedPorts) != 2 ||
		len(cfg.InboundDeniedCIDRs) != 2 {
		t.Fatalf("inbound env overrides = %+v", cfg)
	}
	if cfg.InboundCAFile != "/pki/ca.pem" || cfg.InboundClientKeyFile != "/pki/client-key.pem" {
		t.Fatalf("inbound PKI env overrides = %+v", cfg)
	}
	if err := validateInboundConfig(cfg); err != nil {
		t.Fatalf("env inbound config rejected: %v", err)
	}
}

func TestInboundConfigEnvRejectsInvalidValues(t *testing.T) {
	for _, tc := range []struct {
		env  string
		val  string
		want string
	}{
		{"NODESTEER_INBOUND_ENABLED", "flase", "NODESTEER_INBOUND_ENABLED"},
		{"NODESTEER_INBOUND_ALLOW_LOOPBACK", "flase", "NODESTEER_INBOUND_ALLOW_LOOPBACK"},
		{"NODESTEER_INBOUND_DIAL_CONCURRENCY", "many", "NODESTEER_INBOUND_DIAL_CONCURRENCY"},
		{"NODESTEER_INBOUND_DIAL_TIMEOUT", "soon", "NODESTEER_INBOUND_DIAL_TIMEOUT"},
		{"NODESTEER_INBOUND_ALLOWED_PORTS", "18444,abc", "NODESTEER_INBOUND_ALLOWED_PORTS"},
	} {
		t.Run(tc.env, func(t *testing.T) {
			t.Setenv(tc.env, tc.val)
			cfg := DefaultConfig()
			if err := applyEnv(&cfg); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("applyEnv error = %v, want %s", err, tc.want)
			}
		})
	}
}
