package hub

// OBS-20261018-SCALE-02：心跳超时清理会话时，在途写失败（use of closed network
// connection）此前记 ERROR "agent websocket write failed"，造成「无 ERROR 日志」
// 巡检假阳性。修复后连接已关闭/对端消失类错误降级 WARN，真实写失败保持 ERROR。

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestIsConnectionGoneClassification(t *testing.T) {
	cases := []struct {
		name string
		err  error
		gone bool
	}{
		{"nil", nil, false},
		{"net.ErrClosed", net.ErrClosed, true},
		{"wrapped net.ErrClosed", fmt.Errorf("failed to write frame: %w", net.ErrClosed), true},
		{"closed network connection text", errors.New("write tcp 192.0.2.1:5678->192.0.2.2:9: use of closed network connection"), true},
		{"io.EOF", io.EOF, true},
		{"wrapped io.EOF", fmt.Errorf("read: %w", io.EOF), true},
		{"context.Canceled", context.Canceled, true},
		{"connection reset by peer", errors.New("read tcp: connection reset by peer"), true},
		{"broken pipe", errors.New("write tcp: broken pipe"), true},
		{"io.ErrUnexpectedEOF（真实截断，不得降级）", io.ErrUnexpectedEOF, false},
		{"real write failure", errors.New("websocket: write limit exceeded"), false},
		{"storage error", errors.New("database is locked"), false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := IsConnectionGone(tc.err); got != tc.gone {
				t.Fatalf("IsConnectionGone(%v) = %v, want %v", tc.err, got, tc.gone)
			}
		})
	}
}

// newLogCaptureGateway 构造带日志缓冲的 Gateway 与真实 WebSocket 服务端连接。
func newLogCaptureGateway(t *testing.T) (*Gateway, *bytes.Buffer) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { st.Close() })
	revisions := NewRevisionManager(st)
	nm := NewNodeManager(st, revisions)
	sm := NewSessionManager()
	logs := &bytes.Buffer{}
	logger := slog.New(slog.NewTextHandler(logs, nil))
	g := NewGateway(GatewayConfig{RegistrationToken: "test-token"}, nm, sm, NewSyncManager(st, revisions, sm, nm), nil, logger)
	return g, logs
}

// TestWriteLoopClosedConnectionLogsWarnNotError 心跳超时清理语义：
// 连接先被关闭、写循环再处理在途消息 → WARN，不得出现 ERROR。
func TestWriteLoopClosedConnectionLogsWarnNotError(t *testing.T) {
	g, logs := newLogCaptureGateway(t)
	conn := newHelloTestConn(t, g)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go conn.writeLoop(ctx)

	// 模拟心跳超时清理：会话侧关闭底层连接后，仍在途的消息进入写循环。
	_ = conn.c.CloseNow()
	if err := conn.send(protocol.Envelope{Type: "TEST_WRITE"}); err != nil {
		t.Fatalf("send after close = %v, want buffered success", err)
	}

	// 写循环必须先落日志再 markUnavailable（close(done) 建立 happens-before），
	// 因此等到 done 关闭后再读日志缓冲区，避免测试侧数据竞争。
	select {
	case <-conn.done:
	case <-time.After(3 * time.Second):
		t.Fatal("writeLoop did not terminate after closed-connection write")
	}
	got := logs.String()
	if strings.Contains(got, "level=ERROR") {
		t.Fatalf("closed connection write logged as ERROR (SCALE-02):\n%s", got)
	}
	if !strings.Contains(got, "level=WARN") || !strings.Contains(got, "write aborted") {
		t.Fatalf("expected WARN log for closed connection write:\n%s", got)
	}
}

// TestLogWriteFailureGenuineErrorStaysError 真实写失败保持 ERROR 语义（不整体降级）。
func TestLogWriteFailureGenuineErrorStaysError(t *testing.T) {
	g, logs := newLogCaptureGateway(t)
	conn := newHelloTestConn(t, g)

	conn.logWriteFailure("TEST_WRITE", errors.New("websocket: write limit exceeded"))
	if !strings.Contains(logs.String(), "level=ERROR") || !strings.Contains(logs.String(), "agent websocket write failed") {
		t.Fatalf("genuine write failure must stay ERROR:\n%s", logs.String())
	}

	logs.Reset()
	conn.logWriteFailure("TEST_WRITE", net.ErrClosed)
	if strings.Contains(logs.String(), "level=ERROR") {
		t.Fatalf("closed-connection write must not be ERROR:\n%s", logs.String())
	}
	if !strings.Contains(logs.String(), "level=WARN") {
		t.Fatalf("closed-connection write must be WARN:\n%s", logs.String())
	}
}
