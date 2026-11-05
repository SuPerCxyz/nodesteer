package hub

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"log/slog"
	"sort"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"gopkg.in/yaml.v3"
)

var (
	ErrInboundBundleDisabled        = errors.New("inbound bundle is disabled")
	ErrInboundBundleAlreadyIssued   = errors.New("inbound bundle already issued; rotate explicitly")
	ErrInboundBundleEndpointMissing = errors.New("node endpoint is required before issuing inbound bundle")
)

// InboundBundle 一次性下载的入站安装包。
type InboundBundle struct {
	Archive  []byte
	Filename string
}

// InboundBundleService 负责节点入站身份签发与 tar.gz 安装包生成。
type InboundBundleService struct {
	authority         *inbound.Authority
	nodes             *NodeManager
	sessions          *SessionManager
	registrationToken string
	gatewayBaseURL    string
	baseURL           string
	logger            *slog.Logger
}

func NewInboundBundleService(authority *inbound.Authority, nodes *NodeManager, sessions *SessionManager,
	registrationToken, gatewayBaseURL, baseURL string, logger *slog.Logger) *InboundBundleService {
	return &InboundBundleService{
		authority: authority, nodes: nodes, sessions: sessions,
		registrationToken: registrationToken, gatewayBaseURL: gatewayBaseURL, baseURL: baseURL, logger: logger,
	}
}

// Issue 为节点签发/轮换入站身份并生成安装包。rotate=false 时已有身份返回错误，
// 必须显式轮换；endpoint 变更后 inbound_provisioned 已被清零，可重新签发。
func (s *InboundBundleService) Issue(ctx context.Context, nodeID string, rotate bool) (*InboundBundle, error) {
	if s == nil || s.authority == nil {
		return nil, ErrInboundBundleDisabled
	}
	node, err := s.nodes.GetNode(ctx, nodeID)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(node.Endpoint) == "" {
		return nil, ErrInboundBundleEndpointMissing
	}
	if node.InboundProvisioned && !rotate {
		return nil, ErrInboundBundleAlreadyIssued
	}
	issued, err := s.authority.CreateServerCertificate(node.ID, node.Endpoint)
	if err != nil {
		return nil, fmt.Errorf("issue node certificate: %w", err)
	}
	token, err := inbound.NewNodeToken()
	if err != nil {
		return nil, err
	}
	encrypted, err := s.authority.EncryptToken(token)
	if err != nil {
		return nil, err
	}
	archive, err := s.buildArchive(node, issued, token)
	if err != nil {
		return nil, err
	}
	// 先落库再关闭旧会话：旧令牌立即失效，随后由触发的新拨号使用新身份。
	if err := s.nodes.store.UpdateNodeInboundIdentity(ctx, node.ID, true, issued.Fingerprint, issued.Serial, issued.NotAfter, encrypted); err != nil {
		return nil, err
	}
	_ = s.nodes.store.UpdateNodeInboundDiagnostics(ctx, node.ID, time.Time{}, "")
	if rotate {
		s.closeInboundSession(node.ID)
	}
	name := bundleFilename(node)
	if s.logger != nil {
		s.logger.Info("inbound bundle issued", "node", node.ID, "endpoint", node.Endpoint, "rotate", rotate,
			"cert_serial", issued.Serial, "cert_not_after", issued.NotAfter)
	}
	return &InboundBundle{Archive: archive, Filename: name}, nil
}

func (s *InboundBundleService) closeInboundSession(nodeID string) {
	conn, ok := s.sessions.Get(nodeID)
	if !ok || !SessionIsInbound(conn) {
		return
	}
	// 先注销再关闭，避免断开回执把节点误标为 offline。
	s.sessions.Unregister(nodeID)
	_ = conn.Close()
}

func (s *InboundBundleService) buildArchive(node *models.Node, issued *inbound.IssuedCertificate, token []byte) ([]byte, error) {
	files := map[string]bundleFile{
		"ca.pem":                     {data: s.authority.CACertPEM(), mode: 0o644},
		"agent-server.pem":           {data: issued.CertPEM, mode: 0o644},
		"agent-server-key.pem":       {data: issued.KeyPEM, mode: 0o600},
		"hub-client-fingerprint.txt": {data: []byte(s.authority.HubClientFingerprint() + "\n"), mode: 0o644},
		"inbound-token":              {data: []byte(base64.StdEncoding.EncodeToString(token) + "\n"), mode: 0o600},
		"agent-inbound.yaml":         {data: s.agentConfig(node), mode: 0o600},
		"install-inbound.sh":         {data: []byte(installInboundScript), mode: 0o755},
		"README.txt":                 {data: []byte(bundleReadme(node)), mode: 0o644},
	}
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)

	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for _, name := range names {
		file := files[name]
		header := &tar.Header{
			Name: name, Mode: int64(file.mode), Size: int64(len(file.data)),
			ModTime: time.Now(), Typeflag: tar.TypeReg,
		}
		if err := tw.WriteHeader(header); err != nil {
			return nil, err
		}
		if _, err := tw.Write(file.data); err != nil {
			return nil, err
		}
	}
	if err := tw.Close(); err != nil {
		return nil, err
	}
	if err := gz.Close(); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

type bundleFile struct {
	data []byte
	mode int64
}

