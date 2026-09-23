# NodeSteer 完整产品功能文档

版本：v1.1
文档类型：产品功能设计基线
产品阶段：一期实施范围 + 后续高级能力规划

## 1. 产品定位

NodeSteer 是一套面向 Linux 服务器的轻量级 Hub-Agent 自动化管理、任务调度与二进制应用部署平台。

系统由 Hub 和 Agent 两部分组成。

Hub 提供：
- Web 管理
- 节点与节点组管理
- Script 管理
- Task 管理
- Schedule 管理
- Condition
- Hub → Agent 自动同步
- Artifact 管理
- Managed Application 管理
- Execution 历史与日志
- 用户权限
- Audit
- REST API

Agent 负责：
- 主动连接 Hub
- 注册与 Heartbeat
- 自动同步 Hub Desired State
- 本地缓存 Script/Task/Schedule/Application
- Script/Command 执行
- Local Scheduler
- Hub 故障期间 Offline Execution
- Execution Journal
- Artifact 下载与 Cache
- 二进制部署
- Managed systemd 生命周期
- Health Check
- Basic Rollback
- Hub 恢复后的 Reconciliation

核心定位：

> Hub 集中定义自动化意图，Agent 自动可靠同步并执行；即使 Hub 临时不可用，已同步且明确允许离线执行的自动化仍可安全继续运行。

---

## 2. 一期核心目标

一期必须实现：

1. Hub Web 管理
2. Hub Docker / Docker Compose 部署
3. Agent Native 部署
4. Agent Docker 部署
5. Docker Agent Host Integration 模式
6. Agent 主动连接 Hub
7. Agent 注册、Heartbeat、基础 Inventory
8. Node / Group / Label 管理
9. Script Library 与 Revision
10. Task 管理与 Revision
11. Manual Run
12. Cron / Interval / One-Time Schedule
13. Agent Local Scheduler
14. Hub 故障期间 Offline Execution
15. Local Condition
16. 基础 Remote Node Condition
17. 长连接实时变更通知
18. Revision 周期校验
19. Reconnect Reconciliation
20. Tombstone 删除同步
21. Execution 幂等
22. Execution Journal
23. Realtime / Offline Logs
24. Artifact 管理与 Prefetch
25. Managed Application
26. Binary Deployment
27. 平台托管 systemd
28. Health Check
29. Basic Rollback
30. Administrator / Operator / Viewer
31. Basic Audit
32. REST API

---

## 3. 产品设计原则

### 3.1 Hub 是 Desired State 唯一权威源

Hub 管理：
- Script
- Task
- Schedule
- Application Definition
- Assignment
- Node Configuration

Agent 只保存 Hub 下发的本地副本。

Agent 不允许永久自行修改 Hub 管理的配置。

### 3.2 Agent 主动连接 Hub

统一通信模型：

```text
Agent ─────────────→ Hub
```

连接建立后为双向控制通道：

```text
Hub ⇄ Agent
```

Agent 默认不需要开放远程管理端口，也不依赖 Hub SSH 登录节点。

### 3.3 Hub 不是离线 Schedule 的单点

Allow Offline 的 Schedule 必须提前同步到 Agent，由 Agent Local Scheduler 负责触发。

### 3.4 同步三层保障

一期必须同时实现：

> 长连接实时通知 + Revision 周期校验 + Reconnect Reconciliation

Realtime Notification 只解决速度；Revision 与 Reconciliation 解决最终一致性。

### 3.5 Fail Closed

Remote State 过期或不可确认时：

```text
UNKNOWN → BLOCKED
```

不得把 UNKNOWN 当作 TRUE。

### 3.6 systemd 管理边界

NodeSteer 不是通用 systemd 管理器。

只管理由 NodeSteer Managed Application 创建、登记并托管的 systemd Unit。

外部 systemd 服务仅可通过普通 Command 或 Condition Probe 查询。

---

## 4. Hub 部署

一期必须提供：

```text
Dockerfile
docker-compose.yml
.env.example
```

并支持：

```bash
docker compose up -d
```

小规模模式：
- Hub
- SQLite
- Local Artifact Storage

正式环境推荐：
- Hub
- PostgreSQL
- Persistent Artifact Volume

一期不得强制依赖：
- Kubernetes
- Redis
- RabbitMQ
- Kafka
- Elasticsearch

---

## 5. Agent 部署模式

### 5.1 Native Mode

推荐生产部署方式：

