package hub

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/google/uuid"
)

type probeWait struct {
	nodeID string
	conn   AgentConn
	result chan protocol.MonitorProbeResultPayload
}
type MonitorProbes struct {
	sessions *SessionManager
	nodes *NodeManager
	cidrs    []string
	mu       sync.Mutex
	waiting  map[string]probeWait
}

func NewMonitorProbes(sessions *SessionManager, nodes *NodeManager, cidrs []string) *MonitorProbes {
 return &MonitorProbes{sessions: sessions, nodes:nodes, cidrs: append([]string(nil), cidrs...), waiting: map[string]probeWait{}}
}

func (m *MonitorProbes) Probe(ctx context.Context, target monitoring.Target) (monitoring.HTTPObservation, error) {
	var result monitoring.HTTPObservation
	if m.nodes == nil { return result, errors.New("monitoring node manager unavailable") }
	node, err := m.nodes.GetNode(ctx, target.SourceNodeID)
	if err != nil || node.Status != models.NodeStatusOnline || !node.Capabilities[models.CapMonitorProbe] { return result, errors.New("monitoring agent capability unavailable") }
	conn, ok := m.sessions.Get(target.SourceNodeID)
	if !ok {
		return result, errors.New("monitoring agent is offline")
	}
	data, err := json.Marshal(target)
	if err != nil {
		return result, errors.New("invalid monitoring target")
	}
	id := uuid.NewString()
	wait := probeWait{nodeID: target.SourceNodeID, conn: conn, result: make(chan protocol.MonitorProbeResultPayload, 1)}
	m.mu.Lock()
	m.waiting[id] = wait
	m.mu.Unlock()
	defer func() { m.mu.Lock(); delete(m.waiting, id); m.mu.Unlock() }()
	started := time.Now()
	if err := conn.Send(protocol.NewEnvelope(protocol.MsgMonitorProbe, id, protocol.MonitorProbePayload{Target: data, AllowedCIDRs: m.cidrs})); err != nil {
		return result, errors.New("monitoring agent request failed")
	}
	waitCtx, cancel := context.WithTimeout(ctx, time.Duration(target.TimeoutSec+5)*time.Second)
	defer cancel()
	select {
	case <-waitCtx.Done():
		return result, errors.New("monitoring agent response unavailable")
	case response := <-wait.result:
		current, ok := m.sessions.Get(target.SourceNodeID)
		if !ok || current != conn {
			return result, errors.New("monitoring agent session changed")
		}
		if response.HasValue && len(response.Value) == 0 || !response.HasValue && len(response.Value) != 0 {
			return result, errors.New("monitoring agent returned inconsistent value")
		}
		if response.ErrorCode != "" && response.HasValue {
			return result, errors.New("monitoring agent returned inconsistent error")
		}
		if (target.Kind == "tcp" || target.Kind == "icmp") && response.StatusCode != 0 {
			return result, errors.New("monitoring agent returned invalid network status")
		}
		if response.ErrorCode == "" && (target.Kind == "" || target.Kind == "http") && response.HasValue != (len(target.JSONPath) > 0) {
			return result, errors.New("monitoring agent returned inconsistent HTTP value")
		}
		if response.ErrorCode == "unreachable" || response.ErrorCode == "denied" || response.ErrorCode == "busy" || response.ErrorCode == "invalid" || response.ErrorCode == "untrusted_hub" {
			if response.StatusCode != 0 || response.HasValue {
				return result, errors.New("monitoring agent returned inconsistent error")
			}
		}
		if response.ErrorCode == "" && (target.Kind == "" || target.Kind == "http") && response.StatusCode == 0 {
			return result, errors.New("monitoring agent returned invalid status")
		}
		if response.ErrorCode == "" && (target.Kind == "tcp" || target.Kind == "icmp") && (response.StatusCode != 0 || !response.HasValue) {
			return result, errors.New("monitoring agent returned invalid network probe")
		}
		if response.StatusCode != 0 && (response.StatusCode < 100 || response.StatusCode > 599) {
			return result, errors.New("monitoring agent returned invalid status")
		}
		result.ObservedAt = time.Now().UTC()
		result.Duration = time.Since(started)
		result.StatusCode = response.StatusCode
		if response.HasValue {
			if len(response.Value) == 0 || len(response.Value) > 65536 {
				return result, errors.New("monitoring agent returned invalid value")
			}
			decoder := json.NewDecoder(bytes.NewReader(response.Value))
			decoder.UseNumber()
			if err := decoder.Decode(&result.Value); err != nil {
				return result, errors.New("monitoring agent returned invalid value")
			}
			switch result.Value.(type) {
			case string, bool, json.Number:
				result.HasValue = true
			default:
				return result, errors.New("monitoring agent returned invalid value")
			}
			if target.Kind == "tcp" || target.Kind == "icmp" {
				if value, ok := result.Value.(bool); !ok || !value {
					return result, errors.New("monitoring agent returned invalid network value")
				}
			}
		}
		switch response.ErrorCode {
		case "":
			return result, nil
		case "unreachable":
			return result, monitoring.ErrProbeUnreachable
		case "denied":
			return result, monitoring.ErrDestinationDenied
		default:
			return result, errors.New("monitoring agent observation unavailable")
		}
	}
}

func (m *MonitorProbes) Complete(nodeID, requestID string, conn AgentConn, result protocol.MonitorProbeResultPayload) {
	m.mu.Lock()
	wait, ok := m.waiting[requestID]
	m.mu.Unlock()
	if !ok || wait.nodeID != nodeID || wait.conn != conn {
		return
	}
	select {
	case wait.result <- result:
	default:
	}
}
