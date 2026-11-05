package api

import (
	"database/sql"
	"errors"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
)

const alertMaxSilenceSeconds = 30 * 24 * 60 * 60

type alertEventListResponse struct {
	Events []monitoring.AlertEvent `json:"events"`
	Total  int                     `json:"total"`
	Limit  int                     `json:"limit"`
	Offset int                     `json:"offset"`
}

// alertRequest 注入审计操作者；所有告警写操作都会写审计。
func (s *Server) alertRequest(r *http.Request) *http.Request {
	if user := currentUser(r.Context()); user != nil {
		return r.WithContext(monitoring.WithAuditActor(r.Context(), user.UserID, user.Username))
	}
	return r
}

func (s *Server) alertingAvailable(w http.ResponseWriter) bool {
	if s.alerts == nil {
		writeErr(w, http.StatusServiceUnavailable, "alerting unavailable")
		return false
	}
	return true
}

func (s *Server) writeAlertingError(w http.ResponseWriter, r *http.Request, err error, invalidMessage string) {
	switch {
	case errors.Is(err, monitoring.ErrInvalidAlertRule), errors.Is(err, monitoring.ErrInvalidNotificationCh):
		writeErr(w, http.StatusBadRequest, invalidMessage)
	case errors.Is(err, monitoring.ErrAlertChannelNotFound):
		writeErr(w, http.StatusBadRequest, "notification channel not found")
	case errors.Is(err, monitoring.ErrChannelInUse):
		writeErr(w, http.StatusConflict, "notification channel is referenced by alert rules")
	case errors.Is(err, monitoring.ErrChannelNameExists):
		writeErr(w, http.StatusConflict, "notification channel name already exists")
	case errors.Is(err, monitoring.ErrAlertConflict):
		writeErr(w, http.StatusConflict, "alert event is not active")
	case errors.Is(err, sql.ErrNoRows):
		writeErr(w, http.StatusNotFound, "alert resource not found")
	default:
		s.writeInternalError(w, r, err)
	}
}

func (s *Server) validateAlertRuleReferences(w http.ResponseWriter, r *http.Request, rule monitoring.AlertRule) bool {
	if rule.NodeID != "" {
		if _, err := s.store.GetNode(r.Context(), rule.NodeID); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusBadRequest, "node not found")
			return false
		} else if err != nil {
			s.writeInternalError(w, r, err)
			return false
		}
	}
	if err := s.alerts.ChannelIDsExist(r.Context(), rule.Channels); errors.Is(err, monitoring.ErrAlertChannelNotFound) {
		writeErr(w, http.StatusBadRequest, "notification channel not found")
		return false
	} else if err != nil {
		s.writeInternalError(w, r, err)
		return false
	}
	return true
}

// handleAlertRules 处理 /api/monitoring/alerts/rules。
func (s *Server) handleAlertRules(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if !s.alertingAvailable(w) {
		return
	}
	r = s.alertRequest(r)
	switch r.Method {
	case http.MethodGet:
		rules, err := s.alerts.ListAlertRules(r.Context())
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, rules)
	case http.MethodPost:
		var rule monitoring.AlertRule
		if err := readJSON(r, &rule); err != nil || rule.ID != "" {
			writeErr(w, http.StatusBadRequest, "invalid alert rule")
			return
		}
		rule.Normalize()
		if err := rule.Validate(); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid alert rule")
			return
		}
		if !s.validateAlertRuleReferences(w, r, rule) {
			return
		}
		if err := s.alerts.CreateAlertRule(r.Context(), &rule); err != nil {
			s.writeAlertingError(w, r, err, "invalid alert rule")
			return
		}
		writeJSON(w, http.StatusCreated, rule)
	default:
		methodNotAllowed(w, "GET, POST")
	}
}