```text
/usr/local/bin/nodesteer-agent
/etc/nodesteer/agent.yaml
/var/lib/nodesteer/
/etc/systemd/system/nodesteer-agent.service
```

Native 模式支持完整宿主机管理能力。

节点纳管向导不要求手动选择架构。Native 命令在目标机通过 `uname -m` 判断 x86/amd64 或 ARM/arm64，从 Hub 的公开只读二进制端点下载对应 Agent，校验 SHA256 后安装并启动；首次注册仍使用命令生成的 Registration Token。标准 Hub 构建把 Agent payload 直接打包进 Hub 可执行文件，未打包的本地开发构建才使用配置的固定路径；目标架构 payload 缺失时命令执行失败并给出错误。

### 5.2 Docker Mode

Agent 支持 Docker / Docker Compose 正式部署。

必须持久化：

```text
/var/lib/nodesteer
```

容器 restart/recreate 后不得丢失：
- Agent Identity
- Revision
- Script/Task Local State
- Schedule
- Execution Journal
- Artifact Cache
- Application State

基础 Docker 模式至少支持：
- Hub Communication
- Heartbeat
- Revision Sync
- Script/Task
- Local Scheduler
- Offline Journal
- Artifact Cache

### 5.3 Docker Host Integration Mode

需要宿主机完整能力时显式启用 Host Integration。

通过统一 Host Adapter：

```text
HostAdapter
├── NativeHostAdapter
└── ContainerHostAdapter
```

业务层不得维护 Native/Docker 两套逻辑。

Docker Host Integration 可以访问必要的：
- Host binary path
- Host config path
- Host systemd unit path
- Host systemd/DBus interface

不应把 `privileged=true` 作为唯一默认方案。

优先：
- 最小目录挂载
- 最小 Capability
- Host Path Allowlist
- Path Validation
- 明确 systemd/DBus 接入

Host Integration 应被视为 High Trust Host Access。

---

## 6. Agent Capability

Agent 注册和 Heartbeat 上报 Capability，例如：

```text
script=true
local_scheduler=true
offline_execution=true
host_filesystem=true
managed_systemd=true
application_deploy=true
```

Docker 基础模式可以：

```text
managed_systemd=false
application_deploy=false
```

Hub 在 Task/Deployment 下发前校验 Required Capability。

能力不满足：

```text
BLOCKED
```

不能等执行后才失败。

---

## 7. 网络模型

一期支持：

| Hub | Agent | 支持 |
|---|---|---|
| 内网 | 同一内网 | 是 |
| 公网 | 内网 | 是 |
| 公网 | 公网 | 是 |
| 内网 | 公网 | 是，Agent Gateway 需可达 |
| 内网 A | 内网 B | 网络本身已互通时支持 |
| 隔离内网 A | 隔离内网 B | 一期不负责打通 |

Hub 内网 + 公网 Agent：

```text
Public Agent
     ↓
NAT / Reverse Proxy / Public Entry
     ↓
Hub Agent Gateway
```

Hub Web UI 可以继续仅暴露在内网。

一期不实现：
- P2P
- Hole Punching
- Overlay Network
- Agent Mesh
- Multi-Hop Proxy

一期支持 Hub-mediated File Relay：源 Agent 通过 Agent Gateway 上传文件，Hub 校验并持久化后，由目标 Agent 通过 Agent Gateway 下载；不要求 Agent 之间直连。

---

## 8. Web Endpoint 与 Agent Gateway

推荐独立配置：

```text
Web/API:       10.0.0.10:8080
Agent Gateway: 0.0.0.0:8443
```

允许只将 Agent Gateway 映射到公网。

Web/API 与 Agent Gateway 均支持可配置 TLS；启用 TLS 后 Agent 使用 `wss://` 连接，Artifact 下载使用对应的 `https://` Base URL。

---

## 9. Hub-Agent 长连接

一期核心。

用于：
- Heartbeat
- Change Notification
- Manual Task Dispatch
- Task Cancel
- Execution State
- Realtime Log
- Sync Message

实现可采用 WebSocket 或 gRPC Stream。

---

## 10. Desired State 与 Revision

每个重要对象拥有 Object Revision：
- Script Revision
- Task Revision
- Schedule Revision
- Application Revision

Hub 同时维护 Global State Revision。

Object Revision 用于版本、审计和 Execution 绑定。

Global Revision 用于判断 Agent 整体同步状态。

---

## 11. 长连接实时通知

