package hubserver

import (
	"compress/gzip"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"
	"time"
)

func staticTestFS() fstest.MapFS {
	now := time.Unix(1700000000, 0).UTC()
	return fstest.MapFS{
		"index.html":               {Data: []byte("<!doctype html><html><body>spa</body></html>"), ModTime: now},
		"assets/index-abcdef12.js": {Data: []byte(strings.Repeat("console.log('x');", 256)), ModTime: now},
		"assets/app.js.br":         {Data: []byte("pretend-brotli"), ModTime: now},
		"assets/app.js":            {Data: []byte(strings.Repeat("console.log('y');", 256)), ModTime: now},
		"assets/logo.png":          {Data: []byte{0x89, 0x50, 0x4e, 0x47}, ModTime: now},
	}
}

func TestStaticHandlerGzipImmutableAndETag(t *testing.T) {
	handler := newStaticHandler(staticTestFS())
	req := httptest.NewRequest(http.MethodGet, "/assets/index-abcdef12.js", nil)
	req.Header.Set("Accept-Encoding", "gzip")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	if got := rec.Header().Get("Content-Encoding"); got != "gzip" {
		t.Fatalf("content-encoding = %q", got)
	}
	if got := rec.Header().Get("Cache-Control"); got != staticImmutableAge {
		t.Fatalf("cache-control = %q", got)
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
	if !strings.Contains(string(body), "console.log('x')") {
		t.Fatalf("decompressed body missing content: %d bytes", len(body))
	}
	etag := rec.Header().Get("ETag")
	if etag == "" {
		t.Fatal("missing ETag")
	}

	req = httptest.NewRequest(http.MethodGet, "/assets/index-abcdef12.js", nil)
	req.Header.Set("Accept-Encoding", "gzip")
	req.Header.Set("If-None-Match", etag)
	rec = httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotModified {
		t.Fatalf("conditional status = %d, want 304", rec.Code)
	}
	if rec.Body.Len() != 0 {
		t.Fatalf("304 body = %d bytes", rec.Body.Len())
	}
}

func TestStaticHandlerHTMLNoCacheAndSPAFallback(t *testing.T) {
	handler := newStaticHandler(staticTestFS())
	for _, target := range []string{"/", "/dashboard"} {
		req := httptest.NewRequest(http.MethodGet, target, nil)
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("%s status = %d", target, rec.Code)
		}
		if got := rec.Header().Get("Cache-Control"); got != staticHTMLMaxAge {
			t.Fatalf("%s cache-control = %q", target, got)
		}
		if !strings.Contains(rec.Body.String(), "spa") {
			t.Fatalf("%s body = %q", target, rec.Body.String())
		}
	}
}

func TestStaticHandlerSkipsBinaryCompression(t *testing.T) {
	handler := newStaticHandler(staticTestFS())
	req := httptest.NewRequest(http.MethodGet, "/assets/logo.png", nil)
	req.Header.Set("Accept-Encoding", "gzip, br")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	if got := rec.Header().Get("Content-Encoding"); got != "" {
		t.Fatalf("binary content-encoding = %q", got)
	}
	if got := rec.Header().Get("Content-Type"); !strings.HasPrefix(got, "image/png") {
		t.Fatalf("content-type = %q", got)
	}
}

func TestStaticHandlerPrefersPrecompressedBrotli(t *testing.T) {
	handler := newStaticHandler(staticTestFS())
	req := httptest.NewRequest(http.MethodGet, "/assets/app.js", nil)
	req.Header.Set("Accept-Encoding", "br, gzip")
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if got := rec.Header().Get("Content-Encoding"); got != "br" {
		t.Fatalf("content-encoding = %q, want br", got)
	}
	if rec.Body.String() != "pretend-brotli" {
		t.Fatalf("body = %q", rec.Body.String())
	}
}

func TestStaticHandlerLastModified(t *testing.T) {
	handler := newStaticHandler(staticTestFS())
	req := httptest.NewRequest(http.MethodGet, "/index.html", nil)
	req.Header.Set("If-Modified-Since", time.Unix(1700000001, 0).UTC().Format(http.TimeFormat))
	rec := httptest.NewRecorder()
	handler.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotModified {
		t.Fatalf("status = %d, want 304", rec.Code)
	}
}