// handleAlertRule 处理 /api/monitoring/alerts/rules/{id}[/enable|silence|events]。
func (s *Server) handleAlertRule(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if !s.alertingAvailable(w) {
		return
	}
	r = s.alertRequest(r)
	id, rest := splitResourcePath(r.URL.Path, "/api/monitoring/alerts/rules/")
	if id == "" {
		writeErr(w, http.StatusNotFound, "alert rule not found")
		return
	}
	if len(rest) > 0 {
		switch {
		case len(rest) == 1 && rest[0] == "enable":
			s.handleAlertRuleEnable(w, r, id)
		case len(rest) == 1 && rest[0] == "silence":
			s.handleAlertRuleSilence(w, r, id)
		case len(rest) == 1 && rest[0] == "events":
			s.handleAlertRuleEvents(w, r, id)
		default:
			writeErr(w, http.StatusNotFound, "alert rule not found")
		}
		return
	}

	switch r.Method {
	case http.MethodGet:
		rule, err := s.alerts.GetAlertRule(r.Context(), id)
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "alert rule not found")
			return
		}
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, rule)
	case http.MethodPut:
		var rule monitoring.AlertRule
		if err := readJSON(r, &rule); err != nil || (rule.ID != "" && rule.ID != id) {
			writeErr(w, http.StatusBadRequest, "invalid alert rule")
			return
		}
		rule.ID = id
		rule.Normalize()
		if err := rule.Validate(); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid alert rule")
			return
		}
		if !s.validateAlertRuleReferences(w, r, rule) {
			return
		}
		if err := s.alerts.UpdateAlertRule(r.Context(), rule); err != nil {
			s.writeAlertingError(w, r, err, "invalid alert rule")
			return
		}
		updated, err := s.alerts.GetAlertRule(r.Context(), id)
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, updated)
	case http.MethodDelete:
		if err := s.alerts.DeleteAlertRule(r.Context(), id); err != nil {
			s.writeAlertingError(w, r, err, "invalid alert rule")
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		methodNotAllowed(w, "GET, PUT, DELETE")
	}
}

func (s *Server) handleAlertRuleEnable(w http.ResponseWriter, r *http.Request, id string) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, "POST")
		return
	}
	var request struct {
		Enabled *bool `json:"enabled"`
	}
	if err := readJSON(r, &request); err != nil || request.Enabled == nil {
		writeErr(w, http.StatusBadRequest, "invalid alert rule")
		return
	}
	if err := s.alerts.SetAlertRuleEnabled(r.Context(), id, *request.Enabled); err != nil {
		s.writeAlertingError(w, r, err, "invalid alert rule")
		return
	}
	rule, err := s.alerts.GetAlertRule(r.Context(), id)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, rule)
}

func (s *Server) handleAlertRuleSilence(w http.ResponseWriter, r *http.Request, id string) {
	switch r.Method {
	case http.MethodPost:
		var request struct {
			DurationSec int    `json:"duration_sec"`
			Until       string `json:"until"`
		}
		if err := readJSON(r, &request); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid silence request")
			return
		}
		if (request.DurationSec <= 0) == (strings.TrimSpace(request.Until) == "") {
			writeErr(w, http.StatusBadRequest, "exactly one of duration_sec or until is required")
			return
		}
		var until time.Time
		if request.DurationSec > 0 {
			if request.DurationSec > alertMaxSilenceSeconds {
				writeErr(w, http.StatusBadRequest, "invalid silence duration")
				return
			}
			until = time.Now().UTC().Add(time.Duration(request.DurationSec) * time.Second)
		} else {
			parsed, err := time.Parse(time.RFC3339Nano, request.Until)
			if err != nil || !parsed.After(time.Now().UTC()) || parsed.After(time.Now().UTC().Add(30*24*time.Hour)) {
				writeErr(w, http.StatusBadRequest, "invalid silence time")
				return
			}
			until = parsed.UTC()
		}
		if err := s.alerts.SetAlertRuleSilence(r.Context(), id, until); err != nil {
			s.writeAlertingError(w, r, err, "invalid alert rule")
			return
		}
		rule, err := s.alerts.GetAlertRule(r.Context(), id)
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, rule)
	case http.MethodDelete:
		if err := s.alerts.ClearAlertRuleSilence(r.Context(), id); err != nil {
			s.writeAlertingError(w, r, err, "invalid alert rule")
			return
		}
		rule, err := s.alerts.GetAlertRule(r.Context(), id)
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, rule)
	default:
		methodNotAllowed(w, "POST, DELETE")
	}
}

