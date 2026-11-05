package main

import (
	"strings"
	"testing"
	"time"
)

func TestAgentInboundConfigDefaults(t *testing.T) {
	cfg := DefaultConfig()
	if cfg.ConnectionMode != "auto" {
		t.Fatalf("connection_mode = %q, want auto", cfg.ConnectionMode)
	}
	if cfg.Inbound.ListenAddr != "0.0.0.0:18444" || cfg.Inbound.MaxConnections != 8 ||
		cfg.Inbound.HandshakeTimeout != 20*time.Second {
		t.Fatalf("inbound defaults = %+v", cfg.Inbound)
	}
}

func TestAgentInboundConfigEnv(t *testing.T) {
	t.Setenv("NODESTEER_CONNECTION_MODE", "auto")
	t.Setenv("NODESTEER_INBOUND_LISTEN_ADDR", "127.0.0.1:19444")
	t.Setenv("NODESTEER_INBOUND_ENDPOINT", "agent.example.com:18444")
	t.Setenv("NODESTEER_INBOUND_CERT_FILE", "/pki/agent.pem")
	t.Setenv("NODESTEER_INBOUND_KEY_FILE", "/pki/agent-key.pem")
	t.Setenv("NODESTEER_INBOUND_CA_FILE", "/pki/ca.pem")
	t.Setenv("NODESTEER_INBOUND_HUB_CLIENT_FINGERPRINT_FILE", "/pki/hub-fp.txt")
	t.Setenv("NODESTEER_INBOUND_TOKEN_FILE", "/pki/token")
	t.Setenv("NODESTEER_INBOUND_ALLOWED_SOURCE_CIDRS", "10.0.0.0/8, 192.168.0.0/16")
	t.Setenv("NODESTEER_INBOUND_MAX_CONNECTIONS", "2")
	t.Setenv("NODESTEER_INBOUND_HANDSHAKE_TIMEOUT", "9s")
	t.Setenv("NODESTEER_INBOUND_RATE_LIMIT_PER_MINUTE", "3")
	t.Setenv("NODESTEER_INBOUND_GLOBAL_RATE_LIMIT_PER_MINUTE", "30")
	cfg := DefaultConfig()
	if err := applyEnv(&cfg); err != nil {
		t.Fatalf("applyEnv: %v", err)
	}
	if cfg.Inbound.ListenAddr != "127.0.0.1:19444" || cfg.Inbound.Endpoint != "agent.example.com:18444" ||
		cfg.Inbound.TokenFile != "/pki/token" || len(cfg.Inbound.AllowedSourceCIDRs) != 2 ||
		cfg.Inbound.MaxConnections != 2 || cfg.Inbound.HandshakeTimeout != 9*time.Second ||
		cfg.Inbound.RateLimitPerMinute != 3 || cfg.Inbound.GlobalRateLimitPerMinute != 30 {
		t.Fatalf("inbound env = %+v", cfg.Inbound)
	}
}

func TestAgentInboundConfigEnvRejectsInvalidValues(t *testing.T) {
	for _, tc := range []struct{ env, value, want string }{
		{"NODESTEER_INBOUND_MAX_CONNECTIONS", "many", "NODESTEER_INBOUND_MAX_CONNECTIONS"},
		{"NODESTEER_INBOUND_HANDSHAKE_TIMEOUT", "soon", "NODESTEER_INBOUND_HANDSHAKE_TIMEOUT"},
		{"NODESTEER_INBOUND_RATE_LIMIT_PER_MINUTE", "x", "NODESTEER_INBOUND_RATE_LIMIT_PER_MINUTE"},
	} {
		t.Run(tc.env, func(t *testing.T) {
			t.Setenv(tc.env, tc.value)
			cfg := DefaultConfig()
			if err := applyEnv(&cfg); err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("applyEnv error = %v, want %s", err, tc.want)
			}
		})
	}
}
