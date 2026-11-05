package api

import (
	"database/sql"
	"errors"
	"net/http"
	"strconv"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

func (s *Server) handleMonitoringTargets(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if user := currentUser(r.Context()); user != nil {
		r = r.WithContext(monitoring.WithAuditActor(r.Context(), user.UserID, user.Username))
	}
	if s.monitoring == nil {
		writeErr(w, http.StatusServiceUnavailable, "monitoring unavailable")
		return
	}
	switch r.Method {
	case http.MethodGet:
		targets, err := s.monitoring.List(r.Context())
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, targets)
	case http.MethodPost:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var target monitoring.Target
		if err := readJSON(r, &target); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		if err := target.Validate(); err != nil || target.ID != "" {
			writeErr(w, http.StatusBadRequest, "invalid monitoring target")
			return
		}
		if target.SourceNodeID != "" {
			if s.nodes == nil {
				writeErr(w, http.StatusServiceUnavailable, "nodes unavailable")
				return
			}
			if _, err := s.nodes.GetNode(r.Context(), target.SourceNodeID); err != nil {
				s.writeMonitoringLookupError(w, r, http.StatusBadRequest, "probe node not found", err)
				return
			}
		}
		if target.Kind == "node_metric" {
			if !s.validateNodeMetricTarget(w, r, target) {
				return
			}
		}
		if err := s.monitoring.Create(r.Context(), &target); err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusCreated, target)
	default:
		methodNotAllowed(w, "GET, POST")
	}
}

func (s *Server) handleMonitoringTarget(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if user := currentUser(r.Context()); user != nil {
		r = r.WithContext(monitoring.WithAuditActor(r.Context(), user.UserID, user.Username))
	}
	if s.monitoring == nil {
		writeErr(w, http.StatusServiceUnavailable, "monitoring unavailable")
		return
	}
	id, rest := splitResourcePath(r.URL.Path, "/api/monitoring/http-targets/")
	if id == "" {
		writeErr(w, http.StatusNotFound, "monitoring target not found")
		return
	}
	if len(rest) == 1 && rest[0] == "observations" && r.Method == http.MethodGet {
		if _, err := s.monitoring.Get(r.Context(), id); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring target not found")
			return
		} else if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		limit := 100
		if raw := r.URL.Query().Get("limit"); raw != "" {
			var err error
			limit, err = strconv.Atoi(raw)
			if err != nil || limit < 1 || limit > 1000 {
				writeErr(w, http.StatusBadRequest, "invalid limit")
				return
			}
		}
		observations, err := s.monitoring.Observations(r.Context(), id, limit)
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, observations)
		return
	}
	if len(rest) > 0 {
		writeErr(w, http.StatusNotFound, "monitoring target not found")
		return
	}
	switch r.Method {
	case http.MethodGet:
		target, err := s.monitoring.Get(r.Context(), id)
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring target not found")
			return
		}
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, target)
	case http.MethodPut:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		var target monitoring.Target
		if err := readJSON(r, &target); err != nil || (target.ID != "" && target.ID != id) {
			writeErr(w, http.StatusBadRequest, "invalid request")
			return
		}
		target.ID = id
		if err := target.Validate(); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid monitoring target")
			return
		}
		if target.SourceNodeID != "" {
			if s.nodes == nil {
				writeErr(w, http.StatusServiceUnavailable, "nodes unavailable")
				return
			}
			if _, err := s.nodes.GetNode(r.Context(), target.SourceNodeID); err != nil {
				s.writeMonitoringLookupError(w, r, http.StatusBadRequest, "probe node not found", err)
				return
			}
		}
		if target.Kind == "node_metric" {
			if !s.validateNodeMetricTarget(w, r, target) {
				return
			}
		}
		err := s.monitoring.Update(r.Context(), target)
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring target not found")
			return
		}
		if err != nil {
			s.writeStoreError(w, r, http.StatusConflict, err)
			return
		}
		writeJSON(w, http.StatusOK, target)
	case http.MethodDelete:
		if !s.canAdmin(r) {
			writeErr(w, http.StatusForbidden, "permission denied")
			return
		}
		err := s.monitoring.Delete(r.Context(), id)
		if errors.Is(err, monitoring.ErrTargetHasRules) {
			writeErr(w, http.StatusConflict, "monitoring target is referenced by rules")
			return
		}
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "monitoring target not found")
			return
		}
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		methodNotAllowed(w, "GET, PUT, DELETE")
	}
}

func (s *Server) handleMonitoringLatest(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, "GET")
		return
	}
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if s.monitoring == nil {
		writeErr(w, http.StatusServiceUnavailable, "monitoring unavailable")
		return
	}
	limit := 1000
	if raw := r.URL.Query().Get("limit"); raw != "" {
		var err error
		limit, err = strconv.Atoi(raw)
		if err != nil || limit < 1 || limit > 1000 {
			writeErr(w, http.StatusBadRequest, "invalid limit")
			return
		}
	}
	items, next, err := s.monitoring.LatestObservations(r.Context(), r.URL.Query().Get("after"), limit)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items, "next": next})
}

// validateNodeMetricTarget 校验 node_metric 目标引用的是真实 NodeSteer 节点。
// 指标/序列格式由 Target.Validate 校验；这里只负责节点存在性，驱动错误走统一内部错误。
func (s *Server) validateNodeMetricTarget(w http.ResponseWriter, r *http.Request, target monitoring.Target) bool {
	if s.nodes == nil {
		writeErr(w, http.StatusServiceUnavailable, "nodes unavailable")
		return false
	}
	if _, err := s.nodes.GetNode(r.Context(), target.MonitoredNodeID); err != nil {
		s.writeMonitoringLookupError(w, r, http.StatusBadRequest, "node metric node not found", err)
		return false
	}
	return true
}

// writeMonitoringLookupError 只把“记录不存在”映射为调用方指定的 4xx；
// 驱动错误、SQLITE_BUSY、配置 JSON 损坏等一律走统一内部错误/503，
// 避免把存储故障误报成客户端参数错误。
func (s *Server) writeMonitoringLookupError(w http.ResponseWriter, r *http.Request, notFoundStatus int, notFoundMsg string, err error) {
	if errors.Is(err, sql.ErrNoRows) {
		writeErr(w, notFoundStatus, notFoundMsg)
		return
	}
	s.writeInternalError(w, r, err)
}