Hub 修改 Agent Desired State 后：

```text
DB Transaction
 ↓
Commit
 ↓
Object Revision +1
Global Revision +1
 ↓
Notify Related Agents
```

通知只传：
- Object Type
- Object ID
- Object Revision
- Global Revision

不直接把完整配置作为通知可靠载荷。

通知必须天然允许：
- 重复
- 丢失
- 乱序
- 延迟

最终状态由 Hub Current Desired State 决定。

---

## 12. Revision 周期校验

Agent 周期比较：

```text
Local Global Revision
VS
Hub Desired Global Revision
```

不一致时执行增量 Sync。

周期可配置，建议默认 30~60 秒并加入 Jitter。

---

## 13. Reconnect Reconciliation

Agent 每次重连：

```text
CONNECT
 ↓
AUTH
 ↓
HELLO
 ↓
REPORT LOCAL REVISION
 ↓
CONFIG RECONCILIATION
 ↓
EXECUTION RECONCILIATION
 ↓
READY
```

对账完成之前不得视为 READY。

---

## 14. Revision Change Log

Hub 保存轻量 Change Log：

```text
global_revision
object_type
object_id
object_revision
operation
created_at
```

支持：

```text
changes since <revision>
```

Change Log 只需保留合理窗口。

Agent Revision 超出窗口：

```text
FULL_RESYNC_REQUIRED
```

---

## 15. Tombstone

删除 Script/Task/Schedule/Application 时必须产生 Tombstone 或等价删除记录。

Agent 收到后：
- 停止未来调度
- 删除本地 Desired Definition
- 保留历史 Execution

---

## 16. Atomic Sync

Agent Revision 只有配置成功校验并持久化后才能前进。

例如 Task 依赖 Script：

```text
Fetch Task
Fetch Script
Validate
 ↓
BEGIN
Write Script
Write Task
Update Scheduler
Update Revision
COMMIT
```

任何失败：

```text
ROLLBACK
```

不得出现半同步状态。

---

## 17. Node 管理

一期支持：
- Registration
- Heartbeat
- Online/Offline
- Maintenance
- Disabled
- Inventory
- Labels
- Groups
- Sync Status
- Deployment Mode
- Capability

节点页面提供管理员纳管向导。管理员必须指定节点名称和节点地址（IPv4/IPv6 或 DNS 域名），Hub 地址默认使用当前 Web 页面地址；向导生成的 Native、Docker 和 Compose 命令均包含节点身份及 Registration Token。

Node 页面显示至少：
- Node ID
- Hostname
- IP 或域名
- OS
- Architecture
- Labels
- Agent Version
- Deployment Mode
- Host Integration
- Sync Status
- Last Seen

---

## 18. Inventory

一期采集：
- OS Distribution / Version
- Kernel
- Architecture
- CPU
- Memory
- Filesystem
- Network

Docker Agent 必须尽量报告 Host Inventory，而不是把 Container 状态伪装成 Host 状态。

无法可靠获得时返回：

```text
UNAVAILABLE
```

---

## 19. Node Labels 与 Groups

Labels：

```text
role=compute
cluster=openstack-a
env=production
az=az01
storage=fc
```

一期 Groups：
- Static Group
- Single Label Group

复杂动态表达式属于后续。

---

## 20. Script Library

一期支持：
- Shell
- Bash
- Python

字段：
- Name
- Description
- Interpreter
- Content
- Parameters
- Environment
- Working Directory
- Timeout
- Enabled
- Revision
- SHA256

每次编辑产生新 Revision，并保留 Revision History。

Agent 同步时：

```text
Fetch
 ↓
SHA256
 ↓
Write Temp
 ↓
Atomic Rename
 ↓
Commit Revision
```

---

## 21. Task

一期字段：
- Name
- Type
- Target
- Parameters
- Condition
- Schedule
- Timeout
- Retry
- Offline Policy
- Enabled
- Revision

一期 Task 类型：
- Command
- Script
- Application Deploy
- Application Operation

Application Operation：
- Start
- Stop
- Restart
- Upgrade

Target：
- Single Node
- Multiple Nodes
- Node Group
- Label

---

## 22. Manual Run

Web 提供 Run Now。

流程：

```text
Hub Create Execution UUID
 ↓
Persist PENDING
 ↓
Dispatch
 ↓
Agent Dedup
 ↓
Execute
```

一期 Agent Offline 时 Manual Run 不进入长期等待队列，明确返回不可执行。

