package metrics

import (
	"fmt"
	"sync"
)

// Metrics 基础 Prometheus 指标
type Metrics struct {
	mu sync.Mutex
	// connectedAgents 在线 Agent 数（由 Hub 设置）
	connectedAgents int
	// syncErrors 同步错误计数
	syncErrors int64
	// executionTotal 执行总数
	executionTotal map[string]int64
	// artifactDownloadBytes 制品下载字节
	artifactDownloadBytes int64
}

// New 创建指标收集器
func New() *Metrics {
	return &Metrics{executionTotal: map[string]int64{}}
}

// SetConnectedAgents 设置在线 Agent 数
func (m *Metrics) SetConnectedAgents(n int) {
	m.mu.Lock()
	m.connectedAgents = n
	m.mu.Unlock()
}

// IncSyncErrors 同步错误 +1
func (m *Metrics) IncSyncErrors() {
	m.mu.Lock()
	m.syncErrors++
	m.mu.Unlock()
}

// AddExecution 记录执行结果
func (m *Metrics) AddExecution(status string) {
	m.mu.Lock()
	m.executionTotal[status]++
	m.mu.Unlock()
}

// AddArtifactBytes 累计下载字节
func (m *Metrics) AddArtifactBytes(n int64) {
	m.mu.Lock()
	m.artifactDownloadBytes += n
	m.mu.Unlock()
}

// Render 渲染 Prometheus 文本格式
func (m *Metrics) Render() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out string
	out += fmt.Sprintf("# HELP nodesteer_connected_agents Number of connected agents\n")
	out += fmt.Sprintf("# TYPE nodesteer_connected_agents gauge\n")
	out += fmt.Sprintf("nodesteer_connected_agents %d\n", m.connectedAgents)

	out += "# HELP nodesteer_sync_errors Total sync errors\n"
	out += "# TYPE nodesteer_sync_errors counter\n"
	out += fmt.Sprintf("nodesteer_sync_errors %d\n", m.syncErrors)

	out += "# HELP nodesteer_execution_total Total executions by status\n"
	out += "# TYPE nodesteer_execution_total counter\n"
	for status, n := range m.executionTotal {
		out += fmt.Sprintf("nodesteer_execution_total{status=%q} %d\n", status, n)
	}
	out += fmt.Sprintf("nodesteer_execution_failed %d\n", m.executionTotal["FAILED"])
	out += fmt.Sprintf("nodesteer_execution_total_all %d\n", total(m.executionTotal))
	out += fmt.Sprintf("nodesteer_active_executions %d\n", m.executionTotal["RUNNING"])

	out += "# HELP nodesteer_artifact_download_bytes Artifact download bytes\n"
	out += "# TYPE nodesteer_artifact_download_bytes counter\n"
	out += fmt.Sprintf("nodesteer_artifact_download_bytes %d\n", m.artifactDownloadBytes)
	return out
}

func total(m map[string]int64) int64 {
	var t int64
	for _, v := range m {
		t += v
	}
	return t
}
