package api

import (
	"net/http"
	"strings"
	"time"
)

// slowAPIThreshold 是慢 API 日志阈值；只记录路径，不记录 query 或响应体。
const slowAPIThreshold = 500 * time.Millisecond

type statusRecorder struct {
	http.ResponseWriter
	status int
	bytes  int
}

func (w *statusRecorder) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *statusRecorder) Write(data []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	n, err := w.ResponseWriter.Write(data)
	w.bytes += n
	return n, err
}

func (w *statusRecorder) Flush() {
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (w *statusRecorder) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}

// withRequestInstrumentation 记录慢 API 与总量指标。放在最外层，
// 统计到的是压缩后的实际写出耗时。
func (s *Server) withRequestInstrumentation(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/") {
			next.ServeHTTP(w, r)
			return
		}
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w}
		next.ServeHTTP(rec, r)
		elapsed := time.Since(start)
		slow := elapsed >= slowAPIThreshold
		if s.metrics != nil {
			s.metrics.AddAPIRequest(slow)
		}
		if slow && s.logger != nil {
			status := rec.status
			if status == 0 {
				status = http.StatusOK
			}
			s.logger.Warn("slow api request",
				"method", r.Method,
				"path", r.URL.Path,
				"status", status,
				"bytes", rec.bytes,
				"duration", elapsed)
		}
	})
}
