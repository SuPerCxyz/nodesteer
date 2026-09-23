package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"math/rand"
	"net/http"
	"runtime"
	"time"
)

// jitterDuration 在基础周期上加入 ±20% 随机抖动
func jitterDuration(base time.Duration) time.Duration {
	if base <= 0 {
		return base
	}
	delta := float64(base) * 0.2
	offset := time.Duration((rand.Float64()*2 - 1) * delta)
	return base + offset
}

// httpGet 下载
func httpGet(ctx context.Context, url string) (*http.Response, error) {
	return httpGetWithToken(ctx, url, "")
}

// httpGetWithToken 携带 Agent 身份令牌下载
func httpGetWithToken(ctx context.Context, url, token string) (*http.Response, error) {
	return httpGetWithAgentToken(ctx, url, token, "")
}

func httpGetWithAgentToken(ctx context.Context, url, token, agentID string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	if token != "" {
		req.Header.Set("X-NodeSteer-Agent-Token", token)
	}
	if agentID != "" {
		req.Header.Set("X-NodeSteer-Agent-ID", agentID)
	}
	return http.DefaultClient.Do(req)
}

// shaWriter 计算 SHA256
type shaWriter struct {
	h   io.Writer
	sum []byte
}

func (w *shaWriter) Write(p []byte) (int, error) { return w.h.Write(p) }

func newSHA256() io.Writer {
	return &shaWriter{h: sha256.New()}
}

func copyToMulti(dst io.Writer, src io.Reader, extra io.Writer) (int64, error) {
	return io.Copy(io.MultiWriter(dst, extra), src)
}

func hexSum(w io.Writer) string {
	if sw, ok := w.(*shaWriter); ok {
		return hex.EncodeToString(sw.sum)
	}
	return ""
}

// isArm 判断架构
func isArm() bool {
	return runtime.GOARCH == "arm64"
}
