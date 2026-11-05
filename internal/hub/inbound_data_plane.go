package hub

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// DataPlaneAvailable 仅当拨号器确认 Agent 声明 inbound_data_plane 时为 true。
func (w *wsConn) DataPlaneAvailable() bool {
	return w != nil && w.dataPlane && w.dataPlaneClient != nil && w.dataPlaneBase != "" && len(w.dataPlaneToken) > 0
}

// DataPlaneRequest 通过入站 mTLS 连接发起数据面 HTTPS 请求。
// path 必须由 Hub 内部构造，调用方不得传入任意 URL；不回退明文、不跟随重定向。
func (w *wsConn) DataPlaneRequest(ctx context.Context, method, path string, body io.Reader, contentLength int64) (*http.Response, error) {
	if !w.DataPlaneAvailable() {
		return nil, ErrInboundDataPlaneUnavailable
	}
	if !strings.HasPrefix(path, "/agent-data/v1/") {
		return nil, fmt.Errorf("invalid inbound data plane path")
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(w.dataPlaneBase, "/")+path, body)
	if err != nil {
		return nil, err
	}
	req.ContentLength = contentLength
	req.Header.Set("Authorization", "Bearer "+base64.RawURLEncoding.EncodeToString(w.dataPlaneToken))
	req.Header.Set("X-NodeSteer-Session", w.dataPlaneSessionID)
	req.Header.Set("X-NodeSteer-Node", w.NodeID())
	if body != nil {
		req.Header.Set("Content-Type", "application/octet-stream")
	}
	client := *w.dataPlaneClient
	client.CheckRedirect = func(*http.Request, []*http.Request) error {
		return errors.New("inbound data plane redirects are not allowed")
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	return resp, nil
}
