package monitoring

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/netip"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

var ErrDestinationDenied = errors.New("monitoring destination is not allowed")

// RestrictedTransport connects directly to an allowed resolved address. It
// deliberately ignores proxy environment variables and rechecks every dial.
type NetworkPolicy struct{ prefixes []netip.Prefix }

func NewNetworkPolicy(cidrs []string) (*NetworkPolicy, error) {
	prefixes := make([]netip.Prefix, 0, len(cidrs))
	for _, cidr := range cidrs {
		prefix, err := netip.ParsePrefix(strings.TrimSpace(cidr))
		if err != nil {
			return nil, errors.New("invalid monitoring allowed CIDR")
		}
		prefixes = append(prefixes, prefix.Masked())
	}
	return &NetworkPolicy{prefixes: prefixes}, nil
}

// IntersectNetworkPolicies requires independent Hub and Agent authorization.
func IntersectNetworkPolicies(hubCIDRs, agentCIDRs []string) (*NetworkPolicy, error) {
	hub, err := NewNetworkPolicy(hubCIDRs)
	if err != nil {
		return nil, err
	}
	agent, err := NewNetworkPolicy(agentCIDRs)
	if err != nil {
		return nil, err
	}
	result := &NetworkPolicy{}
	for _, a := range hub.prefixes {
		for _, b := range agent.prefixes {
			if a.Contains(b.Addr()) && a.Bits() <= b.Bits() {
				result.prefixes = append(result.prefixes, b)
			} else if b.Contains(a.Addr()) && b.Bits() <= a.Bits() {
				result.prefixes = append(result.prefixes, a)
			}
		}
	}
	return result, nil
}

func (p *NetworkPolicy) addresses(ctx context.Context, host string) ([]netip.Addr, error) {
	addresses, err := net.DefaultResolver.LookupNetIP(ctx, "ip", host)
	if err != nil {
		return nil, ErrProbeUnreachable
	}
	allowed := []netip.Addr{}
	for _, address := range addresses {
		address = address.Unmap()
		for _, prefix := range p.prefixes {
			if prefix.Contains(address) {
				allowed = append(allowed, address)
				break
			}
		}
	}
	if len(allowed) == 0 {
		return nil, ErrDestinationDenied
	}
	return allowed, nil
}

func RestrictedTransport(cidrs []string) (*http.Transport, error) {
	policy, err := NewNetworkPolicy(cidrs)
	if err != nil {
		return nil, err
	}
	return policy.Transport(), nil
}

func (p *NetworkPolicy) Transport() *http.Transport {
	return &http.Transport{
		Proxy:             nil,
		DisableKeepAlives: true,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			host, port, err := net.SplitHostPort(address)
			if err != nil {
				return nil, errors.New("invalid monitoring address")
			}
			addresses, err := p.addresses(ctx, host)
			if err != nil {
				return nil, err
			}
			for _, address := range addresses {
				conn, err := (&net.Dialer{}).DialContext(ctx, "tcp", net.JoinHostPort(address.String(), port))
				if err == nil {
					return conn, nil
				}
			}
			return nil, errors.New("monitoring address unreachable")
		},
	}
}

func (p *NetworkPolicy) Observe(ctx context.Context, kind, host, port string, timeout time.Duration) (HTTPObservation, error) {
	var observation HTTPObservation
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	start := time.Now()
	addresses, err := p.addresses(ctx, host)
	if err == nil {
		switch kind {
		case "tcp":
			err = ErrProbeUnreachable
			for _, address := range addresses {
				conn, dialErr := (&net.Dialer{}).DialContext(ctx, "tcp", net.JoinHostPort(address.String(), port))
				if dialErr == nil {
					_ = conn.Close()
					err = nil
					break
				}
			}
		case "icmp":
			// Only an already-authorized numeric address is passed to ping.
			binary := "/usr/bin/ping"
			if _, lookupErr := exec.LookPath(binary); lookupErr != nil {
				err = errors.New("ICMP probe unsupported")
				break
			}
			seconds := int((timeout + time.Second - 1) / time.Second)
			cmd := exec.CommandContext(ctx, binary, "-n", "-c", "1", "-W", strconv.Itoa(seconds), addresses[0].String())
			if runErr := cmd.Run(); runErr != nil {
				var exit *exec.ExitError
				if errors.Is(ctx.Err(), context.DeadlineExceeded) || (errors.As(runErr, &exit) && exit.ExitCode() == 1) {
					err = ErrProbeUnreachable
				} else if errors.Is(ctx.Err(), context.Canceled) {
					err = errors.New("network probe canceled")
				} else {
					err = errors.New("ICMP probe unsupported or permission denied")
				}
			}
		default:
			err = errors.New("unsupported network probe")
		}
	}
	observation.ObservedAt, observation.Duration = time.Now().UTC(), time.Since(start)
	if err == nil {
		observation.Value, observation.HasValue = true, true
	}
	return observation, err
}
