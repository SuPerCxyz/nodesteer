package inbound

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	caValidity        = 10 * 365 * 24 * time.Hour
	leafValidity      = 365 * 24 * time.Hour
	inboundTokenBytes = 32
)

// Paths PKI 文件路径。CA/客户端证书均为成对配置；为空时回退到 Dir 下的
// ca.pem/ca-key.pem 与 client.pem/client-key.pem。
type Paths struct {
	Dir        string
	CACert     string
	CAKey      string
	ClientCert string
	ClientKey  string
}

// Authority Hub 入站 PKI：CA、Hub 客户端证书与节点令牌加密密钥。
// CA 私钥只保存在内存/本地文件，不提供导出方法。
type Authority struct {
	caCert        *x509.Certificate
	caKey         *ecdsa.PrivateKey
	caCertPEM     []byte
	clientCert    *x509.Certificate
	clientCertPEM []byte
	clientTLS     tls.Certificate
	tokenKey      []byte
	generated     bool
}

// IssuedCertificate Hub 为节点签发的服务端证书。
type IssuedCertificate struct {
	CertPEM     []byte
	KeyPEM      []byte
	Fingerprint string
	Serial      string
	NotAfter    time.Time
}

// OpenAuthority 加载或首次生成 Hub 入站 PKI。
//
// 显式配置的证书/私钥不可读、不匹配、过期或权限过宽时返回错误，不静默回退到
// 自动生成；未配置时在 Paths.Dir 下生成并持久化（私钥 0600）。
func OpenAuthority(paths Paths) (*Authority, error) {
	caCertPath, caKeyPath, clientCertPath, clientKeyPath, err := resolvePKIPaths(paths)
	if err != nil {
		return nil, err
	}
	if paths.Dir != "" {
		if err := os.MkdirAll(paths.Dir, 0o700); err != nil {
			return nil, fmt.Errorf("create inbound pki dir: %w", err)
		}
	}

	authority := &Authority{}
	authority.generated = false
	caCert, caKey, caCertPEM, created, err := loadOrCreateCA(caCertPath, caKeyPath, paths.CACert == "")
	if err != nil {
		return nil, err
	}
	authority.caCert, authority.caKey, authority.caCertPEM = caCert, caKey, caCertPEM
	authority.generated = authority.generated || created

	clientCert, _, clientCertPEM, clientKeyPEM, created, err := loadOrCreateClient(
		clientCertPath, clientKeyPath, caCert, caKey, paths.ClientCert == "")
	if err != nil {
		return nil, err
	}
	authority.clientCert, authority.clientCertPEM = clientCert, clientCertPEM
	authority.generated = authority.generated || created

	keyPair, err := tls.X509KeyPair(clientCertPEM, clientKeyPEM)
	if err != nil {
		return nil, fmt.Errorf("load inbound hub client keypair: %w", err)
	}
	authority.clientTLS = keyPair

	tokenKey, err := deriveTokenKey(caKey)
	if err != nil {
		return nil, err
	}
	authority.tokenKey = tokenKey
	return authority, nil
}

// Generated 表示本次启动是否生成了新的 CA 或客户端证书。
func (a *Authority) Generated() bool { return a != nil && a.generated }

// CACertPEM 返回 CA 证书 PEM。
func (a *Authority) CACertPEM() []byte {
	if a == nil {
		return nil
	}
	return append([]byte(nil), a.caCertPEM...)
}

// CACertFingerprint 返回 CA 证书 DER 的 SHA-256（小写 hex）。
func (a *Authority) CACertFingerprint() string {
	if a == nil || a.caCert == nil {
		return ""
	}
	return certificateFingerprint(a.caCert.Raw)
}

// HubClientCertPEM 返回 Hub 客户端证书 PEM。
func (a *Authority) HubClientCertPEM() []byte {
	if a == nil {
		return nil
	}
	return append([]byte(nil), a.clientCertPEM...)
}

// HubClientFingerprint 返回 Hub 客户端证书 DER 的 SHA-256（小写 hex）。
func (a *Authority) HubClientFingerprint() string {
	if a == nil || a.clientCert == nil {
		return ""
	}
	return certificateFingerprint(a.clientCert.Raw)
}