type bundleYAML struct {
	HubURL            string            `yaml:"hub_url"`
	RegistrationToken string            `yaml:"registration_token"`
	AgentID           string            `yaml:"agent_id"`
	NodeName          string            `yaml:"node_name"`
	NodeIP            string            `yaml:"node_ip"`
	DataDir           string            `yaml:"data_dir"`
	ConnectionMode    string            `yaml:"connection_mode"`
	Inbound           bundleInboundYAML `yaml:"inbound"`
}

type bundleInboundYAML struct {
	ListenAddr               string   `yaml:"listen_addr"`
	Endpoint                 string   `yaml:"endpoint"`
	CertFile                 string   `yaml:"cert_file"`
	KeyFile                  string   `yaml:"key_file"`
	CAFile                   string   `yaml:"ca_file"`
	HubClientFingerprintFile string   `yaml:"hub_client_fingerprint_file"`
	TokenFile                string   `yaml:"token_file"`
	AllowedSourceCIDRs       []string `yaml:"allowed_source_cidrs,omitempty"`
	MaxConnections           int      `yaml:"max_connections"`
	HandshakeTimeout         string   `yaml:"handshake_timeout"`
}

func (s *InboundBundleService) agentConfig(node *models.Node) []byte {
	hubURL := s.gatewayBaseURL
	if hubURL == "" {
		hubURL = s.baseURL
	}
	hubURL = wsURL(hubURL)
	config := bundleYAML{
		HubURL:            hubURL,
		RegistrationToken: s.registrationToken,
		AgentID:           node.AgentID,
		NodeName:          firstNonEmpty(node.Hostname, node.ID),
		NodeIP:            node.IP,
		DataDir:           "/var/lib/nodesteer",
		ConnectionMode:    "auto",
		Inbound: bundleInboundYAML{
			ListenAddr:               "0.0.0.0:18444",
			Endpoint:                 node.Endpoint,
			CertFile:                 "/etc/nodesteer/inbound/agent-server.pem",
			KeyFile:                  "/etc/nodesteer/inbound/agent-server-key.pem",
			CAFile:                   "/etc/nodesteer/inbound/ca.pem",
			HubClientFingerprintFile: "/etc/nodesteer/inbound/hub-client-fingerprint.txt",
			TokenFile:                "/etc/nodesteer/inbound/inbound-token",
			MaxConnections:           8,
			HandshakeTimeout:         "20s",
		},
	}
	data, _ := yaml.Marshal(&config)
	return data
}

func wsURL(base string) string {
	base = strings.TrimRight(strings.TrimSpace(base), "/")
	switch {
	case strings.HasPrefix(base, "https://"):
		return "wss://" + strings.TrimPrefix(base, "https://")
	case strings.HasPrefix(base, "http://"):
		return "ws://" + strings.TrimPrefix(base, "http://")
	default:
		return base
	}
}

func bundleFilename(node *models.Node) string {
	base := strings.ToLower(strings.TrimSpace(firstNonEmpty(node.Hostname, node.ID)))
	var b strings.Builder
	for _, ch := range base {
		if (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_' || ch == '.' {
			b.WriteRune(ch)
		} else {
			b.WriteByte('_')
		}
	}
	name := strings.Trim(b.String(), "._-")
	if name == "" {
		name = node.ID
	}
	return "nodesteer-inbound-" + name + ".tar.gz"
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

const installInboundScript = `#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v nodesteer-agent >/dev/null || { echo "nodesteer-agent is not installed" >&2; exit 1; }
install -d -m 0755 /etc/nodesteer /etc/nodesteer/inbound /var/lib/nodesteer
install -m 0644 "$base/ca.pem" /etc/nodesteer/inbound/ca.pem
install -m 0644 "$base/agent-server.pem" /etc/nodesteer/inbound/agent-server.pem
install -m 0600 "$base/agent-server-key.pem" /etc/nodesteer/inbound/agent-server-key.pem
install -m 0644 "$base/hub-client-fingerprint.txt" /etc/nodesteer/inbound/hub-client-fingerprint.txt
install -m 0600 "$base/inbound-token" /etc/nodesteer/inbound/inbound-token
install -m 0600 "$base/agent-inbound.yaml" /etc/nodesteer/agent.yaml
systemctl daemon-reload 2>/dev/null || true
systemctl enable nodesteer-agent 2>/dev/null || true
systemctl restart nodesteer-agent
echo "NodeSteer inbound bundle installed; ensure tcp/18444 is reachable from Hub."
`

func bundleReadme(node *models.Node) string {
	return fmt.Sprintf(`NodeSteer inbound bundle for node %s
Endpoint: %s

Install:
  1. Ensure nodesteer-agent is already installed on this host.
  2. Copy this archive to the Agent host and extract it.
  3. Run: sudo ./install-inbound.sh
  4. Allow inbound tcp/18444 only from the Hub egress address.

Security:
  - agent-server-key.pem and inbound-token are node secrets; keep mode 0600.
  - This bundle is one-time. Downloading again does not return the same secrets;
    rotating the bundle invalidates the old certificate and token immediately.
  - Back up the Hub CA private key and client certificate separately.

Rollback:
  - Stop nodesteer-agent, remove /etc/nodesteer/inbound and the inbound section
    from /etc/nodesteer/agent.yaml, then restart the Agent.
  - In Hub, rotate or disable the node inbound identity.
`, firstNonEmpty(node.Hostname, node.ID), node.Endpoint)
}
