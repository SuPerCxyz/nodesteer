package hub

import (
	"context"
	"fmt"
	"net"
	"strconv"
	"strings"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/store"
	"github.com/robfig/cron/v3"
)

var validConditionOperators = map[string]bool{"==": true, "!=": true, ">": true, "<": true, ">=": true, "<=": true}

func validateTarget(ctx context.Context, st store.Store, target models.Target) error {
	switch target.Type {
	case "node":
		if len(target.NodeIDs) == 0 {
			return store.ErrInvalidTask("node target requires node_ids")
		}
		for _, id := range target.NodeIDs {
			if id == "" {
				return store.ErrInvalidTask("node target contains empty node id")
			}
			if _, err := st.GetNode(ctx, id); err != nil {
				return store.ErrInvalidTask("target node not found")
			}
		}
	case "group":
		if len(target.GroupIDs) == 0 {
			return store.ErrInvalidTask("group target requires group_ids")
		}
		for _, id := range target.GroupIDs {
			if _, err := st.GetGroup(ctx, id); err != nil {
				return store.ErrInvalidTask("target group not found")
			}
		}
	case "label":
		if strings.TrimSpace(target.LabelKey) == "" {
			return store.ErrInvalidTask("label target requires label_key")
		}
	default:
		return store.ErrInvalidTask("target type must be node, group, or label")
	}
	return nil
}

func validateCondition(ctx context.Context, st store.Store, c *models.Condition) error {
	if c == nil {
		return nil
	}
	switch c.Type {
	case "and":
		if len(c.And) == 0 {
			return store.ErrInvalidTask("and condition requires at least one child")
		}
		for i := range c.And {
			if err := validateCondition(ctx, st, &c.And[i]); err != nil {
				return err
			}
		}
	case "local":
		if c.Local == nil {
			return store.ErrInvalidTask("local condition is missing")
		}
		if !validConditionOperators[c.Local.Operator] {
			return store.ErrInvalidTask("invalid local condition operator")
		}
		switch c.Local.Metric {
		case "cpu_usage", "memory_usage":
			if _, err := strconv.ParseFloat(c.Local.Value, 64); err != nil {
				return store.ErrInvalidTask("numeric local condition requires numeric value")
			}
		case "disk_usage", "file_exists", "dir_exists", "process_exists", "port_listening":
			if c.Local.Path == "" {
				return store.ErrInvalidTask("local condition requires path")
			}
			if c.Local.Metric == "port_listening" {
				port, err := strconv.Atoi(c.Local.Path)
				if err != nil || port < 1 || port > 65535 {
					return store.ErrInvalidTask("port_listening path must be a valid port")
				}
			}
		case "command_result":
			if c.Local.Command == "" {
				return store.ErrInvalidTask("command_result requires command")
			}
		default:
			return store.ErrInvalidTask("unknown local condition metric")
		}
	case "remote":
		if c.Remote == nil {
			return store.ErrInvalidTask("remote condition is missing")
		}
		if c.Remote.NodeID == "" {
			return store.ErrInvalidTask("remote condition requires node_id")
		}
		if _, err := st.GetNode(ctx, c.Remote.NodeID); err != nil {
			return store.ErrInvalidTask("remote condition node not found")
		}
		if c.Remote.Property != "online" && c.Remote.Property != "last_execution" {
			return store.ErrInvalidTask("unknown remote condition property")
		}
		if c.Remote.Property == "last_execution" && c.Remote.TaskID == "" {
			return store.ErrInvalidTask("last_execution condition requires task_id")
		}
		if c.Remote.Property == "last_execution" {
			if _, err := st.GetTask(ctx, c.Remote.TaskID); err != nil {
				return store.ErrInvalidTask("remote condition task not found")
			}
		}
		if c.Remote.Operator != "==" && c.Remote.Operator != "!=" {
			return store.ErrInvalidTask("remote condition operator must be == or !=")
		}
		if c.Remote.Property == "online" {
			value := strings.ToLower(c.Remote.Value)
			switch value {
			case models.NodeStatusOnline, models.NodeStatusOffline, models.NodeStatusMaintenance, models.NodeStatusDisabled, protocolUnknown:
			default:
				return store.ErrInvalidTask("invalid online condition value")
			}
		}
	default:
		return store.ErrInvalidTask("condition type must be local, remote, or and")
	}
	return nil
}

