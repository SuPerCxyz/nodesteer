package api

import (
	"compress/gzip"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAPIJSONGzip(t *testing.T) {
	s, _ := newMeTestServer(t)
	handler := s.Routes()

	req := httptest.NewRequest(http.MethodGet, "/api/auth-mode", nil)
	req.Header.Set("Accept-Encoding", "gzip")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	if got := rec.Header().Get("Content-Encoding"); got != "gzip" {
		t.Fatalf("content-encoding = %q", got)
	}
	if !strings.Contains(rec.Header().Get("Vary"), "Accept-Encoding") {
		t.Fatalf("vary = %q", rec.Header().Get("Vary"))
	}
	reader, err := gzip.NewReader(rec.Body)
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(reader)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), "allow_empty_password") {
		t.Fatalf("decompressed body = %q", body)
	}

	req = httptest.NewRequest(http.MethodGet, "/api/auth-mode", nil)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if got := rec.Header().Get("Content-Encoding"); got != "" {
		t.Fatalf("unexpected encoding without Accept-Encoding: %q", got)
	}
	if !strings.Contains(rec.Body.String(), "allow_empty_password") {
		t.Fatalf("plain body = %q", rec.Body.String())
	}

	req = httptest.NewRequest(http.MethodGet, "/api/auth-mode", nil)
	req.Header.Set("Accept-Encoding", "gzip;q=0")
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if got := rec.Header().Get("Content-Encoding"); got != "" {
		t.Fatalf("q=0 must not be compressed: %q", got)
	}
}

func TestAPIBinaryNotCompressed(t *testing.T) {
	s, _ := newMeTestServer(t)
	binary := []byte{0x00, 0x01, 0x02, 0x03, 0xff}
	s.SetAgentBinaryPayloads(map[string][]byte{"amd64": binary})
	handler := s.Routes()

	req := httptest.NewRequest(http.MethodGet, "/api/agent/binary?architecture=amd64", nil)
	req.Header.Set("Accept-Encoding", "gzip")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	if got := rec.Header().Get("Content-Encoding"); got != "" {
		t.Fatalf("binary content-encoding = %q", got)
	}
	if !strings.HasPrefix(rec.Header().Get("Content-Type"), "application/octet-stream") {
		t.Fatalf("content-type = %q", rec.Header().Get("Content-Type"))
	}
	if got := rec.Body.Bytes(); len(got) != len(binary) {
		t.Fatalf("binary body = %v", got)
	}
}
