package inbound

import (
	"fmt"
	"net"
	"strings"
)

// defaultAllowedPorts 未显式配置时只允许入站默认端口，避免 Hub 被当作任意端口扫描器。
var defaultAllowedPorts = []int{DefaultPort}

// defaultDeniedCIDRs SSRF/扫描防护的默认拒绝网段；RFC1918 不在此列，
// 内网 Hub/Agent 场景需要允许。
var defaultDeniedCIDRs = []string{
	"0.0.0.0/8",
	"169.254.0.0/16",
	"224.0.0.0/4",
	"240.0.0.0/4",
	"::/128",
	"fe80::/10",
	"ff00::/8",
}

// DialPolicy endpoint 端口白名单与拨号目标地址过滤策略。
type DialPolicy struct {
	allowedPorts  map[int]struct{}
	denied        []*net.IPNet
	allowLoopback bool
}

// DefaultDialPolicy 返回默认策略：仅允许 18444，拒绝 loopback/link-local/
// metadata/multicast/unspecified，保留 RFC1918。
func DefaultDialPolicy() *DialPolicy {
	p, err := NewDialPolicy(nil, nil, false)
	if err != nil {
		panic(err)
	}
	return p
}

// NewDialPolicy 构造拨号策略。
//
// allowedPorts 为空时使用 [18444]；deniedCIDRs 会追加到默认拒绝列表；
// allowLoopback 仅解除 loopback 拒绝，link-local/metadata/multicast 始终拒绝。
func NewDialPolicy(allowedPorts []int, deniedCIDRs []string, allowLoopback bool) (*DialPolicy, error) {
	if len(allowedPorts) == 0 {
		allowedPorts = defaultAllowedPorts
	}
	ports := make(map[int]struct{}, len(allowedPorts))
	for _, port := range allowedPorts {
		if port < 1 || port > 65535 {
			return nil, fmt.Errorf("inbound allowed port %d must be between 1 and 65535", port)
		}
		ports[port] = struct{}{}
	}

	cidrs := append([]string{}, defaultDeniedCIDRs...)
	if !allowLoopback {
		cidrs = append(cidrs, "127.0.0.0/8", "::1/128")
	}
	cidrs = append(cidrs, deniedCIDRs...)
	denied := make([]*net.IPNet, 0, len(cidrs))
	for _, raw := range cidrs {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			continue
		}
		_, network, err := net.ParseCIDR(raw)
		if err != nil {
			return nil, fmt.Errorf("invalid inbound denied CIDR %q: %w", raw, err)
		}
		denied = append(denied, network)
	}
	return &DialPolicy{allowedPorts: ports, denied: denied, allowLoopback: allowLoopback}, nil
}

// AllowsPort 判断端口是否在拨号白名单内。
func (p *DialPolicy) AllowsPort(port int) bool {
	if p == nil {
		p = DefaultDialPolicy()
	}
	_, ok := p.allowedPorts[port]
	return ok
}

// AllowedPorts 返回拨号端口白名单副本（升序由调用方按需排序）。
func (p *DialPolicy) AllowedPorts() []int {
	if p == nil {
		p = DefaultDialPolicy()
	}
	out := make([]int, 0, len(p.allowedPorts))
	for port := range p.allowedPorts {
		out = append(out, port)
	}
	return out
}

// NormalizeEndpoint 解析 endpoint 并校验端口白名单。
func (p *DialPolicy) NormalizeEndpoint(raw string) (string, error) {
	endpoint, err := ParseEndpoint(raw)
	if err != nil {
		return "", err
	}
	if !p.AllowsPort(endpoint.Port) {
		return "", fmt.Errorf("endpoint port %d is not allowed for inbound connections", endpoint.Port)
	}
	return endpoint.String(), nil
}

// AllowsIP 判断解析后的目标 IP 是否允许拨号。
func (p *DialPolicy) AllowsIP(ip net.IP) bool {
	if p == nil {
		p = DefaultDialPolicy()
	}
	if ip == nil {
		return false
	}
	if ip.IsUnspecified() || ip.IsMulticast() || ip.IsLinkLocalUnicast() {
		return false
	}
	if ip.IsLoopback() && !p.allowLoopback {
		return false
	}
	for _, network := range p.denied {
		if network.Contains(ip) {
			return false
		}
	}
	return true
}

// FilterIPs 返回允许拨号的解析结果；全部被拒绝时返回空切片。
func (p *DialPolicy) FilterIPs(ips []net.IP) []net.IP {
	out := make([]net.IP, 0, len(ips))
	for _, ip := range ips {
		if p.AllowsIP(ip) {
			out = append(out, ip)
		}
	}
	return out
}
