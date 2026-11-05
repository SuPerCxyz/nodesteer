// Package inbound 提供 Hub 主动拨号所需的 endpoint 规范化、SSRF/CIDR 策略
// 与 PKI 基础能力。入站 listener、拨号器和数据面不在此阶段实现。
package inbound

import (
	"fmt"
	"net"
	"strconv"
	"strings"
)

// DefaultPort 入站 endpoint 的默认端口。
const DefaultPort = 18444

// Endpoint 规范化后的拨号目标。
type Endpoint struct {
	Host string
	Port int
}

// String 返回 host:port；IPv6 自动加方括号。
func (e Endpoint) String() string {
	return net.JoinHostPort(e.Host, strconv.Itoa(e.Port))
}

// ParseEndpoint 解析并规范化 endpoint：接受 host、host:port、[ipv6]、[ipv6]:port。
//
// 拒绝 scheme、path、query、userinfo、空白和 IPv6 zone；缺省端口补 18444。
// 该函数只做语法与端口范围校验，DNS 解析和 CIDR 过滤由 DialPolicy 在拨号阶段执行。
func ParseEndpoint(raw string) (Endpoint, error) {
	value := strings.TrimSpace(raw)
	if value == "" {
		return Endpoint{}, fmt.Errorf("endpoint is required")
	}
	if value != raw {
		return Endpoint{}, fmt.Errorf("endpoint must not contain leading or trailing whitespace")
	}
	if strings.ContainsAny(value, " \t\r\n") {
		return Endpoint{}, fmt.Errorf("endpoint must not contain whitespace")
	}
	if strings.Contains(value, "://") {
		return Endpoint{}, fmt.Errorf("endpoint must not include a scheme")
	}
	if strings.ContainsAny(value, "/?#@") {
		return Endpoint{}, fmt.Errorf("endpoint must contain only a host and optional port")
	}

	var host, portText string
	if strings.HasPrefix(value, "[") {
		end := strings.IndexByte(value, ']')
		if end < 0 {
			return Endpoint{}, fmt.Errorf("endpoint has an unterminated IPv6 address")
		}
		host = value[1:end]
		rest := value[end+1:]
		if rest != "" {
			if !strings.HasPrefix(rest, ":") {
				return Endpoint{}, fmt.Errorf("endpoint has an invalid port")
			}
			portText = rest[1:]
			if portText == "" {
				return Endpoint{}, fmt.Errorf("endpoint has an empty port")
			}
		}
		ip := net.ParseIP(host)
		if ip == nil || ip.To4() != nil {
			return Endpoint{}, fmt.Errorf("brackets are only allowed for IPv6 addresses")
		}
		host = ip.String()
	} else {
		switch strings.Count(value, ":") {
		case 0:
			host = value
		case 1:
			idx := strings.LastIndexByte(value, ':')
			host, portText = value[:idx], value[idx+1:]
			if portText == "" {
				return Endpoint{}, fmt.Errorf("endpoint has an empty port")
			}
		default:
			return Endpoint{}, fmt.Errorf("IPv6 endpoint must be enclosed in brackets")
		}
		if host == "" {
			return Endpoint{}, fmt.Errorf("endpoint host is required")
		}
		if ip := net.ParseIP(host); ip != nil {
			if ip.To4() == nil {
				return Endpoint{}, fmt.Errorf("IPv6 endpoint must be enclosed in brackets")
			}
			host = ip.String()
		} else {
			normalized, err := normalizeHostname(host)
			if err != nil {
				return Endpoint{}, err
			}
			host = normalized
		}
	}

	port := DefaultPort
	if portText != "" {
		parsed, err := strconv.Atoi(portText)
		if err != nil || parsed < 1 || parsed > 65535 {
			return Endpoint{}, fmt.Errorf("endpoint port must be between 1 and 65535")
		}
		port = parsed
	}
	return Endpoint{Host: host, Port: port}, nil
}

// normalizeHostname 校验 DNS 名称并统一为小写、去掉末尾根点。
func normalizeHostname(value string) (string, error) {
	name := strings.TrimSuffix(value, ".")
	if name == "" || len(name) > 253 {
		return "", fmt.Errorf("endpoint host must be a valid IP address or DNS name")
	}
	for _, label := range strings.Split(name, ".") {
		if len(label) == 0 || len(label) > 63 {
			return "", fmt.Errorf("endpoint host must be a valid IP address or DNS name")
		}
		if !isHostnameLetterOrDigit(label[0]) || !isHostnameLetterOrDigit(label[len(label)-1]) {
			return "", fmt.Errorf("endpoint host must be a valid IP address or DNS name")
		}
		for i := 1; i < len(label)-1; i++ {
			if !isHostnameLetterOrDigit(label[i]) && label[i] != '-' {
				return "", fmt.Errorf("endpoint host must be a valid IP address or DNS name")
			}
		}
	}
	return strings.ToLower(name), nil
}

func isHostnameLetterOrDigit(value byte) bool {
	return (value >= 'a' && value <= 'z') ||
		(value >= 'A' && value <= 'Z') ||
		(value >= '0' && value <= '9')
}

// NormalizeEndpoint 使用默认端口策略规范化 endpoint。
func NormalizeEndpoint(raw string) (string, error) {
	return DefaultDialPolicy().NormalizeEndpoint(raw)
}