func (s *Server) handleAlertRuleEvents(w http.ResponseWriter, r *http.Request, id string) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, "GET")
		return
	}
	if _, err := s.alerts.GetAlertRule(r.Context(), id); errors.Is(err, sql.ErrNoRows) {
		writeErr(w, http.StatusNotFound, "alert rule not found")
		return
	} else if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	filter, ok := parseAlertEventFilter(w, r)
	if !ok {
		return
	}
	filter.RuleID = id
	s.writeAlertEventList(w, r, filter)
}

// handleAlertEvents 处理 /api/monitoring/alerts/events。
func (s *Server) handleAlertEvents(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if !s.alertingAvailable(w) {
		return
	}
	if r.Method != http.MethodGet {
		methodNotAllowed(w, "GET")
		return
	}
	filter, ok := parseAlertEventFilter(w, r)
	if !ok {
		return
	}
	s.writeAlertEventList(w, r, filter)
}

func (s *Server) writeAlertEventList(w http.ResponseWriter, r *http.Request, filter monitoring.AlertEventFilter) {
	events, total, err := s.alerts.ListAlertEvents(r.Context(), filter)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	limit := filter.Limit
	if limit <= 0 {
		limit = 50
	}
	if limit > 200 {
		limit = 200
	}
	offset := filter.Offset
	if offset < 0 {
		offset = 0
	}
	writeJSON(w, http.StatusOK, alertEventListResponse{Events: events, Total: total, Limit: limit, Offset: offset})
}

// handleAlertEvent 处理 /api/monitoring/alerts/events/{id}[/ack]。
func (s *Server) handleAlertEvent(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if !s.alertingAvailable(w) {
		return
	}
	r = s.alertRequest(r)
	id, rest := splitResourcePath(r.URL.Path, "/api/monitoring/alerts/events/")
	if id == "" {
		writeErr(w, http.StatusNotFound, "alert event not found")
		return
	}
	if len(rest) > 0 {
		if len(rest) != 1 || rest[0] != "ack" {
			writeErr(w, http.StatusNotFound, "alert event not found")
			return
		}
		if r.Method != http.MethodPost {
			methodNotAllowed(w, "POST")
			return
		}
		user := currentUser(r.Context())
		userID, username := "", ""
		if user != nil {
			userID, username = user.UserID, user.Username
		}
		if err := s.alerts.AckAlertEvent(r.Context(), id, userID, username, time.Now().UTC()); err != nil {
			s.writeAlertingError(w, r, err, "invalid alert event")
			return
		}
		event, err := s.alerts.GetAlertEvent(r.Context(), id)
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, event)
		return
	}
	if r.Method != http.MethodGet {
		methodNotAllowed(w, "GET")
		return
	}
	event, err := s.alerts.GetAlertEvent(r.Context(), id)
	if errors.Is(err, sql.ErrNoRows) {
		writeErr(w, http.StatusNotFound, "alert event not found")
		return
	}
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, event)
}

type alertNotificationListResponse struct {
	Notifications []monitoring.AlertNotificationRecord `json:"notifications"`
	Total         int                                  `json:"total"`
	Limit         int                                  `json:"limit"`
	Offset        int                                  `json:"offset"`
}

// handleAlertNotifications 处理 /api/monitoring/alerts/notifications。
// 只返回发件箱与尝试记录，不返回渠道配置、URL、token 或 secret。
func (s *Server) handleAlertNotifications(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if !s.alertingAvailable(w) {
		return
	}
	if r.Method != http.MethodGet {
		methodNotAllowed(w, "GET")
		return
	}
	filter, ok := parseAlertNotificationFilter(w, r)
	if !ok {
		return
	}
	if filter.EventID != "" {
		if _, err := s.alerts.GetAlertEvent(r.Context(), filter.EventID); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "alert event not found")
			return
		} else if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
	}
	if filter.ChannelID != "" {
		if _, err := s.alerts.GetChannel(r.Context(), filter.ChannelID); errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "notification channel not found")
			return
		} else if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
	}
	records, total, err := s.alerts.ListNotifications(r.Context(), filter)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	limit := filter.Limit
	if limit <= 0 {
		limit = 50
	}
	if limit > 200 {
		limit = 200
	}
	offset := filter.Offset
	if offset < 0 {
		offset = 0
	}
	writeJSON(w, http.StatusOK, alertNotificationListResponse{
		Notifications: records,
		Total:         total,
		Limit:         limit,
		Offset:        offset,
	})
}