---

## 23. Schedule

一期支持：
- Cron
- Interval
- One-Time

必须保存 IANA Timezone。

Local Scheduler 由 NodeSteer Agent 内置实现，不依赖 `/etc/cron.d`、crontab 或 systemd timer。

---

## 24. Schedule Execution Owner

每个 Schedule 明确：

```text
HUB
或
AGENT
```

Allow Offline：

```text
execution_owner=AGENT
```

Hub Online Required：

```text
execution_owner=HUB
```

同一 Schedule Slot 禁止 Hub 与 Agent 双重触发。

---

## 25. Offline Policy

一期：
- Hub Online Required
- Allow Offline

可选：
- Max Offline Duration

---

## 26. Misfire

一期：
- SKIP
- RUN_ONCE

默认推荐：

```text
RUN_ONCE
```

---

## 27. Condition Engine

一期保持轻量。

支持 AND 与：
- ==
- !=
- >
- <
- >=
- <=

Local Condition：
- CPU Usage
- Memory Usage
- Disk Usage
- File Exists
- Directory Exists
- Process Exists
- Port Listening
- Command Result

外部 systemd 服务可以通过：

```text
systemctl is-active <service>
```

作为 Condition Probe，但不自动纳入 Managed Application。

---

## 28. Remote Node Condition

一期支持基础：
- `node02 == ONLINE`
- `node02.last_execution(task-x) == SUCCESS`

Remote State 必须包含：
- Value
- Observed At
- TTL

过期：

```text
UNKNOWN
```

默认：

```text
BLOCKED
```

---

## 29. Execution

一期记录：
- Execution ID
- Task ID
- Task Revision
- Script Revision
- Node ID
- Trigger Type
- Scheduled Time
- Start Time
- End Time
- Status
- Exit Code
- stdout
- stderr
- Offline

状态：
- PENDING
- RUNNING
- SUCCESS
- FAILED
- SKIPPED
- CANCELED
- TIMED_OUT

---

## 30. Execution 幂等

Scheduled Execution 使用稳定逻辑 Key：

```text
Task ID
+
Node ID
+
Scheduled Time
```

相同 Schedule Slot 只允许执行一次。

Manual Run 使用 Hub 创建的 Execution UUID，Agent 按 Execution ID 去重。

---

## 31. Execution Journal

Agent 启动执行前必须先持久化 Journal：

```text
INSERT RUNNING
 ↓
Start Process
```

完成后更新结果。

Hub Offline 时：

```text
synced=false
```

Hub 恢复后：

```text
Upload
 ↓
Hub Idempotent Upsert
 ↓
ACK
 ↓
synced=true
```

Docker Agent 的 Journal 必须位于 Persistent Volume。

---

## 32. Process Runner 与日志

Process Runner 支持：
- Working Directory
- Environment
- Run User
- Process Group
- stdout
- stderr
- Timeout

Timeout：

```text
SIGTERM Process Group
 ↓
Grace Period
 ↓
SIGKILL Process Group
```

本地日志始终是可靠源。

Hub 在线时同时 Realtime Stream。

Hub 离线不影响本地日志。

必须限制最大 stdout/stderr/total log。

---

## 33. Artifact

中文 UI 术语：发布包。

Artifact 是不可变对象。

字段：
- ID
- Name
- Version
- Architecture
- Filename
- Size
- SHA256
- Storage Location

一期支持：
- amd64
- arm64

Binary 不通过控制长连接传输。

采用：

```text
Control Channel → Metadata / Prefetch
HTTP(S)          → Binary Payload
```

Agent Cache：

```text
artifacts/<sha256>
```

下载先写 `.tmp`，校验成功后 Atomic Rename。

---

## 34. Artifact Prefetch

Hub 可提前发送 PREFETCH，让 Agent 在真正执行前下载并校验 Artifact。

用于：
- Scheduled Application Deployment
- Offline Deployment

---

## 34.1 Agent 文件中继

管理员可以指定一个源 Agent、源文件路径、多个目标 Agent 和目标路径。文件先上传到 Hub，Hub 在校验大小和 SHA256 后才允许目标 Agent 下载。

传输必须支持：
- 每个目标独立状态
- Agent 离线后的持久化重试
- 临时文件、校验和原子替换
- 管理员取消与失败重试
- Agent 身份认证、路径策略和文件大小限制

二进制使用 Agent Gateway 的 HTTPS 数据端点，控制长连接只传输元数据和状态。

