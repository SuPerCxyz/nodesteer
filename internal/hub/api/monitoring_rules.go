package api

import (
	"database/sql"
	"errors"
	"net/http"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

func (s *Server) handleMonitoringRules(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if s.monitoring == nil {
		writeErr(w, http.StatusServiceUnavailable, "monitoring unavailable")
		return
	}
	if user := currentUser(r.Context()); user != nil {
		r = r.WithContext(monitoring.WithAuditActor(r.Context(), user.UserID, user.Username))
	}
	switch r.Method {
	case http.MethodGet:
		rules, err := s.monitoring.Rules(r.Context(), "")
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, rules)
	case http.MethodPost:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var rule monitoring.Rule
		if err := readJSON(r, &rule); err != nil || rule.ID != "" || rule.Validate() != nil {
			writeErr(w, http.StatusBadRequest, "invalid monitoring rule")
			return
		}
		if s.schedules == nil {
			writeErr(w, http.StatusServiceUnavailable, "schedules unavailable")
			return
		}
		target, err := s.monitoring.Get(r.Context(), rule.TargetID)
		if err != nil {
			s.writeMonitoringLookupError(w, r, http.StatusBadRequest, "invalid rule target or comparison", err)
			return
		}
		if monitoring.ValidateRuleTarget(rule, target) != nil {
			writeErr(w, http.StatusBadRequest, "invalid rule target or comparison")
			return
		}
		if target.Kind == "node_metric" && !s.validateNodeMetricTarget(w, r, target) {
			return
		}
		for _, scheduleID := range []string{rule.ScheduleID, rule.VerifyScheduleID, rule.CompensateScheduleID} {
			if scheduleID != "" {
				if _, err := s.schedules.Get(r.Context(), scheduleID); err != nil {
					s.writeMonitoringLookupError(w, r, http.StatusBadRequest, "schedule not found", err)
					return
				}
			}
		}
		if err := s.monitoring.CreateRule(r.Context(), &rule); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusBadRequest, "monitoring target not found")
			return
		} else if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusCreated, rule)
	default:
		methodNotAllowed(w, "GET, POST")
	}
}

func (s *Server) handleMonitoringRule(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if s.monitoring == nil {
		writeErr(w, http.StatusServiceUnavailable, "monitoring unavailable")
		return
	}
	if user := currentUser(r.Context()); user != nil {
		r = r.WithContext(monitoring.WithAuditActor(r.Context(), user.UserID, user.Username))
	}
	id, rest := splitResourcePath(r.URL.Path, "/api/monitoring/rules/")
	if id == "" {
		writeErr(w, http.StatusNotFound, "monitoring rule not found")
		return
	}
	if len(rest) == 1 && rest[0] == "resolve-unknown" && r.Method == http.MethodPost {
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.monitoring.ResolveUnknown(r.Context(), id); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring rule not found")
			return
		} else if err != nil {
			s.writeStoreError(w, r, http.StatusConflict, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if len(rest) == 1 && rest[0] == "abandon-unknown" && r.Method == http.MethodPost {
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if err := s.monitoring.AbandonUnknown(r.Context(), id); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring rule not found")
			return
		} else if err != nil {
			s.writeStoreError(w, r, http.StatusConflict, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if len(rest) == 1 && rest[0] == "events" && r.Method == http.MethodGet {
		if _, err := s.monitoring.GetRule(r.Context(), id); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring rule not found")
			return
		} else if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		events, err := s.monitoring.RuleEvents(r.Context(), id)
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, events)
		return
	}
	if len(rest) > 0 {
		writeErr(w, http.StatusNotFound, "monitoring rule not found")
		return
	}
	switch r.Method {
	case http.MethodGet:
		rule, err := s.monitoring.GetRule(r.Context(), id)
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring rule not found")
			return
		}
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, rule)
	case http.MethodPut:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var rule monitoring.Rule
		if err := readJSON(r, &rule); err != nil || (rule.ID != "" && rule.ID != id) {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		rule.ID = id
		if rule.Validate() != nil {
			writeErr(w, http.StatusBadRequest, "invalid monitoring rule")
			return
		}
		if s.schedules == nil {
			writeErr(w, http.StatusServiceUnavailable, "schedules unavailable")
			return
		}
		target, err := s.monitoring.Get(r.Context(), rule.TargetID)
		if err != nil {
			s.writeMonitoringLookupError(w, r, http.StatusBadRequest, "invalid rule target or comparison", err)
			return
		}
		if monitoring.ValidateRuleTarget(rule, target) != nil {
			writeErr(w, http.StatusBadRequest, "invalid rule target or comparison")
			return
		}
		if target.Kind == "node_metric" && !s.validateNodeMetricTarget(w, r, target) {
			return
		}
		for _, scheduleID := range []string{rule.ScheduleID, rule.VerifyScheduleID, rule.CompensateScheduleID} {
			if scheduleID != "" {
				if _, err := s.schedules.Get(r.Context(), scheduleID); err != nil {
					s.writeMonitoringLookupError(w, r, http.StatusBadRequest, "schedule not found", err)
					return
				}
			}
		}
		if err := s.monitoring.UpdateRule(r.Context(), rule); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring rule not found")
			return
		} else if err != nil {
			s.writeStoreError(w, r, http.StatusConflict, err)
			return
		}
		writeJSON(w, http.StatusOK, rule)
	case http.MethodDelete:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		if _, err := s.monitoring.GetRule(r.Context(), id); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring rule not found")
			return
		} else if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		if err := s.monitoring.DeleteRule(r.Context(), id); errors.Is(err, sql.ErrNoRows) {
			// DeleteRule 只删除无 pending 执行的规则；行存在但未删除表示有未解决执行。
			writeErr(w, http.StatusConflict, "monitoring rule has an unresolved execution")
			return
		} else if err != nil {
			s.writeStoreError(w, r, http.StatusConflict, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		methodNotAllowed(w, "GET, PUT, DELETE")
	}
}
