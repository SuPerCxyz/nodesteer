package api

import (
	"database/sql"
	"errors"
	"net/http"
	"strconv"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func (s *Server) handleInboundBundle(w http.ResponseWriter, r *http.Request, id string, rest []string) {
	rotate := len(rest) == 1 && rest[0] == "rotate"
	if len(rest) > 1 || (len(rest) == 1 && !rotate) {
		writeErr(w, http.StatusNotFound, "node not found")
		return
	}
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if s.inboundBundle == nil {
		writeErrCode(w, http.StatusConflict, protocol.ErrorCodeInboundDisabled, "inbound bundle is disabled")
		return
	}
	if rotate {
		if r.Method != http.MethodPost {
			methodNotAllowed(w, "POST")
			return
		}
	} else if r.Method != http.MethodGet {
		methodNotAllowed(w, "GET")
		return
	}
	bundle, err := s.inboundBundle.Issue(r.Context(), id, rotate)
	if err != nil {
		s.writeInboundBundleError(w, r, err)
		return
	}
	if rotate {
		s.audit(r, "node", id, "inbound_bundle_rotate")
		if s.inboundDialer != nil {
			s.inboundDialer.Trigger(id)
		}
		w.Header().Set("Content-Type", "application/gzip")
		w.Header().Set("Content-Disposition", `attachment; filename="`+bundle.Filename+`"`)
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Length", strconv.Itoa(len(bundle.Archive)))
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(bundle.Archive)
		return
	}
	s.audit(r, "node", id, "inbound_bundle_issue")
	if s.inboundDialer != nil {
		s.inboundDialer.Trigger(id)
	}
	w.Header().Set("Content-Type", "application/gzip")
	w.Header().Set("Content-Disposition", `attachment; filename="`+bundle.Filename+`"`)
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Length", strconv.Itoa(len(bundle.Archive)))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(bundle.Archive)
}

func (s *Server) writeInboundBundleError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, sql.ErrNoRows):
		writeErr(w, http.StatusNotFound, "node not found")
	case errors.Is(err, hub.ErrInboundBundleDisabled):
		writeErrCode(w, http.StatusConflict, protocol.ErrorCodeInboundDisabled, "inbound bundle is disabled")
	case errors.Is(err, hub.ErrInboundBundleAlreadyIssued):
		writeErrCode(w, http.StatusConflict, protocol.ErrorCodeInboundBundleAlreadyIssued,
			"inbound bundle already issued; rotate explicitly")
	case errors.Is(err, hub.ErrInboundBundleEndpointMissing):
		writeErrCode(w, http.StatusBadRequest, protocol.ErrorCodeInboundEndpointInvalid,
			"node endpoint is required before issuing inbound bundle")
	default:
		s.writeInternalError(w, r, err)
	}
}

func (s *Server) handleNodeConnectionTest(w http.ResponseWriter, r *http.Request, id string, rest []string) {
	if len(rest) != 0 {
		writeErr(w, http.StatusNotFound, "node not found")
		return
	}
	if r.Method != http.MethodPost {
		methodNotAllowed(w, "POST")
		return
	}
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	if s.inboundDialer == nil {
		writeErrCode(w, http.StatusConflict, protocol.ErrorCodeInboundDisabled, "inbound connection is disabled")
		return
	}
	node, err := s.nodes.GetNode(r.Context(), id)
	if err != nil {
		writeErr(w, http.StatusNotFound, "node not found")
		return
	}
	result, err := s.inboundDialer.TestConnection(r.Context(), node)
	if err != nil {
		s.writeInboundTestError(w, r, err)
		return
	}
	s.auditDetail(r, "node", id, "connection_test", result.AuditSummary())
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) writeInboundTestError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, hub.ErrInboundTestRateLimited):
		writeErrCode(w, http.StatusTooManyRequests, "inbound_test_rate_limited", "connection test is rate limited; retry later")
	case errors.Is(err, hub.ErrInboundBundleDisabled):
		writeErrCode(w, http.StatusConflict, protocol.ErrorCodeInboundDisabled, "inbound connection is disabled")
	case errors.Is(err, hub.ErrInboundBundleEndpointMissing):
		writeErrCode(w, http.StatusBadRequest, protocol.ErrorCodeInboundEndpointInvalid, "node endpoint is required")
	case errors.Is(err, hub.ErrInboundNotProvisioned):
		writeErrCode(w, http.StatusConflict, protocol.ErrorCodeInboundNotProvisioned, "node inbound identity is not provisioned")
	default:
		var testErr *hub.InboundTestError
		if errors.As(err, &testErr) {
			writeErrCode(w, http.StatusBadGateway, testErr.Code, testErr.Error())
			return
		}
		s.writeInternalError(w, r, err)
	}
}