---

## 35. Managed Application

中文 UI 术语：托管应用。

Managed Application 是由 NodeSteer 主动部署并负责生命周期的 Linux 二进制应用。

包含：
- Name
- Version
- Artifact
- Binary Path
- Arguments
- Environment
- Configuration
- systemd Unit
- Health Check

Application Definition Sync 不等于自动 Deploy/Upgrade。

Definition 更新后 Agent 可以立即同步和 Prefetch，但真正部署由 Manual 或 Schedule 触发。

---

## 36. Native / Docker Host Adapter

统一：

```text
Application Manager
 ↓
HostAdapter
 ├── NativeHostAdapter
 └── ContainerHostAdapter
```

Application Definition 永远使用 Host Logical Path，例如：

```text
/usr/local/bin/foo
```

Native：

```text
HOST_ROOT=/
```

Docker：

```text
HOST_ROOT=/host
```

ContainerHostAdapter 映射为：

```text
/host/usr/local/bin/foo
```

业务层不得维护两套路径。

---

## 37. Managed systemd

Managed Unit 必须与 Application ID 建立 Registry。

Application API 只能操作已登记 Unit。

支持：
- Install Unit
- Update Unit
- daemon-reload
- enable
- disable
- start
- stop
- restart
- status

不提供宿主机全部 systemd 服务管理页面。

---

## 38. Application Deployment

流程：

```text
Resolve Artifact
 ↓
Download / Cache
 ↓
SHA256 Verify
 ↓
Prepare
 ↓
Install Binary
 ↓
Install Config
 ↓
Install Unit
 ↓
daemon-reload
 ↓
enable
 ↓
start
 ↓
Health Check
```

---

## 39. Application Upgrade

流程：

```text
Prefetch
 ↓
Verify
 ↓
Backup Current
 ↓
Stop
 ↓
Atomic Replace
 ↓
Update Config
 ↓
Update Unit
 ↓
daemon-reload if required
 ↓
Start
 ↓
Health Check
```

跨文件系统时不得假设 `rename()` 原子。

Docker Host Integration 的临时文件应创建在目标 Host Filesystem 上。

---

## 40. Health Check

一期支持：
- SYSTEMD
- TCP
- HTTP
- COMMAND

---

## 41. Basic Rollback

升级前至少保留一个 Previous。

Health Check 失败：

```text
Stop New
 ↓
Restore Previous Binary
 ↓
Restore Previous Config/Unit
 ↓
daemon-reload
 ↓
Start Previous
 ↓
Health Check
```

---

## 42. Deployment Journal

Application Deployment 必须单独持久化事务状态。

字段示例：
- deployment_id
- application_id
- from_version
- to_version
- phase
- backup_path
- started_at

Phase：
- PREPARING
- STOPPED
- REPLACED
- STARTED
- VERIFYING
- DONE
- ROLLING_BACK

Agent Crash/Restart 后必须识别半完成部署并恢复到明确状态。

---

## 43. Web UI

一期必须包含：

```text
Dashboard
Nodes
Groups
Scripts
Tasks
Schedules
Applications
Artifacts
Executions
Settings
```

Dashboard：
- Nodes Online/Offline
- Execution Running/Success/Failed
- Sync Synced/Outdated/Error
- Application Healthy/Unhealthy
- Recent Failures

Node Detail：
- Overview
- Inventory
- Labels
- Tasks
- Schedules
- Managed Applications
- Executions
- Sync

Script：
- Create/Edit/Clone
- Enable/Disable/Delete
- Revision History

Task：
- Create/Edit
- Enable/Disable
- Run Now
- Target
- Parameters
- Condition
- Schedule
- Retry
- Timeout
- Offline Policy
- Execution History

Application：
- Create
- Versions
- Artifact
- Assign Nodes
- Configuration
- Deploy/Upgrade
- Start/Stop/Restart
- Health
- Execution History

Artifact：
- Upload
- Version
- Architecture
- Size
- SHA256
- Usage

Execution：
- Metadata
- Status
- Duration
- Exit Code
- stdout
- stderr
- Offline
- Revision

所有状态必须来自真实后端，不允许永久 Fake Data。

---

## 44. 权限

一期：
- Administrator
- Operator
- Viewer

Administrator：全部权限。

Operator：
- 查看
- Run Task
- 查看日志
- Deploy Application
- 操作 Managed Application

Viewer：只读。

---

## 45. Audit

