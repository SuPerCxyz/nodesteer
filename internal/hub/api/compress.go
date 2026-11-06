package api

import (
	"compress/gzip"
	"net/http"
	"strings"
	"sync"
)

// gzipPool 复用压缩器，避免每次大 JSON 响应重新分配 32KiB 窗口。
var gzipPool = sync.Pool{
	New: func() any { return gzip.NewWriter(nil) },
}

// acceptsGzip 只做常见的 token 匹配；q=0 视为明确拒绝。
func acceptsGzip(r *http.Request) bool {
	return acceptsEncodingHeader(r.Header.Get("Accept-Encoding"), "gzip")
}

func acceptsEncodingHeader(header, encoding string) bool {
	for _, part := range strings.Split(header, ",") {
		fields := strings.Split(part, ";")
		token := strings.TrimSpace(fields[0])
		if !strings.EqualFold(token, encoding) && token != "*" {
			continue
		}
		rejected := false
		for _, param := range fields[1:] {
			keyValue := strings.SplitN(strings.TrimSpace(param), "=", 2)
			if len(keyValue) == 2 && strings.EqualFold(strings.TrimSpace(keyValue[0]), "q") &&
				strings.TrimSpace(keyValue[1]) == "0" {
				rejected = true
			}
		}
		if !rejected {
			return true
		}
	}
	return false
}

func gzipEligible(r *http.Request, status int, header http.Header) bool {
	if r.Method == http.MethodHead || !acceptsGzip(r) {
		return false
	}
	if status < http.StatusOK || status == http.StatusNoContent || status == http.StatusNotModified {
		return false
	}
	if header.Get("Content-Encoding") != "" {
		return false
	}
	contentType := strings.ToLower(header.Get("Content-Type"))
	return strings.HasPrefix(contentType, "application/json")
}

// gzipResponseWriter 在首次 WriteHeader 时按 Content-Type 决定是否压缩。
// 二进制下载、SSE、304/204 与已编码响应保持原样。
type gzipResponseWriter struct {
	http.ResponseWriter
	request     *http.Request
	writer      *gzip.Writer
	wroteHeader bool
}

func (w *gzipResponseWriter) WriteHeader(status int) {
	if w.wroteHeader {
		return
	}
	w.wroteHeader = true
	if gzipEligible(w.request, status, w.Header()) {
		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Del("Content-Length")
		gz := gzipPool.Get().(*gzip.Writer)
		gz.Reset(w.ResponseWriter)
		w.writer = gz
	}
	w.ResponseWriter.WriteHeader(status)
}

func (w *gzipResponseWriter) Write(data []byte) (int, error) {
	if !w.wroteHeader {
		w.WriteHeader(http.StatusOK)
	}
	if w.writer != nil {
		return w.writer.Write(data)
	}
	return w.ResponseWriter.Write(data)
}

func (w *gzipResponseWriter) Flush() {
	if w.writer != nil {
		_ = w.writer.Flush()
	}
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (w *gzipResponseWriter) close() {
	if w.writer == nil {
		return
	}
	_ = w.writer.Close()
	gzipPool.Put(w.writer)
	w.writer = nil
}

// Unwrap 让 http.ResponseController 能找到底层 writer。
func (w *gzipResponseWriter) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}

// withGzipJSON 包装 API 路由；Vary 对所有响应设置，Content-Encoding 只对 JSON。
func withGzipJSON(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		addVary(w.Header(), "Accept-Encoding")
		if !acceptsGzip(r) {
			next.ServeHTTP(w, r)
			return
		}
		gw := &gzipResponseWriter{ResponseWriter: w, request: r}
		defer gw.close()
		next.ServeHTTP(gw, r)
	})
}

func addVary(header http.Header, value string) {
	for _, existing := range header.Values("Vary") {
		for _, item := range strings.Split(existing, ",") {
			if strings.EqualFold(strings.TrimSpace(item), value) {
				return
			}
		}
	}
	header.Add("Vary", value)
}