// HubClientCertificate 返回 Hub 客户端证书。
func (a *Authority) HubClientCertificate() *x509.Certificate {
	if a == nil {
		return nil
	}
	return a.clientCert
}

// ClientTLSConfig 返回供入站拨号器使用的 mTLS 客户端配置。
func (a *Authority) ClientTLSConfig() *tls.Config {
	if a == nil {
		return nil
	}
	return &tls.Config{
		MinVersion:   tls.VersionTLS12,
		Certificates: []tls.Certificate{a.clientTLS},
		RootCAs:      a.RootCAs(),
	}
}

// RootCAs 返回只包含 Hub CA 的证书池。
func (a *Authority) RootCAs() *x509.CertPool {
	pool := x509.NewCertPool()
	if a != nil {
		pool.AppendCertsFromPEM(a.caCertPEM)
	}
	return pool
}

// CreateServerCertificate 为节点签发服务端证书；SAN 按 endpoint 的域名/IP 生成。
func (a *Authority) CreateServerCertificate(nodeID, endpoint string) (*IssuedCertificate, error) {
	if a == nil {
		return nil, errors.New("inbound pki is not initialized")
	}
	parsed, err := ParseEndpoint(endpoint)
	if err != nil {
		return nil, err
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate node key: %w", err)
	}
	serial, err := randomSerial()
	if err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	commonName := "nodesteer-agent"
	if nodeID != "" {
		commonName += "-" + nodeID
	}
	if len(commonName) > 64 {
		commonName = commonName[:64]
	}
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: commonName, Organization: []string{"NodeSteer"}},
		NotBefore:    now.Add(-5 * time.Minute),
		NotAfter:     now.Add(leafValidity),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	if ip := net.ParseIP(parsed.Host); ip != nil {
		template.IPAddresses = []net.IP{ip}
	} else {
		template.DNSNames = []string{parsed.Host}
	}
	der, err := x509.CreateCertificate(rand.Reader, template, a.caCert, &key.PublicKey, a.caKey)
	if err != nil {
		return nil, fmt.Errorf("sign node certificate: %w", err)
	}
	certificate, err := x509.ParseCertificate(der)
	if err != nil {
		return nil, fmt.Errorf("parse signed node certificate: %w", err)
	}
	keyPEM, err := marshalPrivateKey(key)
	if err != nil {
		return nil, err
	}
	return &IssuedCertificate{
		CertPEM:     pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}),
		KeyPEM:      keyPEM,
		Fingerprint: certificateFingerprint(der),
		Serial:      serial.Text(16),
		NotAfter:    certificate.NotAfter.UTC(),
	}, nil
}

// NewNodeToken 生成 32 字节随机入站令牌。
func NewNodeToken() ([]byte, error) {
	token := make([]byte, inboundTokenBytes)
	if _, err := rand.Read(token); err != nil {
		return nil, fmt.Errorf("generate inbound token: %w", err)
	}
	return token, nil
}

// EncryptToken 返回 base64(nonce||ciphertext) 形式的 AES-256-GCM 密文。
func (a *Authority) EncryptToken(token []byte) (string, error) {
	if a == nil || len(a.tokenKey) == 0 {
		return "", errors.New("inbound pki is not initialized")
	}
	block, err := aes.NewCipher(a.tokenKey)
	if err != nil {
		return "", err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return "", fmt.Errorf("generate token nonce: %w", err)
	}
	sealed := gcm.Seal(nonce, nonce, token, nil)
	return base64.StdEncoding.EncodeToString(sealed), nil
}

// DecryptToken 解密 EncryptToken 的输出，并校验 GCM 认证标签。
func (a *Authority) DecryptToken(encoded string) ([]byte, error) {
	if a == nil || len(a.tokenKey) == 0 {
		return nil, errors.New("inbound pki is not initialized")
	}
	raw, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return nil, fmt.Errorf("decode inbound token: %w", err)
	}
	block, err := aes.NewCipher(a.tokenKey)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	if len(raw) < gcm.NonceSize() {
		return nil, errors.New("inbound token ciphertext is too short")
	}
	nonce, ciphertext := raw[:gcm.NonceSize()], raw[gcm.NonceSize():]
	token, err := gcm.Open(nil, nonce, ciphertext, nil)
	if err != nil {
		return nil, fmt.Errorf("decrypt inbound token: %w", err)
	}
	return token, nil
}

