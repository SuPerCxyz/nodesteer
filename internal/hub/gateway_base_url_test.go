package hub

import "testing"

// TestResolveGatewayBaseURLFollowsGatewayPort 派生断言：
// ResolveGatewayBaseURL 的端口（含 fallback）必须跟随 gateway_addr，
// 不再硬编码 8443 —— 单端口模式（gateway 与 web 同端口）依赖该行为。
func TestResolveGatewayBaseURLFollowsGatewayPort(t *testing.T) {
	cases := []struct {
		name        string
		configured  string
		webBaseURL  string
		gatewayAddr string
		want        string
	}{
		{
			name: "主路径端口取自 gateway_addr（契约派生断言）",
			// 端口 = 8080（来自 gatewayAddr），host = example.com（来自 webBaseURL）
			webBaseURL:  "http://example.com:8080",
			gatewayAddr: "127.0.0.1:8080",
			want:        "http://example.com:8080",
		},
		{
			name:        "单端口模式 gateway_addr 无 host 也取其端口",
			webBaseURL:  "http://example.com:8080",
			gatewayAddr: ":8080",
			want:        "http://example.com:8080",
		},
		{
			name:        "webBaseURL 无效时 fallback 跟随 gateway 端口（不再硬编码 8443）",
			webBaseURL:  "",
			gatewayAddr: "127.0.0.1:9090",
			want:        "http://localhost:9090",
		},
		{
			name:        "gateway_addr 缺失时 fallback 保持默认 8443",
			webBaseURL:  "",
			gatewayAddr: "",
			want:        "http://localhost:8443",
		},
		{
			name:        "显式 configured 优先于推导",
			configured:  "https://gw.example.com",
			webBaseURL:  "http://example.com:8080",
			gatewayAddr: ":8080",
			want:        "https://gw.example.com",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := ResolveGatewayBaseURL(tc.configured, tc.webBaseURL, tc.gatewayAddr)
			if got != tc.want {
				t.Fatalf("ResolveGatewayBaseURL(%q, %q, %q) = %q, want %q",
					tc.configured, tc.webBaseURL, tc.gatewayAddr, got, tc.want)
			}
		})
	}
}
