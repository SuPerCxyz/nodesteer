# agent/core Specification

## Purpose
定义 Agent 在同步、执行、离线恢复和本地配置执行中的完整行为。
## Requirements
### Requirement: Atomic complete local desired state

The Agent SHALL apply objects and deletions in one local transaction and SHALL advance its revision only after the complete desired state is durable.

#### Scenario: Object no longer matches

- **WHEN** a sync response omits an object that was previously assigned to the Agent
- **THEN** the Agent removes the stale local object in the same transaction
- **AND** it stops any removed schedule

### Requirement: Reliable execution journal

The Agent SHALL persist every execution before starting work, retain unsynced results across restart, and mark them synced only after Hub acknowledgement.

#### Scenario: Hub unavailable during completion

- **WHEN** an execution finishes while Hub is unavailable
- **THEN** its result remains in the local Journal
- **AND** reconnect reconciliation retries it until acknowledged

### Requirement: Unified host and artifact safety

The Agent SHALL verify artifact HTTP status and SHA256 before installation, and SHALL apply path validation consistently for Native and Container HostAdapters.

#### Scenario: Invalid artifact

- **WHEN** an artifact download returns a non-success HTTP status or a mismatched SHA256
- **THEN** the Agent keeps the temporary file out of the install path
- **AND** the application deployment fails without installing the artifact

### Requirement: Persisted enrollment identity

The Agent SHALL load a preassigned Agent ID from generated configuration or environment variables when no persisted Agent ID exists, include it in the first HELLO, and persist the Hub-issued identity and credential after acceptance.

#### Scenario: Generated enrollment configuration

- **WHEN** an Agent starts with a preassigned Agent ID and registration token but no local identity database
- **THEN** its first HELLO includes the preassigned Agent ID and registration token
- **AND** after acceptance it persists the returned Node ID, Agent ID, and Agent Credential

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
系统 SHALL 使用 SQLite + WAL 持久化本地状态至 /var/lib/nodesteer/，Docker Agent 必须使用 Persistent Volume 保存全部状态（Identity、Revision、Scripts/Tasks、Schedules、Journal、Artifact Cache、Application State）。

#### Scenario: Docker Agent 重启不丢失状态
- **WHEN** Docker Agent 容器 restart/recreate
- **THEN** 所有持久化状态从 Persistent Volume 恢复

### Requirement: Cron Schedule 本地触发
系统 SHALL 支持 Cron 类型 Schedule 在 Agent Local Scheduler（execution_owner=agent）侧按标准 cron 表达式触发。

#### Scenario: Cron 本地到点
- **WHEN** Agent-owned Cron Schedule 到达触发时刻
- **THEN** Agent 本地执行并持久化 Journal，重连后上报

### Requirement: 同步事务
系统 SHALL 在一次同步中对脚本/任务/调度/应用进行事务性持久化。

#### Scenario: 同步中途失败
- **WHEN** 同步过程中任一条目写入失败
- **THEN** 整个同步回滚，本地不出现半同步状态，Revision 不前进

### Requirement: READY 门禁
系统 SHALL 在 Reconnect Reconciliation 完成前不将 Agent 视为就绪。

#### Scenario: 对账完成前
- **WHEN** Agent 重连后配置对账尚未完成
- **THEN** Agent 不被标记 READY，不接收新任务

### Requirement: 周期 Jitter
系统 SHALL 为 Heartbeat 与 Revision 周期校验加入随机抖动。

#### Scenario: 抖动生效
- **WHEN** 多个 Agent 同时运行
- **THEN** 各 Agent 的周期检查时间随机偏移，避免同步冲击

### Requirement: Realtime Log
系统 SHALL 在执行期间将 stdout/stderr 分片实时上报 Hub。

#### Scenario: 执行产生日志
- **WHEN** 执行产生日志且 Hub 在线
- **THEN** Agent 分片发送 LOG_CHUNK，Hub 记录于 Execution

#### Scenario: Hub 离线
- **WHEN** Hub 离线
- **THEN** 本地日志仍完整记录，重连后整包上报

### Requirement: Task Retry
系统 SHALL 在 Task 执行失败时按 Retry 次数重试。

