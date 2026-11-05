package hubserver

import (
	"fmt"
	"log/slog"
	"path/filepath"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/inbound"
)

const (
	defaultInboundDialConcurrency = 16
	defaultInboundDialTimeout     = 20 * time.Second
	defaultInboundBackoffInitial  = 2 * time.Second
	defaultInboundBackoffMax      = 60 * time.Second
)

type inboundRuntime struct {
	enabled   bool
	policy    *inbound.DialPolicy
	authority *inbound.Authority
}

// prepareInbound 校验入站配置并初始化 PKI。默认关闭：不生成证书、不拨号。
func prepareInbound(cfg Config, logger *slog.Logger) (inboundRuntime, error) {
	policy, err := inbound.NewDialPolicy(cfg.InboundAllowedPorts, cfg.InboundDeniedCIDRs, cfg.InboundAllowLoopback)
	if err != nil {
		return inboundRuntime{}, err
	}
	runtime := inboundRuntime{enabled: cfg.InboundEnabled, policy: policy}
	if !cfg.InboundEnabled {
		logger.Info("inbound connection disabled")
		return runtime, nil
	}
	if cfg.DataDir == "" {
		return inboundRuntime{}, fmt.Errorf("data_dir is required when inbound connection is enabled")
	}

	concurrency := cfg.InboundDialConcurrency
	if concurrency == 0 {
		concurrency = defaultInboundDialConcurrency
	}
	if concurrency < 1 || concurrency > 256 {
		return inboundRuntime{}, fmt.Errorf("inbound dial concurrency must be between 1 and 256")
	}
	timeout := cfg.InboundDialTimeout
	if timeout == 0 {
		timeout = defaultInboundDialTimeout
	}
	if timeout <= 0 {
		return inboundRuntime{}, fmt.Errorf("inbound dial timeout must be positive")
	}
	backoffInitial := cfg.InboundBackoffInitial
	if backoffInitial == 0 {
		backoffInitial = defaultInboundBackoffInitial
	}
	backoffMax := cfg.InboundBackoffMax
	if backoffMax == 0 {
		backoffMax = defaultInboundBackoffMax
	}
	if backoffInitial <= 0 || backoffMax < backoffInitial {
		return inboundRuntime{}, fmt.Errorf("inbound backoff must satisfy 0 < initial <= max")
	}

	authority, err := inbound.OpenAuthority(inbound.Paths{
		Dir:        filepath.Join(cfg.DataDir, "inbound"),
		CACert:     cfg.InboundCAFile,
		CAKey:      cfg.InboundCAKeyFile,
		ClientCert: cfg.InboundClientCertFile,
		ClientKey:  cfg.InboundClientKeyFile,
	})
	if err != nil {
		return inboundRuntime{}, fmt.Errorf("init inbound pki: %w", err)
	}
	runtime.authority = authority
	logger.Info("inbound connection enabled",
		"dial_concurrency", concurrency,
		"dial_timeout", timeout,
		"backoff_initial", backoffInitial,
		"backoff_max", backoffMax,
		"ca_fingerprint", authority.CACertFingerprint(),
		"hub_client_fingerprint", authority.HubClientFingerprint(),
		"generated", authority.Generated())
	if authority.Generated() {
		logger.Warn("inbound PKI was generated; back up the CA private key and client certificate")
	}
	return runtime, nil
}