const protocolUnknown = "unknown"

func validateSchedule(s *models.Schedule) error {
	if s.TaskID == "" {
		return fmt.Errorf("task_id required")
	}
	switch s.Type {
	case models.ScheduleTypeCron:
		if strings.TrimSpace(s.Expression) == "" {
			return fmt.Errorf("cron expression required")
		}
		if _, err := cron.ParseStandard(s.Expression); err != nil {
			return fmt.Errorf("invalid cron expression: %w", err)
		}
	case models.ScheduleTypeInterval:
		if s.IntervalSec < 1 {
			return fmt.Errorf("interval_sec must be positive")
		}
	case models.ScheduleTypeOneTime:
		if s.RunAt.IsZero() {
			return fmt.Errorf("run_at required")
		}
	default:
		return fmt.Errorf("schedule type must be cron, interval, or one_time")
	}
	if _, err := time.LoadLocation(s.Timezone); err != nil {
		return fmt.Errorf("invalid timezone: %w", err)
	}
	if s.ExecutionOwner != models.ExecutionOwnerHub && s.ExecutionOwner != models.ExecutionOwnerAgent {
		return fmt.Errorf("execution_owner must be hub or agent")
	}
	if s.OfflinePolicy != models.OfflinePolicyHubOnlineRequired && s.OfflinePolicy != models.OfflinePolicyAllowOffline {
		return fmt.Errorf("invalid offline_policy")
	}
	if s.MisfirePolicy != models.MisfirePolicySkip && s.MisfirePolicy != models.MisfirePolicyRunOnce {
		return fmt.Errorf("invalid misfire_policy")
	}
	return nil
}

func validateApplicationPath(path string) error {
	if path == "" || !strings.HasPrefix(path, "/") || strings.ContainsRune(path, 0) || strings.Contains(path, "..") {
		return fmt.Errorf("invalid application path")
	}
	return nil
}

func validateApplicationAddress(target string) error {
	if target == "" {
		return nil
	}
	if _, _, err := net.SplitHostPort(target); err != nil {
		return fmt.Errorf("invalid health check address")
	}
	return nil
}

func validateApplicationDefinition(a *models.Application) error {
	if a == nil || strings.TrimSpace(a.Name) == "" {
		return fmt.Errorf("application name required")
	}
	if a.BinaryPath != "" {
		if err := validateApplicationPath(a.BinaryPath); err != nil {
			return fmt.Errorf("binary_path: %w", err)
		}
	}
	if a.ConfigPath != "" {
		if err := validateApplicationPath(a.ConfigPath); err != nil {
			return fmt.Errorf("config_path: %w", err)
		}
	}
	if a.UnitName == "" {
		return fmt.Errorf("unit_name required")
	}
	unit := a.UnitName
	if !strings.HasSuffix(unit, ".service") {
		unit += ".service"
	}
	for _, r := range unit {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-' || r == '_' || r == '.' || r == '@' {
			continue
		}
		return fmt.Errorf("invalid unit_name")
	}
	if a.HealthCheck != nil {
		switch a.HealthCheck.Type {
		case models.HealthTypeSystemd:
		case models.HealthTypeTCP:
			if err := validateApplicationAddress(a.HealthCheck.Target); err != nil {
				return err
			}
		case models.HealthTypeHTTP, models.HealthTypeCommand:
			if a.HealthCheck.Target == "" {
				return fmt.Errorf("health check target required")
			}
		default:
			return fmt.Errorf("invalid health check type")
		}
	}
	return nil
}
