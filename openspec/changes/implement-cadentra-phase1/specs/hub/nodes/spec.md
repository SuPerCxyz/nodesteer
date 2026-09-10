## Purpose

Hub 管理 Agent Node 的注册、心跳、在线状态、Inventory、Capability、Label 与 Group，作为节点生命周期与同步调度的基础。

## ADDED Requirements

### Requirement: Agent 注册与身份
系统 SHALL 通过 Registration Token 支持 Agent 首次注册，注册成功后分配稳定的 Node ID 与唯一 Agent Credential，且不得以 Hostname 或 IP 作为永久唯一标识。

#### Scenario: 首次注册成功
- **WHEN** Agent 使用有效 Registration Token 连接 Hub 并完成 AUTH
- **THEN** Hub 为该 Agent 创建 Node 记录并分配稳定 Node ID 与 Agent Credential

#### Scenario: 无效注册凭证
- **WHEN** Agent 使用无效或过期 Registration Token
- **THEN** Hub 拒绝 AUTH 并关闭连接

### Requirement: Heartbeat 与在线状态
系统 SHALL 支持 Agent 周期性发送 Heartbeat，Hub 据此更新 Node 的 Last Seen、Online/Offline、Maintenance、Disabled 状态。

#### Scenario: 心跳更新在线状态
- **WHEN** Agent 在心跳间隔内发送 Heartbeat
- **THEN** Hub 更新 Node Last Seen 并将 Node 标记为 Online

#### Scenario: 心跳超时
- **WHEN** Agent 超过心跳超时阈值未发送 Heartbeat
- **THEN** Hub 将 Node 标记为 Offline

### Requirement: Inventory 采集
系统 SHALL 采集 OS Distribution/Version、Kernel、Architecture、CPU、Memory、Filesystem、Network 信息；Docker Agent 应尽量报告 Host Inventory，无法可靠获取时返回 UNAVAILABLE。

#### Scenario: Native Agent 上报完整 Inventory
- **WHEN** Native Agent 上报 Inventory
- **THEN** Hub 持久化其 Host OS、CPU、Memory、Filesystem、Network 数据

#### Scenario: Docker Agent 无法获取 Host Inventory
- **WHEN** Docker Agent 无法可靠获取 Host 信息
- **THEN** Agent 上报 UNAVAILABLE，不得用容器状态冒充 Host 状态

### Requirement: Capability 上报与校验
系统 SHALL 支持 Agent 在 HELLO/Heartbeat 中上报 Capability（script、local_scheduler、offline_execution、host_filesystem、managed_systemd、application_deploy），Hub 在下发 Task/Deployment 前校验 Required Capability，不满足则 BLOCKED。

#### Scenario: Capability 不满足
- **WHEN** Hub 要下发需要 managed_systemd 的 Task 到不具备该 Capability 的 Agent
- **THEN** Hub 将该 Task 状态置为 BLOCKED，不向该 Agent 下发执行

### Requirement: Node Label 管理
系统 SHALL 支持对 Node 添加/移除 Label（如 role=compute、env=production），并支持按 Label 查询节点。

#### Scenario: 添加与移除 Label
- **WHEN** 管理员为 Node 添加 Label 或移除 Label
- **THEN** 该 Node 的 Label 集合实时更新并反映在 API 与页面中

### Requirement: Node Group 管理
系统 SHALL 支持 Static Group 与 Single Label Group 两种 Group，支持 Group 成员增删。

#### Scenario: 创建 Static Group 并加入成员
- **WHEN** 管理员创建 Static Group 并添加成员 Node
- **THEN** Group 成员持久化并可被 Task 作为 Target 使用

#### Scenario: 创建 Label Group
- **WHEN** 管理员创建基于 Label 的 Group
- **THEN** Group 动态解析为所有含该 Label 的在线 Node

### Requirement: 节点启停管理
系统 SHALL 支持将 Node 置为 Disabled 或 Maintenance，Disabled 节点不得接收新执行。

#### Scenario: 禁用节点
- **WHEN** 管理员将 Node 置为 Disabled
- **THEN** Hub 不再向其下发新任务，且该状态在页面与 API 可见

### Requirement: 同步状态展示
系统 SHALL 记录并展示每个 Agent 的 Sync Status（Synced/Outdated/Error）与 Local Global Revision。

#### Scenario: 查看节点同步状态
- **WHEN** 查看 Node 详情
- **THEN** 展示当前 Global Revision、本地 Agent Revision 与同步状态

## ADDED Requirements

### Requirement: Node 列表与详情 API
系统 SHALL 提供 Node 列表与详情 API，至少包含 Node ID、Hostname、IP、OS、Architecture、Labels、Agent Version、Deployment Mode、Host Integration、Sync Status、Last Seen。

#### Scenario: 查询节点列表
- **WHEN** 调用 GET /api/nodes
- **THEN** 返回包含上述字段的 Node 列表