func resolvePKIPaths(paths Paths) (string, string, string, string, error) {
	if (paths.CACert == "") != (paths.CAKey == "") {
		return "", "", "", "", errors.New("inbound CA certificate and key must be configured together")
	}
	if (paths.ClientCert == "") != (paths.ClientKey == "") {
		return "", "", "", "", errors.New("inbound client certificate and key must be configured together")
	}
	needsDefaults := paths.CACert == "" || paths.ClientCert == ""
	if needsDefaults && paths.Dir == "" {
		return "", "", "", "", errors.New("inbound pki directory is required when certificate paths are omitted")
	}
	caCert, caKey := paths.CACert, paths.CAKey
	if caCert == "" {
		caCert = filepath.Join(paths.Dir, "ca.pem")
		caKey = filepath.Join(paths.Dir, "ca-key.pem")
	}
	clientCert, clientKey := paths.ClientCert, paths.ClientKey
	if clientCert == "" {
		clientCert = filepath.Join(paths.Dir, "client.pem")
		clientKey = filepath.Join(paths.Dir, "client-key.pem")
	}
	return caCert, caKey, clientCert, clientKey, nil
}

func loadOrCreateCA(certPath, keyPath string, allowCreate bool) (*x509.Certificate, *ecdsa.PrivateKey, []byte, bool, error) {
	certExists, keyExists, err := filePairState(certPath, keyPath)
	if err != nil {
		return nil, nil, nil, false, err
	}
	if certExists && keyExists {
		cert, key, certPEM, err := loadCA(certPath, keyPath)
		return cert, key, certPEM, false, err
	}
	if !allowCreate {
		return nil, nil, nil, false, fmt.Errorf("configured inbound CA files are missing or unreadable: %s, %s", certPath, keyPath)
	}
	if certExists != keyExists {
		return nil, nil, nil, false, fmt.Errorf("inbound CA certificate and key must both exist: %s, %s", certPath, keyPath)
	}
	cert, key, certPEM, err := createCA()
	if err != nil {
		return nil, nil, nil, false, err
	}
	keyPEM, err := marshalPrivateKey(key)
	if err != nil {
		return nil, nil, nil, false, err
	}
	if err := writePEMFile(certPath, certPEM, 0o600); err != nil {
		return nil, nil, nil, false, err
	}
	if err := writePEMFile(keyPath, keyPEM, 0o600); err != nil {
		return nil, nil, nil, false, err
	}
	return cert, key, certPEM, true, nil
}

func loadOrCreateClient(certPath, keyPath string, caCert *x509.Certificate, caKey *ecdsa.PrivateKey, allowCreate bool) (*x509.Certificate, *ecdsa.PrivateKey, []byte, []byte, bool, error) {
	certExists, keyExists, err := filePairState(certPath, keyPath)
	if err != nil {
		return nil, nil, nil, nil, false, err
	}
	if certExists && keyExists {
		cert, key, certPEM, keyPEM, err := loadClient(certPath, keyPath, caCert)
		return cert, key, certPEM, keyPEM, false, err
	}
	if !allowCreate {
		return nil, nil, nil, nil, false, fmt.Errorf("configured inbound client certificate files are missing or unreadable: %s, %s", certPath, keyPath)
	}
	if certExists != keyExists {
		return nil, nil, nil, nil, false, fmt.Errorf("inbound client certificate and key must both exist: %s, %s", certPath, keyPath)
	}
	cert, key, certPEM, err := createClientCertificate(caCert, caKey)
	if err != nil {
		return nil, nil, nil, nil, false, err
	}
	keyPEM, err := marshalPrivateKey(key)
	if err != nil {
		return nil, nil, nil, nil, false, err
	}
	if err := writePEMFile(certPath, certPEM, 0o600); err != nil {
		return nil, nil, nil, nil, false, err
	}
	if err := writePEMFile(keyPath, keyPEM, 0o600); err != nil {
		return nil, nil, nil, nil, false, err
	}
	return cert, key, certPEM, keyPEM, true, nil
}