func parseAlertNotificationFilter(w http.ResponseWriter, r *http.Request) (monitoring.AlertNotificationFilter, bool) {
	values, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		writeErr(w, http.StatusBadRequest, "invalid query parameter")
		return monitoring.AlertNotificationFilter{}, false
	}
	allowed := map[string]bool{
		"event_id": true, "channel_id": true, "status": true, "limit": true, "offset": true,
	}
	for key := range values {
		if !allowed[key] {
			writeErr(w, http.StatusBadRequest, "unknown query parameter")
			return monitoring.AlertNotificationFilter{}, false
		}
	}
	filter := monitoring.AlertNotificationFilter{
		EventID:   values.Get("event_id"),
		ChannelID: values.Get("channel_id"),
		Status:    values.Get("status"),
		Limit:     50,
	}
	if !validAlertIdentifier(filter.EventID) || !validAlertIdentifier(filter.ChannelID) {
		writeErr(w, http.StatusBadRequest, "invalid notification filter")
		return filter, false
	}
	if filter.Status != "" && !monitoring.ValidNotificationStatus(filter.Status) {
		writeErr(w, http.StatusBadRequest, "invalid notification status")
		return filter, false
	}
	if raw := values.Get("limit"); raw != "" {
		limit, err := strconv.Atoi(raw)
		if err != nil || limit < 1 || limit > 200 {
			writeErr(w, http.StatusBadRequest, "invalid limit")
			return filter, false
		}
		filter.Limit = limit
	}
	if raw := values.Get("offset"); raw != "" {
		offset, err := strconv.Atoi(raw)
		if err != nil || offset < 0 {
			writeErr(w, http.StatusBadRequest, "invalid offset")
			return filter, false
		}
		filter.Offset = offset
	}
	return filter, true
}

func validAlertIdentifier(value string) bool {
	if len(value) > 128 {
		return false
	}
	for index := 0; index < len(value); index++ {
		if value[index] < 0x20 || value[index] == 0x7f {
			return false
		}
	}
	return true
}

func parseAlertEventFilter(w http.ResponseWriter, r *http.Request) (monitoring.AlertEventFilter, bool) {
	values := r.URL.Query()
	filter := monitoring.AlertEventFilter{
		State:    values.Get("state"),
		Status:   values.Get("status"),
		RuleID:   values.Get("rule_id"),
		NodeID:   values.Get("node_id"),
		Severity: values.Get("severity"),
		Limit:    50,
	}
	switch filter.State {
	case "", "active":
		filter.State = "active"
	case "history", "all":
	default:
		writeErr(w, http.StatusBadRequest, "invalid alert event state")
		return filter, false
	}
	if filter.Status != "" {
		switch filter.Status {
		case monitoring.AlertStatusPending, monitoring.AlertStatusFiring,
			monitoring.AlertStatusResolved, monitoring.AlertStatusSilenced,
			monitoring.AlertStatusUnknown, monitoring.AlertStatusOK:
		default:
			writeErr(w, http.StatusBadRequest, "invalid alert event status")
			return filter, false
		}
	}
	if filter.Severity != "" && !monitoring.ValidAlertSeverity(filter.Severity) {
		writeErr(w, http.StatusBadRequest, "invalid alert severity")
		return filter, false
	}
	if raw := values.Get("limit"); raw != "" {
		limit, err := strconv.Atoi(raw)
		if err != nil || limit < 1 || limit > 200 {
			writeErr(w, http.StatusBadRequest, "invalid limit")
			return filter, false
		}
		filter.Limit = limit
	}
	if raw := values.Get("offset"); raw != "" {
		offset, err := strconv.Atoi(raw)
		if err != nil || offset < 0 {
			writeErr(w, http.StatusBadRequest, "invalid offset")
			return filter, false
		}
		filter.Offset = offset
	}
	if raw := values.Get("since"); raw != "" {
		parsed, _, err := parseNativeTimeParam(raw)
		if err != nil {
			writeErr(w, http.StatusBadRequest, "invalid since")
			return filter, false
		}
		filter.Since = parsed
	}
	if raw := values.Get("until"); raw != "" {
		parsed, _, err := parseNativeTimeParam(raw)
		if err != nil {
			writeErr(w, http.StatusBadRequest, "invalid until")
			return filter, false
		}
		filter.Until = parsed
	}
	if !filter.Since.IsZero() && !filter.Until.IsZero() && filter.Since.After(filter.Until) {
		writeErr(w, http.StatusBadRequest, "invalid time range")
		return filter, false
	}
	return filter, true
}