一期至少记录：
- Login
- Script Create/Modify/Delete
- Task Create/Modify/Delete/Execute
- Schedule Modify
- Artifact Upload
- Application Create/Modify/Deploy/Upgrade
- Node Enable/Disable

---

## 46. REST API

API-first。

至少：

```text
/api/nodes
/api/groups
/api/scripts
/api/tasks
/api/schedules
/api/artifacts
/api/applications
/api/applications/{id}/state
/api/applications/{id}/executions
/api/executions
/api/settings
```

Web UI 调用真实 API。

---

## 47. Agent 本地存储

建议：

```text
/var/lib/nodesteer/
├── state.db
├── scripts/
├── artifacts/
├── applications/
├── executions/
├── logs/
└── tmp/
```

Agent SQLite 建议使用 WAL。

核心数据：
- identity
- sync_state
- scripts
- tasks
- schedules
- applications
- artifacts
- executions
- deployments

---

## 48. Agent 资源目标

目标：
- Go 单二进制
- Idle CPU 接近 0
- Idle RAM 尽量 < 50 MB

Heartbeat / Revision Check / Inventory 需避免形成高频负载。

---

## 49. 一期明确不做

以下属于后续规划，一期不实现：

- Workflow DAG
- Task Dependency
- Parallel Execution
- Conditional Branch
- Rule Engine
- Event Engine
- Self-Healing
- Complex Condition DSL
- Aggregated Cluster Condition
- Strong Condition
- Distributed Lock
- Singleton
- Quorum
- Rolling Execution
- Rolling Deployment
- Canary Deployment
- Advanced Queue
- Priority
- Advanced Concurrency
- Approval
- Advanced RBAC
- LDAP
- OIDC
- SSO
- Hub HA
- Multi-Hub
- Multi-Tenant
- GitOps
- CLI
- Plugin SDK
- S3/MinIO External Artifact Storage
- Complex Notification System
- Agent Self Upgrade
- P2P/Relay/Overlay Network

架构不得阻碍未来加入这些能力。

---

## 50. 后续规划

### V1.1
- Bulk Concurrency
- Rolling Deployment
- Canary Deployment
- Webhook
- Notifications
- Approval
- Richer Conditions

### V1.2
- Workflow DAG
- Parallel / Branch
- Rule Engine
- Event Engine
- Self-Healing
- Dynamic Groups

### V2
- Distributed Lock
- Singleton
- Quorum
- Strong Conditions
- Hub HA
- Multi-Hub
- Advanced RBAC
- LDAP/OIDC
- GitOps
- CLI
- Plugin System
- S3-compatible Artifact Storage

---

## 51. 一期核心不变量

1. Hub 是 Desired State 唯一权威源。
2. Agent 主动连接 Hub。
3. 同步采用实时通知 + Revision Check + Reconnect Reconciliation。
4. Realtime Notification 不承担最终一致性。
5. Agent Revision 成功落盘后才能前进。
6. Allow Offline 的 Schedule 由 Agent Local Scheduler 负责。
7. 同一 Schedule Slot 禁止 Hub/Agent 双触发。
8. Execution ID / Execution Key 必须幂等。
9. Execution Start 前必须写本地 Journal。
10. Remote State 未知/过期默认 Fail Closed。
11. Artifact 校验失败禁止安装。
12. Managed Application 更新必须可恢复。
13. systemd 只管理 NodeSteer Managed Application。
14. Native 与 Docker 共用 Agent Core。
15. 宿主机差异必须通过 Host Adapter 隔离。
16. Docker Agent 状态必须持久化。
17. 不具备 Capability 的 Agent 不得接收对应 Host Task。

---

## 52. 最终产品定位

一期核心闭环：

```text
Hub Desired State
        ↓
Object / Global Revision
        ↓
Realtime Notify
+ Periodic Check
+ Reconnect Reconcile
        ↓
Agent Local State
        ↓
Local Scheduler / Executor
        ↓
Execution Journal
        ↓
Hub Reconciliation
```

应用闭环：

```text
Artifact
 ↓
Application Definition
 ↓
Agent Cache
 ↓
Host Adapter
 ↓
Atomic Deployment
 ↓
systemd
 ↓
Health Check
 ↓
Rollback
```

NodeSteer 一期的核心价值：

> 以轻量 Hub-Agent 模型集中管理 Linux 自动化，同时兼顾实时控制、最终一致性、Agent 离线自治，以及平台托管二进制应用的安全生命周期管理。