func filePairState(certPath, keyPath string) (bool, bool, error) {
	certExists, err := fileExists(certPath)
	if err != nil {
		return false, false, err
	}
	keyExists, err := fileExists(keyPath)
	if err != nil {
		return false, false, err
	}
	return certExists, keyExists, nil
}

func fileExists(path string) (bool, error) {
	info, err := os.Stat(path)
	if err == nil {
		if info.IsDir() {
			return false, fmt.Errorf("inbound pki path is a directory: %s", path)
		}
		return true, nil
	}
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return false, err
}

func loadCA(certPath, keyPath string) (*x509.Certificate, *ecdsa.PrivateKey, []byte, error) {
	certPEM, err := os.ReadFile(certPath)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("read inbound CA certificate: %w", err)
	}
	cert, err := parseCertificatePEM(certPEM)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("parse inbound CA certificate: %w", err)
	}
	if !cert.IsCA || !cert.BasicConstraintsValid || cert.KeyUsage&x509.KeyUsageCertSign == 0 {
		return nil, nil, nil, errors.New("configured inbound CA certificate is not a signing CA")
	}
	if err := validateCertificateTime(cert); err != nil {
		return nil, nil, nil, fmt.Errorf("inbound CA certificate: %w", err)
	}
	key, err := loadPrivateKey(keyPath)
	if err != nil {
		return nil, nil, nil, err
	}
	if err := ensureKeyMatchesCertificate(key, cert); err != nil {
		return nil, nil, nil, err
	}
	return cert, key, certPEM, nil
}

func loadClient(certPath, keyPath string, caCert *x509.Certificate) (*x509.Certificate, *ecdsa.PrivateKey, []byte, []byte, error) {
	certPEM, err := os.ReadFile(certPath)
	if err != nil {
		return nil, nil, nil, nil, fmt.Errorf("read inbound client certificate: %w", err)
	}
	cert, err := parseCertificatePEM(certPEM)
	if err != nil {
		return nil, nil, nil, nil, fmt.Errorf("parse inbound client certificate: %w", err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(caCert)
	if _, err := cert.Verify(x509.VerifyOptions{
		Roots:     roots,
		KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}); err != nil {
		return nil, nil, nil, nil, fmt.Errorf("verify inbound client certificate: %w", err)
	}
	key, err := loadPrivateKey(keyPath)
	if err != nil {
		return nil, nil, nil, nil, err
	}
	if err := ensureKeyMatchesCertificate(key, cert); err != nil {
		return nil, nil, nil, nil, err
	}
	keyPEM, err := os.ReadFile(keyPath)
	if err != nil {
		return nil, nil, nil, nil, fmt.Errorf("read inbound client key: %w", err)
	}
	return cert, key, certPEM, keyPEM, nil
}

func loadPrivateKey(path string) (*ecdsa.PrivateKey, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, fmt.Errorf("read inbound private key: %w", err)
	}
	if info.Mode().Perm()&0o077 != 0 {
		return nil, fmt.Errorf("inbound private key %s must not be accessible by group or others", path)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read inbound private key: %w", err)
	}
	block, _ := pem.Decode(data)
	if block == nil {
		return nil, errors.New("inbound private key is not PEM encoded")
	}
	if key, err := x509.ParsePKCS8PrivateKey(block.Bytes); err == nil {
		ecdsaKey, ok := key.(*ecdsa.PrivateKey)
		if !ok {
			return nil, errors.New("inbound private key must be ECDSA")
		}
		return ecdsaKey, nil
	}
	key, err := x509.ParseECPrivateKey(block.Bytes)
	if err != nil {
		return nil, errors.New("inbound private key must be a PKCS#8 or EC private key")
	}
	return key, nil
}

func parseCertificatePEM(data []byte) (*x509.Certificate, error) {
	block, _ := pem.Decode(data)
	if block == nil || block.Type != "CERTIFICATE" {
		return nil, errors.New("certificate is not PEM encoded")
	}
	return x509.ParseCertificate(block.Bytes)
}

func validateCertificateTime(cert *x509.Certificate) error {
	now := time.Now()
	if now.Before(cert.NotBefore) {
		return fmt.Errorf("not valid before %s", cert.NotBefore.UTC().Format(time.RFC3339))
	}
	if now.After(cert.NotAfter) {
		return fmt.Errorf("expired at %s", cert.NotAfter.UTC().Format(time.RFC3339))
	}
	return nil
}

func ensureKeyMatchesCertificate(key *ecdsa.PrivateKey, cert *x509.Certificate) error {
	want, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
	if err != nil {
		return err
	}
	got, err := x509.MarshalPKIXPublicKey(cert.PublicKey)
	if err != nil {
		return err
	}
	if !strings.EqualFold(hex.EncodeToString(want), hex.EncodeToString(got)) {
		return errors.New("inbound private key does not match certificate")
	}
	return nil
}

func createCA() (*x509.Certificate, *ecdsa.PrivateKey, []byte, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("generate inbound CA key: %w", err)
	}
	serial, err := randomSerial()
	if err != nil {
		return nil, nil, nil, err
	}
	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber:          serial,
		Subject:               pkix.Name{CommonName: "NodeSteer Hub Inbound CA", Organization: []string{"NodeSteer"}},
		NotBefore:             now.Add(-5 * time.Minute),
		NotAfter:              now.Add(caValidity),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign | x509.KeyUsageDigitalSignature,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("create inbound CA certificate: %w", err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		return nil, nil, nil, err
	}
	return cert, key, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), nil
}

