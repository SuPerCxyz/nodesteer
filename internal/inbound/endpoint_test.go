package inbound

import (
	"net"
	"strings"
	"testing"
)

func TestParseEndpoint(t *testing.T) {
	cases := []struct {
		name    string
		raw     string
		want    string
		wantErr bool
	}{
		{name: "dns default port", raw: "agent.example.com", want: "agent.example.com:18444"},
		{name: "dns explicit port", raw: "agent.example.com:18444", want: "agent.example.com:18444"},
		{name: "dns uppercase and root dot", raw: "Agent.Example.COM.", want: "agent.example.com:18444"},
		{name: "ipv4", raw: "203.0.113.10", want: "203.0.113.10:18444"},
		{name: "ipv4 with port", raw: "203.0.113.10:18444", want: "203.0.113.10:18444"},
		{name: "ipv6", raw: "[2001:db8::10]", want: "[2001:db8::10]:18444"},
		{name: "ipv6 with port", raw: "[2001:db8::10]:18444", want: "[2001:db8::10]:18444"},
		{name: "scheme rejected", raw: "https://agent.example.com", wantErr: true},
		{name: "path rejected", raw: "agent.example.com/api", wantErr: true},
		{name: "query rejected", raw: "agent.example.com?x=1", wantErr: true},
		{name: "userinfo rejected", raw: "user@agent.example.com", wantErr: true},
		{name: "whitespace rejected", raw: " agent.example.com", wantErr: true},
		{name: "empty rejected", raw: "", wantErr: true},
		{name: "unbracketed ipv6 rejected", raw: "2001:db8::10", wantErr: true},
		{name: "empty port rejected", raw: "agent.example.com:", wantErr: true},
		{name: "port zero rejected", raw: "agent.example.com:0", wantErr: true},
		{name: "port too large rejected", raw: "agent.example.com:65536", wantErr: true},
		{name: "port not numeric rejected", raw: "agent.example.com:https", wantErr: true},
		{name: "ipv4 in brackets rejected", raw: "[203.0.113.10]:18444", wantErr: true},
		{name: "zone rejected", raw: "[fe80::1%25eth0]:18444", wantErr: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			endpoint, err := ParseEndpoint(tc.raw)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("ParseEndpoint(%q) = %v, want error", tc.raw, endpoint)
				}
				return
			}
			if err != nil {
				t.Fatalf("ParseEndpoint(%q): %v", tc.raw, err)
			}
			if endpoint.String() != tc.want {
				t.Fatalf("ParseEndpoint(%q) = %q, want %q", tc.raw, endpoint.String(), tc.want)
			}
		})
	}
}

func TestDialPolicyPortsAndAddresses(t *testing.T) {
	policy, err := NewDialPolicy([]int{18444, 28443}, nil, false)
	if err != nil {
		t.Fatalf("new policy: %v", err)
	}
	if _, err := policy.NormalizeEndpoint("agent.example.com:28443"); err != nil {
		t.Fatalf("allowed port rejected: %v", err)
	}
	if _, err := policy.NormalizeEndpoint("agent.example.com:2222"); err == nil ||
		!strings.Contains(err.Error(), "not allowed") {
		t.Fatalf("non-allowed port err = %v", err)
	}

	cases := []struct {
		ip   string
		want bool
	}{
		{"203.0.113.10", true},
		{"10.0.0.10", true},
		{"192.168.1.10", true},
		{"127.0.0.1", false},
		{"::1", false},
		{"169.254.169.254", false},
		{"0.0.0.0", false},
		{"224.0.0.1", false},
		{"240.0.0.1", false},
		{"fe80::1", false},
		{"ff02::1", false},
		{"2001:db8::1", true},
	}
	for _, tc := range cases {
		if got := policy.AllowsIP(net.ParseIP(tc.ip)); got != tc.want {
			t.Fatalf("AllowsIP(%s) = %t, want %t", tc.ip, got, tc.want)
		}
	}

	loopbackPolicy, err := NewDialPolicy(nil, nil, true)
	if err != nil {
		t.Fatalf("new loopback policy: %v", err)
	}
	if !loopbackPolicy.AllowsIP(net.ParseIP("127.0.0.1")) || !loopbackPolicy.AllowsIP(net.ParseIP("::1")) {
		t.Fatal("allow_loopback must permit loopback addresses")
	}
	if loopbackPolicy.AllowsIP(net.ParseIP("169.254.169.254")) {
		t.Fatal("allow_loopback must not permit metadata address")
	}

	filtered := policy.FilterIPs([]net.IP{
		net.ParseIP("203.0.113.10"),
		net.ParseIP("127.0.0.1"),
		net.ParseIP("10.0.0.1"),
	})
	if len(filtered) != 2 {
		t.Fatalf("FilterIPs = %v, want 2 allowed addresses", filtered)
	}
}

func TestDialPolicyRejectsInvalidConfig(t *testing.T) {
	if _, err := NewDialPolicy([]int{0}, nil, false); err == nil {
		t.Fatal("port 0 must be rejected")
	}
	if _, err := NewDialPolicy(nil, []string{"not-a-cidr"}, false); err == nil {
		t.Fatal("invalid denied CIDR must be rejected")
	}
}