// handleNotificationChannels 处理 /api/monitoring/notification-channels。
func (s *Server) handleNotificationChannels(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if !s.alertingAvailable(w) {
		return
	}
	r = s.alertRequest(r)
	switch r.Method {
	case http.MethodGet:
		channels, err := s.alerts.ListChannels(r.Context())
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, channels)
	case http.MethodPost:
		var channel monitoring.NotificationChannel
		if err := readJSON(r, &channel); err != nil || channel.ID != "" {
			writeErr(w, http.StatusBadRequest, "invalid notification channel")
			return
		}
		if err := channel.Validate(); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid notification channel")
			return
		}
		if err := s.alerts.CreateChannel(r.Context(), &channel); err != nil {
			s.writeAlertingError(w, r, err, "invalid notification channel")
			return
		}
		writeJSON(w, http.StatusCreated, channel)
	default:
		methodNotAllowed(w, "GET, POST")
	}
}

// handleNotificationChannel 处理 /api/monitoring/notification-channels/{id}[/test]。
func (s *Server) handleNotificationChannel(w http.ResponseWriter, r *http.Request) {
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if !s.alertingAvailable(w) {
		return
	}
	r = s.alertRequest(r)
	id, rest := splitResourcePath(r.URL.Path, "/api/monitoring/notification-channels/")
	if id == "" {
		writeErr(w, http.StatusNotFound, "notification channel not found")
		return
	}
	if len(rest) > 0 {
		if len(rest) != 1 || rest[0] != "test" {
			writeErr(w, http.StatusNotFound, "notification channel not found")
			return
		}
		if r.Method != http.MethodPost {
			methodNotAllowed(w, "POST")
			return
		}
		channel, err := s.alerts.GetChannel(r.Context(), id)
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "notification channel not found")
			return
		}
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		if s.alertNotifier == nil {
			writeErr(w, http.StatusServiceUnavailable, "alerting unavailable")
			return
		}
		if err := s.alertNotifier.SendTest(r.Context(), channel); err != nil {
			s.audit(r, "notification_channel", id, "test")
			// 错误已由 notifier 归一化；不返回 URL、token 或 SMTP 细节。
			if errors.Is(err, monitoring.ErrNotificationConfig) {
				writeErr(w, http.StatusBadRequest, "invalid notification channel configuration")
				return
			}
			writeErr(w, http.StatusBadGateway, "notification delivery failed")
			return
		}
		s.audit(r, "notification_channel", id, "test")
		writeJSON(w, http.StatusOK, map[string]string{"ok": "true"})
		return
	}
	switch r.Method {
	case http.MethodGet:
		channel, err := s.alerts.GetChannel(r.Context(), id)
		if errors.Is(err, sql.ErrNoRows) {
			writeErr(w, http.StatusNotFound, "notification channel not found")
			return
		}
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, channel)
	case http.MethodPut:
		var channel monitoring.NotificationChannel
		if err := readJSON(r, &channel); err != nil || (channel.ID != "" && channel.ID != id) {
			writeErr(w, http.StatusBadRequest, "invalid notification channel")
			return
		}
		channel.ID = id
		if err := channel.Validate(); err != nil {
			writeErr(w, http.StatusBadRequest, "invalid notification channel")
			return
		}
		if err := s.alerts.UpdateChannel(r.Context(), channel); err != nil {
			s.writeAlertingError(w, r, err, "invalid notification channel")
			return
		}
		updated, err := s.alerts.GetChannel(r.Context(), id)
		if err != nil {
			s.writeInternalError(w, r, err)
			return
		}
		writeJSON(w, http.StatusOK, updated)
	case http.MethodDelete:
		if err := s.alerts.DeleteChannel(r.Context(), id); err != nil {
			s.writeAlertingError(w, r, err, "invalid notification channel")
			return
		}
		w.WriteHeader(http.StatusNoContent)
	default:
		methodNotAllowed(w, "GET, PUT, DELETE")
	}
}
