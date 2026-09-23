package api

import "testing"

func TestValidNodeAddress(t *testing.T) {
	tests := []struct {
		name    string
		address string
		valid   bool
	}{
		{name: "ipv4", address: "192.0.2.10", valid: true},
		{name: "ipv6", address: "2001:db8::10", valid: true},
		{name: "hostname", address: "agent.example.com", valid: true},
		{name: "single label hostname", address: "node-01", valid: true},
		{name: "trailing dot hostname", address: "agent.example.com.", valid: true},
		{name: "empty", address: "", valid: false},
		{name: "scheme", address: "https://agent.example.com", valid: false},
		{name: "port", address: "agent.example.com:8443", valid: false},
		{name: "invalid label", address: "-agent.example.com", valid: false},
		{name: "underscore", address: "agent_node.example.com", valid: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := validNodeAddress(test.address); got != test.valid {
				t.Fatalf("validNodeAddress(%q) = %v, want %v", test.address, got, test.valid)
			}
		})
	}
}

// TestConfiguredGatewayAddr 锁定纳管地址派生的端口契约：
// 配置 URL 显式端口优先；隐式端口按 scheme 补全（https→:443、http→:80），
// 不得对隐式 443 的反代地址硬编码回退 :8443；解析失败或无 scheme 时兜底 :8443。
func TestConfiguredGatewayAddr(t *testing.T) {
	tests := []struct {
		name string
		base string
		want string
	}{
		{name: "https 隐式端口按 scheme 补 443", base: "https://x.y", want: ":443"},
		{name: "https 显式端口优先", base: "https://x.y:8443", want: ":8443"},
		{name: "http 隐式端口按 scheme 补 80", base: "http://x.y", want: ":80"},
		{name: "http 显式端口优先", base: "http://x.y:8080", want: ":8080"},
		{name: "空值兜底 8443", base: "", want: ":8443"},
		{name: "坏值兜底 8443", base: "://bad url", want: ":8443"},
		{name: "无 scheme 兜底 8443", base: "nodesteer.example.com", want: ":8443"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := configuredGatewayAddr(test.base); got != test.want {
				t.Fatalf("configuredGatewayAddr(%q) = %q, want %q", test.base, got, test.want)
			}
		})
	}
}
