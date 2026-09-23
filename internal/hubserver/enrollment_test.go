package hubserver

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func testAgentBinary(t *testing.T, dir string) string {
	t.Helper()
	path := filepath.Join(dir, "nodesteer-agent-amd64")
	if err := os.WriteFile(path, []byte("agent-binary-test"), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestAgentBinaryDownloadIsPublicAndBounded(t *testing.T) {
	dir := t.TempDir()
	agentBinary := testAgentBinary(t, dir)
	armBinary := filepath.Join(dir, "nodesteer-agent-arm64")
	if err := os.WriteFile(armBinary, []byte("agent-binary-arm64"), 0o755); err != nil {
		t.Fatal(err)
	}
	h, err := New(Config{
		DataDir:              dir,
		ArtifactDir:          filepath.Join(dir, "artifacts"),
		AgentBinaryAMD64Path: agentBinary,
		AgentBinaryARM64Path: armBinary,
	}, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/api/agent/binary?architecture=amd64")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	expected := sha256.Sum256([]byte("agent-binary-test"))
	if resp.StatusCode != http.StatusOK || string(body) != "agent-binary-test" ||
		resp.Header.Get("X-Agent-Binary-SHA256") != hex.EncodeToString(expected[:]) ||
		resp.Header.Get("Content-Length") != "17" {
		t.Fatalf("unexpected binary response: status=%d headers=%v body=%q", resp.StatusCode, resp.Header, body)
	}

	missing, err := http.Get(ts.URL + "/api/agent/binary?architecture=mips64")
	if err != nil {
		t.Fatal(err)
	}
	missing.Body.Close()
	if missing.StatusCode != http.StatusBadRequest {
		t.Fatalf("expected invalid architecture response, got %d", missing.StatusCode)
	}

	arm, err := http.Get(ts.URL + "/api/agent/binary?architecture=arm64")
	if err != nil {
		t.Fatal(err)
	}
	armBody, _ := io.ReadAll(arm.Body)
	arm.Body.Close()
	if arm.StatusCode != http.StatusOK || string(armBody) != "agent-binary-arm64" {
		t.Fatalf("unexpected arm64 binary response: status=%d body=%q", arm.StatusCode, armBody)
	}
}

func TestNodeEnrollmentMetadata(t *testing.T) {
	dir := t.TempDir()
	agentBinary := testAgentBinary(t, dir)
	h, err := New(Config{
		RegistrationToken:    "registration-test",
		DataDir:              dir,
		ArtifactDir:          dir + "/artifacts",
		AgentBinaryAMD64Path: agentBinary,
		BaseURL:              "http://hub.example:8080",
		GatewayAddr:          ":8443",
		HeartbeatTimeout:     30 * time.Second,
		AdminUsername:        "admin",
		AdminPassword:        "admin123",
		SessionTTL:           time.Hour,
	}, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()
	login, err := http.Post(ts.URL+"/api/login", "application/json", strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	defer login.Body.Close()
	var session struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(login.Body).Decode(&session); err != nil {
		t.Fatal(err)
	}
	params := url.Values{
		"node_name":   {"edge-node-01"},
		"node_ip":     {"203.0.113.10"},
		"hub_address": {"http://public.example:8080"},
	}
	req, err := http.NewRequest(http.MethodGet, ts.URL+"/api/nodes/enrollment?"+params.Encode(), nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+session.Token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("enrollment status %d: %s", resp.StatusCode, body)
	}
	var result map[string]string
	if err := json.Unmarshal(body, &result); err != nil {
		t.Fatal(err)
	}
	shellCheck := exec.Command("bash", "-n")
	shellCheck.Stdin = strings.NewReader(result["native"])
	if output, err := shellCheck.CombinedOutput(); err != nil {
		t.Fatalf("generated native command is invalid: %v: %s", err, output)
	}
	if result["gateway_url"] != "ws://public.example:8443" ||
		!strings.Contains(result["native"], "uname -m") ||
		!strings.Contains(result["native"], "x-agent-binary-sha256") ||
		!strings.Contains(result["native"], "sha256sum -c") ||
		!strings.Contains(result["native"], "node_name: \"edge-node-01\"") ||
		!strings.Contains(result["native"], "node_ip: \"203.0.113.10\"") ||
		!strings.Contains(result["native"], "registration_token: \"registration-test\"") ||
		!strings.Contains(result["docker_run"], "NODESTEER_REGISTRATION_TOKEN='registration-test'") ||
		!strings.Contains(result["docker_run"], "NODESTEER_NODE_NAME='edge-node-01'") ||
		!strings.Contains(result["docker_run"], "NODESTEER_NODE_IP='203.0.113.10'") ||
		!strings.Contains(result["docker_run"], "NODESTEER_DEPLOYMENT_MODE=docker") ||
		!strings.Contains(result["docker_compose"], "NODESTEER_NODE_NAME: \"edge-node-01\"") ||
		!strings.Contains(result["docker_compose"], "NODESTEER_NODE_IP: \"203.0.113.10\"") ||
		!strings.Contains(result["docker_compose"], "NODESTEER_DEPLOYMENT_MODE: docker") {
		t.Fatalf("unexpected enrollment metadata: %+v", result)
	}

	domainParams := url.Values{
		"node_name":   {"edge-node-domain"},
		"node_ip":     {"agent.example.com"},
		"hub_address": {"http://public.example:8080"},
	}
	domainReq, err := http.NewRequest(http.MethodGet, ts.URL+"/api/nodes/enrollment?"+domainParams.Encode(), nil)
	if err != nil {
		t.Fatal(err)
	}
	domainReq.Header.Set("Authorization", "Bearer "+session.Token)
	domainResp, err := http.DefaultClient.Do(domainReq)
	if err != nil {
		t.Fatal(err)
	}
	defer domainResp.Body.Close()
	if domainResp.StatusCode != http.StatusOK {
		t.Fatalf("domain enrollment status %d", domainResp.StatusCode)
	}
	var domainResult map[string]string
	if err := json.NewDecoder(domainResp.Body).Decode(&domainResult); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(domainResult["native"], "node_ip: \"agent.example.com\"") ||
		!strings.Contains(domainResult["docker_run"], "NODESTEER_NODE_IP='agent.example.com'") ||
		!strings.Contains(domainResult["docker_compose"], "NODESTEER_NODE_IP: \"agent.example.com\"") {
		t.Fatalf("domain address was not preserved: %+v", domainResult)
	}
}

func TestNodeEnrollmentCreatesPendingNode(t *testing.T) {
	dir := t.TempDir()
	agentBinary := testAgentBinary(t, dir)
	h, err := New(Config{
		RegistrationToken:    "registration-test",
		DataDir:              dir,
		ArtifactDir:          dir + "/artifacts",
		AgentBinaryAMD64Path: agentBinary,
		BaseURL:              "http://hub.example:8080",
		GatewayAddr:          ":8443",
		HeartbeatTimeout:     30 * time.Second,
		AdminUsername:        "admin",
		AdminPassword:        "admin123",
		SessionTTL:           time.Hour,
	}, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	ctx := context.Background()
	if err := h.EnsureDefaultAdmin(ctx); err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()
	login, err := http.Post(ts.URL+"/api/login", "application/json", strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	var session struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(login.Body).Decode(&session); err != nil {
		t.Fatal(err)
	}
	login.Body.Close()

	request, err := http.NewRequest(http.MethodPost, ts.URL+"/api/nodes/enrollment", strings.NewReader(`{"node_name":"docker-node","node_ip":"203.0.113.20","hub_address":"http://public.example:8080"}`))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer "+session.Token)
	request.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(response.Body)
		t.Fatalf("enrollment status %d: %s", response.StatusCode, body)
	}
	var result map[string]string
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if result["agent_id"] == "" || result["agent_image"] != "ghcr.io/supercxyz/nodesteer-agent:latest" ||
		!strings.Contains(result["native"], "agent_id: \""+result["agent_id"]+"\"") ||
		!strings.Contains(result["docker_run"], "NODESTEER_AGENT_ID='") ||
		!strings.Contains(result["docker_run"], "ghcr.io/supercxyz/nodesteer-agent:latest") ||
		!strings.Contains(result["docker_compose"], "image: ghcr.io/supercxyz/nodesteer-agent:latest") ||
		!strings.Contains(result["docker_compose"], "NODESTEER_AGENT_ID: \""+result["agent_id"]+"\"") {
		t.Fatalf("unexpected enrollment result: %+v", result)
	}
	nodes, err := h.Nodes().ListNodes(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(nodes) != 1 || nodes[0].Hostname != "docker-node" || nodes[0].Status != models.NodeStatusOffline || nodes[0].AgentID != result["agent_id"] {
		t.Fatalf("unexpected pending nodes: %+v", nodes)
	}
}

func TestNodeEnrollmentGeneratesWithoutLocalBinary(t *testing.T) {
	dir := t.TempDir()
	h, err := New(Config{
		RegistrationToken: "registration-test",
		DataDir:           dir,
		ArtifactDir:       filepath.Join(dir, "artifacts"),
		BaseURL:           "http://hub.example:8080",
		GatewayAddr:       ":8443",
		HeartbeatTimeout:  30 * time.Second,
		AdminUsername:     "admin",
		AdminPassword:     "admin123",
		SessionTTL:        time.Hour,
	}, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	ctx := context.Background()
	if err := h.EnsureDefaultAdmin(ctx); err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()
	login, err := http.Post(ts.URL+"/api/login", "application/json", strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	var session struct {
		Token string `json:"token"`
	}
	if err := json.NewDecoder(login.Body).Decode(&session); err != nil {
		t.Fatal(err)
	}
	login.Body.Close()
	request, err := http.NewRequest(http.MethodPost, ts.URL+"/api/nodes/enrollment", strings.NewReader(`{"node_name":"missing-binary","node_ip":"203.0.113.21","hub_address":"http://public.example:8080"}`))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Authorization", "Bearer "+session.Token)
	request.Header.Set("Content-Type", "application/json")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("expected generation to succeed without local binary, got %d", response.StatusCode)
	}
	nodes, err := h.Nodes().ListNodes(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(nodes) != 1 || nodes[0].Hostname != "missing-binary" {
		t.Fatalf("unexpected pending nodes: %+v", nodes)
	}
}

func TestNodeEnrollmentRejectsInvalidIdentity(t *testing.T) {
	dir := t.TempDir()
	h, err := New(Config{
		RegistrationToken: "registration-test",
		DataDir:           dir,
		ArtifactDir:       dir + "/artifacts",
		BaseURL:           "http://hub.example:8080",
		GatewayAddr:       ":8443",
		HeartbeatTimeout:  30 * time.Second,
		AdminUsername:     "admin",
		AdminPassword:     "admin123",
		SessionTTL:        time.Hour,
	}, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	if err := h.EnsureDefaultAdmin(context.Background()); err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()
	login, err := http.Post(ts.URL+"/api/login", "application/json", strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	var session struct {
		Token string `json:"token"`
	}
	json.NewDecoder(login.Body).Decode(&session)
	login.Body.Close()
	req, err := http.NewRequest(http.MethodGet, ts.URL+"/api/nodes/enrollment?node_name=valid-node&node_ip=-invalid.example.com", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+session.Token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("expected bad request, got %d", resp.StatusCode)
	}
}

func TestFileTransferAPI(t *testing.T) {
	dir := t.TempDir()
	h, err := New(Config{
		RegistrationToken: "registration-test",
		DataDir:           dir,
		ArtifactDir:       dir + "/artifacts",
		BaseURL:           "http://hub.example:8080",
		GatewayAddr:       ":8443",
		HeartbeatTimeout:  30 * time.Second,
		AdminUsername:     "admin",
		AdminPassword:     "admin123",
		SessionTTL:        time.Hour,
	}, slog.Default())
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	ctx := context.Background()
	if err := h.EnsureDefaultAdmin(ctx); err != nil {
		t.Fatal(err)
	}
	source, _, _, err := h.Nodes().RegisterOrUpdate(ctx, "source-agent", "source", "10.0.0.1", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	target, _, _, err := h.Nodes().RegisterOrUpdate(ctx, "target-agent", "target", "10.0.0.2", "linux", "amd64", "1", models.DeploymentModeNative, false, nil)
	if err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()
	login, err := http.Post(ts.URL+"/api/login", "application/json", strings.NewReader(`{"username":"admin","password":"admin123"}`))
	if err != nil {
		t.Fatal(err)
	}
	var session struct {
		Token string `json:"token"`
	}
	json.NewDecoder(login.Body).Decode(&session)
	login.Body.Close()
	req, err := http.NewRequest(http.MethodPost, ts.URL+"/api/transfers", strings.NewReader(`{"source_node_id":"`+source.ID+`","source_path":"/source","targets":[{"node_id":"`+target.ID+`","destination_path":"/target"}]}`))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+session.Token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var transfer models.FileTransfer
	json.NewDecoder(resp.Body).Decode(&transfer)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || transfer.ID == "" || transfer.Status != models.FileTransferPending {
		t.Fatalf("create transfer failed: status=%d transfer=%+v", resp.StatusCode, transfer)
	}
	unauth, err := http.Get(ts.URL + "/api/transfers")
	if err != nil {
		t.Fatal(err)
	}
	unauth.Body.Close()
	if unauth.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected unauthorized transfer list, got %d", unauth.StatusCode)
	}
}