#### Scenario: 失败重试
- **WHEN** 执行失败且 Retry > 0
- **THEN** Agent 在失败后重新执行，最多 Retry 次

### Requirement: Manual 执行幂等
系统 SHALL 按 Execution ID 去重 Manual Run，且同一 Task+Node 的多次手动执行均生效。

#### Scenario: 同一任务多次手动执行
- **WHEN** 同一 Task 在同一 Node 连续手动执行
- **THEN** 每次生成独立执行记录，互不阻塞

### Requirement: 总日志上限
系统 SHALL 同时限制 stdout、stderr 与两者合计的最大字节数。

#### Scenario: 超过总上限
- **WHEN** 日志总量超过配置上限
- **THEN** 截断并标记 Truncated，不无限增长

### Requirement: WorkingDir 与 RunUser
系统 SHALL 将 Script/Task 的 WorkingDir 与 RunUser 传入进程执行。

#### Scenario: 工作目录生效
- **WHEN** Script 配置 WorkingDir
- **THEN** 进程在指定目录启动

### Requirement: Remote Condition 查询
系统 SHALL 在评估 Remote Node Condition 时向 Hub 发起查询并获取真实状态。

#### Scenario: 远程条件求值
- **WHEN** 任务配置远程条件
- **THEN** Agent 通过 Hub 获取目标节点状态，未过期时正常求值，否则 Fail Closed

### Requirement: 实时日志流标记
系统 SHALL 正确标记 Realtime Log 分片的 stream（stdout/stderr）。

#### Scenario: stderr 分片
- **WHEN** 进程向 stderr 输出且触发分片上报
- **THEN** LOG_CHUNK 的 stream 字段为 "stderr"
- **AND** stdout 输出 stream 为 "stdout"

### Requirement: Inventory 完整性
系统 SHALL 采集 OS Version 与 CPU MHz。

#### Scenario: Inventory 上报
- **WHEN** Agent 采集并上报 Inventory
- **THEN** os_version 非空、cpu[].mhz 反映真实频率（无法获取时返回 0）
- **AND** 无法可靠获取 OS 版本时标记 UNAVAILABLE

### Requirement: 回滚完整性
系统 SHALL 在升级失败回滚时恢复 config/unit 并执行回滚后 Health Check。

#### Scenario: 健康检查失败回滚
- **WHEN** 新版本 Health Check 失败
- **THEN** 恢复 Previous 二进制、config、unit，daemon-reload 后启动 Previous
- **AND** 对 Previous 执行 Health Check


### Requirement: Agent 自助升级

系统 SHALL 支持对 native 部署的 Agent 执行自助升级（单节点或批量）：Hub 下发升级执行并记录于执行历史；Agent 下载 Hub 提供的当前构建二进制、校验 SHA256、备份当前可执行文件、替换自身并重启服务；任一失败步骤 SHALL 回滚到备份版本并回报失败。升级完成重连后 Agent SHALL 上报新版本。docker 部署形态 SHALL 不提供自升级（升级入口隐藏），由镜像更新流程处理。版本粒度为「升至 Hub 同版本」。

#### Scenario: 单节点升级

- **WHEN** 管理员对 native 部署节点发起升级
- **THEN** Hub 创建升级执行并下发，执行历史可见该记录与终态
- **AND** Agent 下载二进制并通过 SHA256 校验后替换自身、重启服务

#### Scenario: 升级失败回滚

- **WHEN** 升级过程中下载校验失败或新二进制启动异常
- **THEN** Agent 恢复备份的旧二进制并保持服务可用
- **AND** 执行记录为失败并含失败原因

#### Scenario: 升级后版本上报

- **WHEN** 升级成功且 Agent 以新版本重连
- **THEN** HELLO 上报的 `agent_version` 更新为新版本，节点详情同步显示

#### Scenario: docker 形态不提供升级

- **WHEN** 节点为 docker 部署形态
- **THEN** 升级入口不出现，不下发升级执行

#### Scenario: 批量升级

- **WHEN** 管理员多选多个 native 节点执行批量升级
- **THEN** 逐节点创建独立升级执行，各自产出成功/失败终态与原因