func createClientCertificate(caCert *x509.Certificate, caKey *ecdsa.PrivateKey) (*x509.Certificate, *ecdsa.PrivateKey, []byte, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("generate inbound client key: %w", err)
	}
	serial, err := randomSerial()
	if err != nil {
		return nil, nil, nil, err
	}
	now := time.Now().UTC()
	template := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: "nodesteer-hub-client", Organization: []string{"NodeSteer"}},
		NotBefore:    now.Add(-5 * time.Minute),
		NotAfter:     now.Add(leafValidity),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, template, caCert, &key.PublicKey, caKey)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("create inbound client certificate: %w", err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		return nil, nil, nil, err
	}
	return cert, key, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), nil
}

func marshalPrivateKey(key *ecdsa.PrivateKey) ([]byte, error) {
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return nil, fmt.Errorf("marshal private key: %w", err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), nil
}

func writePEMFile(path string, data []byte, mode os.FileMode) error {
	if err := os.WriteFile(path, data, mode); err != nil {
		return fmt.Errorf("write inbound pki file %s: %w", path, err)
	}
	if err := os.Chmod(path, mode); err != nil {
		return fmt.Errorf("set inbound pki file permissions %s: %w", path, err)
	}
	return nil
}

func randomSerial() (*big.Int, error) {
	limit := new(big.Int).Lsh(big.NewInt(1), 128)
	serial, err := rand.Int(rand.Reader, limit)
	if err != nil {
		return nil, fmt.Errorf("generate certificate serial: %w", err)
	}
	return serial.Add(serial, big.NewInt(1)), nil
}

func certificateFingerprint(der []byte) string {
	sum := sha256.Sum256(der)
	return hex.EncodeToString(sum[:])
}

func deriveTokenKey(caKey *ecdsa.PrivateKey) ([]byte, error) {
	der, err := x509.MarshalPKCS8PrivateKey(caKey)
	if err != nil {
		return nil, fmt.Errorf("derive inbound token key: %w", err)
	}
	key, err := hkdf.Key(sha256.New, der, []byte("nodesteer-inbound-token-v1"), "aes-256-gcm", 32)
	if err != nil {
		return nil, fmt.Errorf("derive inbound token key: %w", err)
	}
	return key, nil
}
