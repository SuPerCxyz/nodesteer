## Purpose

Agent Core 负责主动连接 Hub、注册认证、心跳、Desired State 同步、Local Scheduler、Condition Engine 与 Inventory，是 Native 与 Docker 共用的大脑。

## ADDED Requirements

### Requirement: Agent 主动连接
系统 SHALL 支持 Agent 主动向 Hub Agent Gateway 建立持久连接；Agent 默认不开放远程管理端口，不依赖 Hub SSH 登录节点。

#### Scenario: Agent 主动连接
- **WHEN** Agent 启动且 Hub 可达
- **THEN** Agent 主动建立 WebSocket 连接并完成 AUTH/HELLO

#### Scenario: Hub 不可达重连
- **WHEN** Hub 临时不可达
- **THEN** Agent 按退避策略持续重连且不影响本地 Scheduler 继续工作

### Requirement: Registration 与 Agent Credential
系统 SHALL 支持使用 Registration Token 首次注册并获取唯一 Agent Credential；Agent Identity 持久化，重启后使用已注册身份。

#### Scenario: 首次注册
- **WHEN** Agent 首次启动并使用 Registration Token 注册
- **THEN** Agent 持久化 Node ID 与 Credential，后续重启使用该身份

### Requirement: Heartbeat 与周期 Revision 校验
系统 SHALL 周期性发送 Heartbeat 与本地 Global Revision；Heartbeat 与 Revision Check 是独立机制。

#### Scenario: 心跳与校验
- **WHEN** Agent 在线运行
- **THEN** Agent 周期发送 Heartbeat 与 Revision Check

### Requirement: Desired State 同步与本地持久化
系统 SHALL 支持获取 Hub Desired State、原子写入本地 SQLite 并更新本地 Revision；Revision 只有在配置成功持久化后才能前进；本地保存 Identity、sync_state、Scripts、Tasks、Schedules、Applications、Artifacts、Executions、Deployments。

#### Scenario: 增量同步
- **WHEN** 收到 Change Notification 或周期校验发现不一致
- **THEN** Agent 发起增量 Sync 并原子提交本地状态

#### Scenario: 同步失败回滚
- **WHEN** Sync 中任一步失败
- **THEN** Agent 回滚本地事务，Revision 不前进

### Requirement: 重连 Reconciliation
系统 SHALL 在每次重连后依次执行配置对账与执行对账，完成前不得视为 READY。

#### Scenario: 重连对账
- **WHEN** Agent 重连成功
- **THEN** 依次执行 CONFIG RECONCILIATION 与 EXECUTION RECONCILIATION 后进入 READY

### Requirement: Local Scheduler
系统 SHALL 内置 Local Scheduler（不依赖 /etc/cron.d、crontab、systemd timer），支持 Cron/Interval/One-Time、IANA Timezone、Misfire（SKIP/RUN_ONCE，默认 RUN_ONCE）与 Offline Policy。

#### Scenario: Cron 调度触发
- **WHEN** 到达 Cron Schedule 的触发时间且任务允许
- **THEN** Agent 本地触发 Execution

#### Scenario: 错过触发
- **WHEN** Agent 停机期间错过触发时间
- **THEN** 按 Misfire Policy 处理（RUN_ONCE 或 SKIP）

### Requirement: Execution Owner 语义
系统 SHALL 支持 execution_owner=AGENT 的 Schedule 仅由 Agent Local Scheduler 触发，禁止 Hub 在同一 Slot 重复发送 RUN_EXECUTION。

#### Scenario: Agent 自有调度不双触发
- **WHEN** Schedule 的 execution_owner=AGENT
- **THEN** Hub 不在执行时间发送 RUN_EXECUTION，仅由 Agent 触发

### Requirement: Condition Engine
系统 SHALL 提供本地 Condition Engine（CPU、Memory、Disk、File Exists、Directory Exists、Process Exists、Port Listening、Command Result）支持 AND 与 ==/!=/>/</>=/<= 比较，通过 State Provider 抽象统一 Native/Docker。

#### Scenario: Condition 满足
- **WHEN** 本地 Condition 全部满足
- **THEN** Execution 正常执行

#### Scenario: Condition 不满足
- **WHEN** 本地 Condition 任一不满足
- **THEN** Execution 状态为 SKIPPED

### Requirement: Remote Node Condition
系统 SHALL 支持基础 Remote Node Condition（如 node02==ONLINE、node02.last_execution(task-x)==SUCCESS），远程状态包含 Value、Observed At、TTL；过期或未知时返回 UNKNOWN 并默认 BLOCKED。

#### Scenario: 远程条件过期
- **WHEN** Remote State 超出 TTL 无法确认
- **THEN** Condition 视为 UNKNOWN，Execution 置为 BLOCKED 而不是执行

### Requirement: Inventory 采集
系统 SHALL 支持采集 Host Inventory（OS/Kernel/Architecture/CPU/Memory/Filesystem/Network）；Docker Agent 应报告 Host Inventory 而非容器状态，无法可靠获取时返回 UNAVAILABLE。

#### Scenario: Native Inventory
- **WHEN** Native Agent 采集 Inventory
- **THEN** 上报真实 Host 数据

#### Scenario: Docker 无法获取 Host 数据
- **WHEN** Docker Agent 无法获取 Host 数据
- **THEN** 上报 UNAVAILABLE

### Requirement: Capability 声明
系统 SHALL 在 HELLO/Heartbeat 中声明 Capability，并拒绝接收自身不具备 Capability 的 Host Operation。

#### Scenario: 拒绝不具备能力的任务
- **WHEN** Agent 收到需要 managed_systemd 的部署任务但自身不支持
- **THEN** Agent 拒绝执行并上报 BLOCKED

### Requirement: Agent 本地存储
系统 SHALL 使用 SQLite + WAL 持久化本地状态至 /var/lib/cadentra/，Docker Agent 必须使用 Persistent Volume 保存全部状态（Identity、Revision、Scripts/Tasks、Schedules、Journal、Artifact Cache、Application State）。

#### Scenario: Docker Agent 重启不丢失状态
- **WHEN** Docker Agent 容器 restart/recreate
- **THEN** 所有持久化状态从 Persistent Volume 恢复
