package agent

import (
	"encoding/json"
	"errors"
	"net"
	"net/url"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// OnMonitorProbe executes read-only probes without consuming the task executor.
func (a *Agent) OnMonitorProbe(env protocol.Envelope) {
	if a.conn == nil || !a.conn.HandshakeComplete() {
		return
	}
	hubURL, err := url.Parse(a.cfg.HubURL)
	if err != nil || (hubURL.Scheme != "wss" && !a.cfg.AllowInsecureMonitorHub) {
		a.conn.Send(protocol.NewEnvelope(protocol.MsgMonitorProbeResult, env.ID, protocol.MonitorProbeResultPayload{ErrorCode: "untrusted_hub"}))
		return
	}
	if !a.claimProbeRequest(env, time.Now()) {
		return
	}
	select {
	case a.probeSlots <- struct{}{}:
	default:
		a.conn.Send(protocol.NewEnvelope(protocol.MsgMonitorProbeResult, env.ID, protocol.MonitorProbeResultPayload{ErrorCode: "busy"}))
		return
	}
	go func() {
		defer func() { <-a.probeSlots }()
		if !a.conn.HandshakeComplete() {
			return
		}
		result := protocol.MonitorProbeResultPayload{}
		var request protocol.MonitorProbePayload
		var target monitoring.Target
		if json.Unmarshal(env.Payload, &request) != nil || json.Unmarshal(request.Target, &target) != nil || target.Validate() != nil || target.SourceNodeID != a.nodeID || !a.monitorSecretsAllowed(target) {
			result.ErrorCode = "invalid"
		} else if policy, err := monitoring.IntersectNetworkPolicies(request.AllowedCIDRs, a.cfg.MonitoringAllowedCIDRs); err != nil {
			result.ErrorCode = "invalid"
		} else {
			observation, err := monitoring.Collect(a.ctx, target, policy.Transport(), policy)
			result.StatusCode, result.DurationNS, result.HasValue = observation.StatusCode, int64(observation.Duration), observation.HasValue
			if observation.HasValue {
				result.Value, _ = json.Marshal(observation.Value)
			}
			switch {
			case errors.Is(err, monitoring.ErrProbeUnreachable):
				result.ErrorCode = "unreachable"
			case errors.Is(err, monitoring.ErrDestinationDenied):
				result.ErrorCode = "denied"
			case err != nil:
				result.ErrorCode = "invalid_observation"
			}
		}
		a.conn.Send(protocol.NewEnvelope(protocol.MsgMonitorProbeResult, env.ID, result))
	}()
}

func (a *Agent) claimProbeRequest(env protocol.Envelope, now time.Time) bool {
	if env.ID == "" || env.Timestamp.IsZero() || now.Sub(env.Timestamp) > time.Minute || env.Timestamp.Sub(now) > 30*time.Second {
		return false
	}
	a.probeMu.Lock()
	defer a.probeMu.Unlock()
	if a.probeSeen == nil {
		a.probeSeen = map[string]time.Time{}
	}
	for id, seen := range a.probeSeen {
		if now.Sub(seen) > 2*time.Minute {
			delete(a.probeSeen, id)
		}
	}
	_, duplicate := a.probeSeen[env.ID]
	if duplicate || len(a.probeSeen) >= 1024 {
		return false
	}
	a.probeSeen[env.ID] = now
	return true
}

func monitorOrigin(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil {
		return ""
	}
	port := u.Port()
	if port == "" {
		port = "443"
	}
	return "https://" + net.JoinHostPort(strings.ToLower(u.Hostname()), port)
}

func (a *Agent) monitorSecretsAllowed(target monitoring.Target) bool {
	names := []string{}
	if target.CredentialEnv != "" {
		names = append(names, target.CredentialEnv)
	}
	for _, parameter := range target.QueryParams {
		if parameter.SecretEnv != "" {
			names = append(names, parameter.SecretEnv)
		}
	}
	if len(names) == 0 {
		return true
	}
	origin := monitorOrigin(target.URL)
	if origin == "" {
		return false
	}
	for _, name := range names {
		allowed := false
		for _, destination := range a.cfg.MonitoringSecretDestinations[name] {
			if monitorOrigin(destination) == origin {
				allowed = true
				break
			}
		}
		if !allowed {
			return false
		}
	}
	return true
}
