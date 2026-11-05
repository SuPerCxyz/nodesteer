package agent

import (
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func TestMonitorSecretRequiresExplicitDestination(t *testing.T) {
	const name = "NODESTEER_MONITOR_SECRET_EXAMPLE"
	a := &Agent{cfg: Config{MonitoringSecretDestinations: map[string][]string{name: {"https://allowed.example"}}}}
	target := monitoring.Target{URL: "https://allowed.example/health", CredentialEnv: name}
	if !a.monitorSecretsAllowed(target) {
		t.Fatal("explicitly allowed destination denied")
	}
	target.URL = "https://other.example/health"
	if a.monitorSecretsAllowed(target) {
		t.Fatal("credential allowed to unapproved destination")
	}
	target.URL = "https://allowed.example:444/health"
	if a.monitorSecretsAllowed(target) {
		t.Fatal("credential allowed on unapproved port")
	}
	target.URL = "https://allowed.example/health"
	target.QueryParams = []monitoring.QueryParam{{Name: "token", SecretEnv: "NODESTEER_MONITOR_SECRET_OTHER"}}
	if a.monitorSecretsAllowed(target) {
		t.Fatal("unbound query credential allowed")
	}
}

func TestMonitorProbeRejectsReplayAndStaleRequest(t *testing.T) {
	a := &Agent{}
	now := time.Now()
	request := protocol.Envelope{ID: "request", Timestamp: now}
	if !a.claimProbeRequest(request, now) {
		t.Fatal("fresh request rejected")
	}
	if a.claimProbeRequest(request, now) {
		t.Fatal("replayed request accepted")
	}
	if a.claimProbeRequest(protocol.Envelope{ID: "old", Timestamp: now.Add(-time.Minute - time.Second)}, now) {
		t.Fatal("stale request accepted")
	}
	if a.claimProbeRequest(protocol.Envelope{ID: "future", Timestamp: now.Add(time.Minute)}, now) {
		t.Fatal("future request accepted")
	}
}
