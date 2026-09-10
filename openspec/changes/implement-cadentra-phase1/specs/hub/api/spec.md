## Purpose

Hub 暴露 REST API、Agent Gateway（WebSocket 长连接）、可观测性端点并托管 Web UI，是 Hub 面向 Web 用户与 Agent 的统一入口。

## ADDED Requirements

### Requirement: REST API
系统 SHALL 提供 API-first 的 REST 接口，至少覆盖 /api/nodes、/api/groups、/api/scripts、/api/tasks、/api/schedules、/api/artifacts、/api/applications、/api/executions、/api/settings，并支持认证与鉴权。

#### Scenario: 鉴权访问 API
- **WHEN** 无凭证或凭证无权限的请求访问受保护 API
- **THEN** 系统返回 401 或 403

#### Scenario: 调用节点 API
- **WHEN** 带有效凭证调用 GET /api/nodes
- **THEN** 返回节点列表 JSON

### Requirement: Agent Gateway
系统 SHALL 提供独立的 Agent Gateway（如 :8443）承载 WebSocket 长连接，负责 Agent Auth、Heartbeat、Realtime Notification、Task Dispatch、Execution Status、Realtime Logs 与 Sync Protocol。

#### Scenario: Agent 建立长连接
- **WHEN** Agent 通过 Gateway 建立 WebSocket 连接并完成认证
- **THEN** 建立持久双向通道并注册 Session

#### Scenario: 会话状态仅内存维护
- **WHEN** Hub 重启
- **THEN** 内存 Session 丢失但不影响 Desired State 与 Execution 持久化数据，Agent 重连后恢复

### Requirement: Agent Protocol
系统 SHALL 使用版本化 JSON Envelope 协议（type、id、timestamp、payload），支持 HELLO、HEARTBEAT、CHANGE_NOTIFICATION、REVISION_CHECK、SYNC_REQUEST、SYNC_RESPONSE、SYNC_ACK、RUN_EXECUTION、CANCEL_EXECUTION、EXECUTION_STARTED、EXECUTION_FINISHED、LOG_CHUNK、ARTIFACT_PREFETCH。

#### Scenario: HELLO 握手
- **WHEN** Agent 连接后发送 HELLO（含 protocol_version、agent_version、deployment_mode、host_integration、capabilities）
- **THEN** Hub 校验协议版本并继续对账流程

#### Scenario: 协议版本不匹配
- **WHEN** Agent 发送的 protocol_version 不被 Hub 支持
- **THEN** Hub 拒绝连接并提示版本不兼容

### Requirement: 可观测性
系统 SHALL 提供结构化日志、/healthz、/readyz 与基础 Prometheus Metrics（connected_agents、sync_errors、execution_total、execution_failed、active_executions、artifact_download_bytes）。

#### Scenario: 健康检查
- **WHEN** 调用 /healthz
- **THEN** 返回 200 表示进程存活

#### Scenario: 就绪检查
- **WHEN** 调用 /readyz
- **THEN** 返回 200 表示可接收流量

### Requirement: Web UI 服务
系统 SHALL 托管 Web UI（Dashboard、Nodes、Groups、Scripts、Tasks、Schedules、Applications、Artifacts、Executions、Settings），所有页面数据必须来自真实 API，不允许永久 Fake Data。

#### Scenario: 页面加载真实数据
- **WHEN** 打开 Nodes 页面
- **THEN** 页面从真实后端 API 拉取并展示节点数据

#### Scenario: 空数据页面
- **WHEN** 打开无数据的页面
- **THEN** 页面显示空状态而非报错或假数据
