package hub

import (
	"context"
	"errors"
	"io"
	"net"
	"strings"
)

// IsConnectionGone 判定错误是否属于「连接已关闭 / 对端消失」的正常清理场景：
// 心跳超时清理关闭连接后仍在途的写、对端断开、客户端取消等。这类错误不是服务端
// 故障，调用方应将日志降级（WARN/DEBUG），只有真实错误才保持 ERROR 语义。
//
// 与 F8（context.Canceled → Debug）同族的判定集中于此：Gateway 写循环与 API
// 错误归一化共用，避免各自硬编码错误字符串（缺陷 OBS-20261018-SCALE-02：
// 心跳超时清理会话时，在途写以 ERROR "agent websocket write failed" 输出，
// 造成「无 ERROR 日志」巡检假阳性）。
func IsConnectionGone(err error) bool {
	if err == nil {
		return false
	}
	// net.ErrClosed 覆盖 "use of closed network connection"；io.EOF 覆盖对端正常关闭；
	// context.Canceled 覆盖请求上下文取消（客户端断开、连接回收）。
	if errors.Is(err, net.ErrClosed) || errors.Is(err, io.EOF) || errors.Is(err, context.Canceled) {
		return true
	}
	// 驱动/系统层文本兜底（部分平台包装错误不含 sentinel）。
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "use of closed network connection") ||
		strings.Contains(msg, "connection reset by peer") ||
		strings.Contains(msg, "broken pipe")
}
