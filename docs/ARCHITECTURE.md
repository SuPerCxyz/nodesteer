# NodeSteer 系统架构设计文档

版本：v1.1
文档类型：系统架构基线
适用阶段：一期研发 + 后续演进

## 1. 架构目标

NodeSteer 架构重点保证：

- Lightweight
- Hub Central Control
- Agent Active Connection
- Realtime Sync
- Eventual Consistency
- Local Scheduling
- Offline Autonomy
- Execution Idempotency
- Persistent Journal
- Safe Binary Deployment
- Native/Docker Unified Agent Model

一期不追求复杂编排平台能力。

---

## 2. 总体架构

```text
                         Users
                           │
                           ▼
                    ┌─────────────┐
                    │   Web UI    │
                    └──────┬──────┘
                           │
                           ▼
                ┌────────────────────┐
                │    NodeSteer Hub    │
                │                    │
                │ REST API           │
                │ Agent Gateway      │
                │ Node Manager       │
                │ Sync Manager       │
                │ Revision Manager   │
                │ Script Manager     │
                │ Task Manager       │
                │ Scheduler          │
                │ Execution Manager  │
                │ Artifact Manager   │
                │ Application Mgr    │
                └─────────┬──────────┘
                          │
                   Persistent Channel
                          │
             ┌────────────┴────────────┐
             │                         │
             ▼                         ▼
       Native Agent               Docker Agent
             │                         │
      NativeHostAdapter        ContainerHostAdapter
             │                         │
             └────────────┬────────────┘
                          │
                     Linux Host
```

---

## 3. Control Plane 与 Execution Plane

Hub 是 Control Plane：
- Desired State
- Node Management
- Script/Task/Schedule Definition
- Artifact/Application Definition
- Manual Operation
- Global State
- Execution History

Agent 是 Execution Plane：
- Local Sync
- Scheduler
- Condition Evaluation
- Process Execution
- Artifact Cache
- Application Deployment
- Host Integration
- Journal

Agent 不承担复杂全局编排。

---

## 4. Hub 架构形态

一期采用：

> Modular Monolith

推荐：
- Go Backend
- Web Frontend
- SQLite / PostgreSQL
- Local Artifact Storage

一期不拆微服务，不强制引入：
- Kafka
- RabbitMQ
- Redis
- Elasticsearch
- Kubernetes

原因：
- 大量本地事务边界
- 避免分布式事务
- 降低部署复杂度
- 保持轻量

---

## 5. Hub 模块

```text
Hub
│
├── API Server
├── Web Server
├── Authentication
├── Authorization
│
├── Node Manager
├── Inventory Manager
├── Group Manager
│
├── Script Manager
├── Task Manager
├── Schedule Manager
│
├── Condition Service
├── State Store
│
├── Revision Manager
├── Sync Manager
│
├── Execution Manager
├── Execution Dispatcher
│
├── Artifact Manager
├── Application Manager
│
├── Agent Gateway
├── Session Manager
│
├── Audit Manager
└── Persistence Layer
```

依赖方向：

```text
API / Handler
    ↓
Domain Service
    ↓
Repository / Persistence
```

HTTP Handler 不应直接承担核心业务事务。

---

## 6. Hub Endpoint

Web/API 与 Agent Gateway 独立：

```text
Web/API:       :8080
Agent Gateway: :8443
```

可分别控制暴露范围。

Hub 内网 + Public Agent 时，只需要将 Agent Gateway 通过 NAT/Reverse Proxy 暴露。

---

## 7. Agent 部署模式

Agent 支持：

```text
NATIVE
DOCKER
DOCKER_HOST_INTEGRATION
```

三种模式共用同一个 Agent Core。

---

## 8. Agent Core

```text
Agent Core
│
├── Connection Manager
├── Protocol Client
├── Sync Engine
├── Revision Checker
├── Inventory Collector
├── State Collector
├── Scheduler
├── Condition Engine
├── Execution Manager
├── Process Runner
├── Script Manager
├── Artifact Cache
├── Application Manager
├── Journal
└── Host Adapter
```

部署差异只能进入 Host Adapter / State Provider 等宿主机访问层。

---

## 9. Host Adapter

统一接口可包括：

```text
WriteFile
AtomicReplace
Chmod
Chown
Mkdir
Remove

InstallUnit
UpdateUnit
DaemonReload
EnableService
DisableService
StartService
StopService
RestartService
ServiceStatus
```

实现：

```text
HostAdapter
├── NativeHostAdapter
└── ContainerHostAdapter
```

Application Manager 不直接判断 Native/Docker。

---

## 10. Host Root

Native：

```text
HOST_ROOT=/
```

Docker Host Integration：

```text
HOST_ROOT=/host
```

Application Definition 永远使用宿主机逻辑路径：

```text
/usr/local/bin/foo
```

ContainerHostAdapter 映射：

```text
/host/usr/local/bin/foo
```

---

## 11. Docker Agent

基础 Docker 模式：
- Connection
- Sync
- Scheduler
- Journal
- Script/Task
- Artifact Cache

Host Integration 模式再启用：
- Host Filesystem
- Managed systemd
- Application Deployment

必须持久化：

```text
/var/lib/cadentra
```

容器 restart/recreate 行为必须与 Native Agent restart 基本一致。

---

## 12. Docker Host Integration 安全

Host Integration 必须显式开启。

不要把：

```text
privileged=true
```

作为默认唯一方案。

优先：
- Minimal Mounts
- Minimal Capabilities
- Host Path Allowlist
- Path Clean
- 防 `../` Escape
- 必要时防 Symlink Escape
- 明确 systemd/DBus Bridge

Host Integration 本质属于 High Trust Host Access。

---

## 13. Inventory Adapter

Native 可以直接读取 Host `/proc`、`/sys`、`/etc/os-release` 等。

Docker 必须区分：
- Container State
- Host State

产品默认 Inventory/Condition 表示 Host State。

无法可靠获得 Host 状态时返回 `UNAVAILABLE`，不能用容器状态冒充。

---

## 14. Agent Capability

Agent HELLO / Heartbeat 上报：

```text
script
local_scheduler
offline_execution
host_filesystem
managed_systemd
application_deploy
```

Hub 调度前执行：

```text
Task Required Capability
VS
Agent Capability
```

不满足：

```text
BLOCKED
```

---

## 15. Agent Identity

Node ID / Agent ID 必须稳定。

不能把 Hostname 或 IP 作为永久唯一标识。

首次注册：
- Registration Token
- Authentication
- 使用 Web 纳管命令时采用 Hub 预分配的 Stable Agent ID；直接启动时由 Agent 生成/接收 Stable ID
- Obtain Unique Agent Credential

未来可扩展 mTLS Client Certificate。

---

## 16. Agent Gateway 与 Session Manager

Agent Gateway 负责：
- Agent Auth
- Persistent Connection
- Heartbeat
- Realtime Notification
- Task Dispatch
- Execution Status
- Realtime Logs
- Sync Protocol

Session Manager 内存维护：

```text
Agent ID
Node ID
Connection ID
Connected At
Last Heartbeat
Remote Address
Protocol Version
Agent Version
```

Session 只是当前连接，不是业务状态唯一来源。

Hub 重启丢失 Session 不应破坏 Desired State 或 Execution 数据。

---

## 17. Desired State

Hub 是唯一权威源。

不同 Agent 的 Desired State 可以不同。

Sync Manager 根据：
- Task Target
- Group
- Label
- Application Assignment

计算 Agent 应同步的对象。

Agent 不必下载整个 Hub 数据库。

---

## 18. Revision 架构

对象 Revision：
- Script
- Task
- Schedule
- Application

Global Revision：
- 整体 Desired State 版本
- 快速判断 Agent 是否落后

Revision 必须全局单调递增。

SQLite 可用 revision_sequence。

PostgreSQL 可用 SEQUENCE。

---

## 19. Revision Change Log

Hub 保存：

```text
global_revision
object_type
object_id
object_revision
operation
created_at
```

Agent 支持：

```text
SYNC_REQUEST since=<global_revision>
```

Hub 返回变化对象的最终 Desired State。

Change Log 只需保留窗口。

Agent 太旧：

```text
FULL_RESYNC_REQUIRED
```

---

## 20. 同步三层保障

必须同时存在：

1. Realtime Change Notification
2. Periodic Revision Check
3. Reconnect Reconciliation

---

## 21. Realtime Notification

更新流程：

```text
User Update
 ↓
Domain Service
 ↓
DB Transaction
 ↓
Object Revision +1
Global Revision +1
 ↓
Revision Change
Audit
 ↓
COMMIT
 ↓
Sync Manager
 ↓
Notify Affected Agents
```

必须 Commit 后再 Notify。

通知只包含：

```text
Object Type
Object ID
Object Revision
Global Revision
```

---

## 22. Notification 语义

通知必须允许：
- duplicate
- lost
- out-of-order
- delayed

最终正确性只来自 Hub Desired State。

示例收到：

```text
rev18
rev20
rev19
```

最终 Agent 只需要同步到 Hub Current Revision 20。

---

## 23. Periodic Revision Check

Agent 周期发送 Local Global Revision。

Hub 返回 Desired Global Revision。

不一致则执行 Sync。

建议：
- 30~60 秒
- Configurable
- Jitter

Heartbeat 与 Revision Check 是独立机制。

---

## 24. Reconnect Reconciliation

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

一期至少对账：
- Configuration Revision
- Unsynced Executions
- Running Execution State

---

## 25. Tombstone

删除也必须进入 Revision Change Log。

例如：

```text
TASK task-a DELETE rev=1210
```

Agent：
- Stop schedule
- Remove local Desired Definition
- Keep Execution History

---

## 26. Atomic Sync

错误方式：

```text
update local revision
then save object
```

正确：

```text
BEGIN
Validate
Resolve Dependencies
Write Objects
Update Scheduler
Update Local Revision
COMMIT
```

失败：

```text
ROLLBACK
```

Revision 不前进。

---

## 27. Sync Plan

例如：

```text
Task rev10
requires Script rev7
```

必须先获取并验证依赖，最后一次性切换。

不能出现：
- New Task active
- Required Script missing

---

## 28. Agent 本地目录

```text
/var/lib/cadentra/
├── state.db
├── scripts/
├── artifacts/
├── applications/
├── executions/
├── logs/
└── tmp/
```

Native 与 Docker 共用逻辑结构。

Docker 必须使用 Persistent Volume。

---

## 29. Agent SQLite

建议：

```text
SQLite + WAL
```

核心表：
- identity
- sync_state
- scripts
- tasks
- schedules
- applications
- artifacts
- executions
- deployments

关键同步修改必须用事务。

---

## 30. Local Scheduler

由 Agent 自身实现，不依赖 Linux Cron。

支持：
- Cron
- Interval
- One-Time
- Timezone
- Misfire
- Offline Policy

Schedule 数据可包括：

```text
schedule_id
task_id
revision
type
expression
timezone
execution_owner
offline_policy
misfire_policy
next_run_at
```

---

## 31. Schedule Execution Owner

明确：

```text
HUB
AGENT
```

Allow Offline → AGENT。

Hub Online Required → HUB。

对于 Agent-owned Schedule，Hub 不在执行时间重复发送 `RUN_EXECUTION`。

---

## 32. Scheduled Execution Key

Agent-owned Schedule 使用：

```text
Task ID
+
Node ID
+
Scheduled Time
```

作为稳定逻辑 Key。

数据库 UNIQUE。

Task Revision 不加入 Slot 唯一性，否则同一时间槽可能因为 Revision 变化重复执行。

Execution 另外记录实际 Task/Script Revision。

---

## 33. Manual Execution

Hub：
- Create Execution UUID
- Persist PENDING
- Dispatch

Agent：
- Dedup by Execution ID
- Execute

一期 Agent Offline 时 Manual Run 明确失败/不可执行，不实现长期 Offline Queue。

---

## 34. Execution State Machine

一期：

```text
PENDING
   ↓
RUNNING
   ├─ SUCCESS
   ├─ FAILED
   ├─ TIMED_OUT
   └─ CANCELED
```

Condition 不满足：

```text
SKIPPED
```

---

## 35. Execution Journal

执行前：

```text
INSERT RUNNING
```

然后才启动 Process。

结束后更新结果。

Hub Offline：
- synced=false

Hub Recover：
- upload
- Hub idempotent upsert
- ACK
- synced=true

Hub Execution ID 必须 UNIQUE。

---

## 36. Agent Restart Recovery

启动发现旧 RUNNING，但进程不存在：

一期：

```text
FAILED
reason=agent_restarted
```

Application Deployment 使用单独 Deployment Journal 恢复。

---

## 37. Process Runner

统一负责：
- Working Directory
- Environment
- Run User
- Process Group
- stdout
- stderr
- Timeout
- Cancel

Timeout：

```text
SIGTERM Process Group
 ↓
Grace Period
 ↓
SIGKILL Process Group
```

不能只杀父 Shell。

---

## 38. Logs

本地日志是可靠源：

```text
Process
 ↓
Local Persistent Log
 ├─ Realtime Stream → Hub
 └─ Offline Retention
```

Hub 断开不能导致日志丢失。

必须限制：
- max_stdout_bytes
- max_stderr_bytes
- max_total_log_bytes

超限应 truncate 并标记，不应直接把 Agent 磁盘写满。

---

## 39. Condition Engine

一期：
- AND
- ==
- !=
- >
- <
- >=
- <=

Local State Provider：
- CPU
- Memory
- Disk
- File
- Directory
- Process
- Port
- Command Result

Condition Engine 不直接依赖 `/proc`，应通过 State Provider 抽象，便于 Native/Docker 统一。

---

## 40. Remote State

Hub 保存最新：

```text
node_id
property
value
observed_at
expires_at
```

Agent Offline 时只能使用仍在 TTL 内的 Cache。

过期：

```text
UNKNOWN → BLOCKED
```

---

## 41. Script 架构

Hub：
- scripts
- script_revisions
- SHA256

Agent：
- Fetch
- Verify
- Temp File
- Atomic Rename
- Update Revision

Task 引用 `script_id`，不复制 Script Content。

Execution 记录实际 Script Revision。

---

## 42. Artifact 架构

Artifact 为 immutable object：

```text
artifact_id
name
version
architecture
sha256
size
storage_path
```

内容变化必须创建新 Artifact。

---

## 43. Artifact Transport

长连接仅传 Control。

Binary 使用 HTTP(S) Artifact Endpoint。

这样以后可以平滑迁移到：
- S3
- MinIO
- CDN

---

## 44. Artifact Cache

Agent 使用 Content Addressed Cache：

```text
artifacts/<sha256>
```

下载：

```text
<sha>.tmp
 ↓
Download
 ↓
Verify
 ↓
rename → <sha>
```

---

## 44.1 Agent File Relay

文件中继复用 Agent 主动建立的 Hub 连接，但二进制不进入控制 WebSocket：

```text
Source Agent ──HTTPS upload──> Hub staged blob
Target Agent <─HTTPS download─ Hub staged blob
```

Hub 保存 `file_transfers` 和 `file_transfer_targets` 状态。源文件上传到按传输 ID 创建的临时文件，Hub 校验大小和 SHA256 后原子提升为 immutable blob，再向目标 Agent 发送交付元数据。目标 Agent 下载到目标目录临时文件，校验成功后原子替换目标路径。

目标 Agent 离线不影响其他目标，目标状态在 Hub 重启和 Agent 重连后恢复。源/目标身份均使用 Agent Credential 认证，上传只允许源节点，下载只允许目标节点。

此能力支持 Hub/Agent 位于不同可达网络的场景，但仍要求 Agent 能连接 Hub Gateway 或管理员配置可达的 Gateway 公网地址；不实现 Agent 直连、P2P、打洞或 Overlay Network。

---

## 45. Managed Application 架构

```text
Application
│
├── Artifact
├── Version
├── Binary Path
├── Configuration
├── Environment
├── Arguments
├── systemd Unit
└── Health Check
```

Application Definition 与 Artifact 是不同对象。

Definition Sync 不等于立即 Deploy。

---

## 46. Application Manager

依赖：
- Artifact Manager
- Host Adapter
- Health Checker
- Deployment Journal

业务层不直接操作 `/host/...` 或 Native/Docker 差异。

---

## 47. Deployment Flow

```text
Resolve Artifact
 ↓
Cache / Download
 ↓
SHA256 Verify
 ↓
Prepare
 ↓
Backup if upgrade
 ↓
Stop if required
 ↓
Install / Atomic Replace
 ↓
Config
 ↓
systemd Unit
 ↓
daemon-reload
 ↓
Start
 ↓
Health Check
 ↓
Commit Deployment
```

---

## 48. Atomic Replace

同一文件系统：

```text
write temp
 ↓
fsync
 ↓
chmod/chown
 ↓
rename
```

Docker Host Integration 时临时文件必须在目标 Host Filesystem 上。

不得假设：

```text
/container/tmp → /host/...
```

可原子 rename。

---

## 49. systemd Adapter

Application Manager 暴露：
- InstallUnit
- UpdateUnit
- DaemonReload
- Enable
- Disable
- Start
- Stop
- Restart
- Status

Managed Unit Registry：

```text
application_id
unit_name
```

systemd Adapter 只能操作已登记 Unit。

NodeSteer 不做通用 systemd 控制平台。

---

## 50. Docker Host systemd Bridge

Container Host Integration：

```text
Application Manager
 ↓
ContainerHostAdapter
 ↓
Host Systemd Bridge
```

Host systemd 接入与 Host path 映射独立封装。

不要在业务代码中硬编码 `docker exec + systemctl`。

---

## 51. Health Check

统一 `HealthChecker`。

一期支持：
- SYSTEMD
- TCP
- HTTP
- COMMAND

字段可包括：
- type
- target
- timeout
- attempts
- interval

---

## 52. Deployment Journal

保存：

```text
deployment_id
application_id
from_version
to_version
phase
backup_path
started_at
```

Phase：
- PREPARING
- STOPPED
- REPLACED
- STARTED
- VERIFYING
- DONE
- ROLLING_BACK

Agent Crash 后读取 Journal，判断继续还是 Rollback。

一期默认优先安全恢复 Previous。

---

## 53. Rollback

Health Check 失败：

```text
Stop New
 ↓
Restore Previous Binary
 ↓
Restore Previous Config
 ↓
Restore Previous Unit
 ↓
daemon-reload
 ↓
Start Previous
 ↓
Health Check
```

Execution 可以 FAILED，同时 Deployment Result 为 ROLLBACK_SUCCESS。

---

## 54. Hub 数据库

核心实体建议：

```text
users

nodes
node_labels
node_groups
node_group_members

scripts
script_revisions

tasks
task_revisions

schedules

artifacts

applications
application_revisions
application_assignments

executions
execution_logs

agent_sync_state

revision_changes

audit_logs
```

核心索引字段关系型。

Task / Application Revision 可采用：

```text
Core Indexed Fields
+
Definition JSON
```

避免一期 Schema 被未来高级功能锁死。

---

## 55. Hub Revision Transaction

更新 Task 示例：

```text
BEGIN

Insert Task Revision
Update Current Revision
Allocate Global Revision
Insert Revision Change
Insert Audit

COMMIT

Notify Agents
```

绝对不能 Commit 前通知 Agent。

---

## 56. Agent Protocol

控制消息建议：

```text
HELLO
HEARTBEAT

CHANGE_NOTIFICATION
REVISION_CHECK
SYNC_REQUEST
SYNC_RESPONSE
SYNC_ACK

RUN_EXECUTION
CANCEL_EXECUTION

EXECUTION_STARTED
EXECUTION_FINISHED
EXECUTION_ACK
LOG_CHUNK

ARTIFACT_PREFETCH
DEPLOY_REQUEST
DEPLOY_RESULT
SETTINGS
```

统一 Envelope：

```json
{
  "type": "...",
  "id": "...",
  "timestamp": "...",
  "payload": {}
}
```

协议必须版本化。

HELLO 包含：
- protocol_version
- agent_version
- deployment_mode
- host_integration
- capabilities

HELLO 首次注册使用 Registration Token；Hub 返回的 Agent Credential 持久化后用于后续连接和 Agent Artifact 请求。

节点纳管命令可通过 `agent_id`、`node_name` / `node_ip`（Native YAML）或 `CADENTRA_AGENT_ID`、`CADENTRA_NODE_NAME` / `CADENTRA_NODE_IP`（Docker 环境变量）指定 HELLO 上报的身份与节点地址。节点地址支持 IPv4、IPv6 或 DNS 主机名，并在生成命令时保持原值；未指定时 Agent 继续使用本机探测值。Web 页面生成命令时使用当前页面地址作为 Hub 地址来源，并保留配置的 Agent Gateway 端口；生成成功后 Hub 会先保存一条离线待接入节点记录，Agent 首次 HELLO 时绑定该记录。

纳管不要求管理员选择架构。Native 命令在目标机检测 `uname -m`，再通过公开的内置 payload 端点 `/api/agent/binary?architecture=...` 获取对应 Agent 和 SHA256 元数据；该只读 payload 不使用 Registration Token，Registration Token 仍只用于 Agent 首次 HELLO。Native 命令下载后必须校验 SHA256，Docker/Compose 使用多架构镜像自动选择平台。标准 Hub 可执行文件和 Hub 镜像携带两种 Agent payload，未打包的本地开发构建可回退到配置路径。

---

## 57. Control Message 不做可靠队列

配置变化的可靠性由：
- Revision
- Reconciliation

保证。

Execution 可靠性由：
- Execution ID
- Journal
- Idempotent Upsert

保证。

因此不需要一期自行实现 RabbitMQ。

---

## 58. 网络架构

统一：

```text
Agent → Hub Agent Gateway
```

支持 Hub 公网/内网及 Agent 公网/内网，只要 Gateway 可达。

两个完全隔离且无任何网络路径的内网不属于一期解决范围。

---

## 59. TLS 与 Agent 安全

Hub-Agent 必须支持 TLS。

首次注册：
- Registration Token

注册后：
- Unique Agent Credential
- Revoke Capability

未来：
- mTLS Client Certificate

Artifact 一期必须 SHA256。

未来可增加 Signature。

---

## 60. Secret

Secret 不得明文长期出现在：
- Script
- Task
- Agent Log

一期如支持 Secret 参数，应至少实现受控注入和日志防回显。

高级 External Secret Provider 后续实现。

---

## 61. Observability

Hub：
- Structured Logs
- `/healthz`
- `/readyz`
- Basic Prometheus Metrics

建议 Metrics：
- connected_agents
- sync_errors
- execution_total
- execution_failed
- active_executions
- artifact_download_bytes

Agent：
- Structured Logs
- Execution Logs
- Sync Logs

NodeSteer 不建设完整监控平台。

---

## 62. 故障恢复场景

### Hub Restart

```text
Hub Down
 ↓
Agents Disconnect
 ↓
Agent Local Scheduler Continues
 ↓
Hub Start
 ↓
Agents Reconnect + Jitter
 ↓
Reconciliation
```

### Agent Restart

```text
Load SQLite
 ↓
Recover Scheduler
 ↓
Recover Journal
 ↓
Mark interrupted non-recoverable executions
 ↓
Reconnect
 ↓
Reconciliation
```

### Notification Lost

```text
Notify Lost
 ↓
Periodic Revision Check
 ↓
Mismatch
 ↓
Sync
```

### Notification Out-of-order

最终以 Hub Current Desired Revision 为准。

### Sync Partial Failure

必须通过 Sync Plan + Local Transaction 防止半状态。

### Artifact Download Failure

仅 `.tmp`，成功 SHA256 后才 rename。

### Application Upgrade Crash

依赖 Deployment Journal + Previous Backup 恢复。

---

## 63. 扩展规模目标

一期架构至少面向约：

```text
100 ~ 1000 Agents
```

实际能力需压测。

避免：
- 每 Agent 高成本线程
- 同秒 Heartbeat Storm
- 同秒 Revision Check Storm
- 每次 Heartbeat 全量 Inventory
- 大日志塞 Execution 主表
- Binary 走控制长连接

---

## 64. 一期推荐代码结构

```text
/
├── cmd/
│   ├── hub/
│   └── agent/
│
├── internal/
│   ├── hub/
│   │   ├── api/
│   │   ├── auth/
│   │   ├── node/
│   │   ├── script/
│   │   ├── task/
│   │   ├── schedule/
│   │   ├── sync/
│   │   ├── execution/
│   │   ├── artifact/
│   │   └── application/
│   │
│   ├── agent/
│   │   ├── connection/
│   │   ├── sync/
│   │   ├── scheduler/
│   │   ├── execution/
│   │   ├── condition/
│   │   ├── artifact/
│   │   ├── application/
│   │   ├── journal/
│   │   └── host/
│   │       ├── native/
│   │       └── container/
│   │
│   ├── protocol/
│   └── models/
│
├── web/
├── migrations/
├── packaging/
│   ├── docker/
│   │   ├── hub/
│   │   └── agent/
│   └── systemd/
└── docs/
```

如果仓库已有合理结构，不要求为匹配示例而无意义迁移。

---

## 65. 一期研发顺序

### Phase 1
- Hub Skeleton
- Native Agent
- Docker Agent
- Registration
- Heartbeat
- Node UI

### Phase 2
- Persistent Channel
- Revision
- Realtime Notification
- Periodic Revision Check
- Reconnect Reconciliation

### Phase 3
- Script
- Task
- Manual Execution
- Journal
- Logs

### Phase 4
- Local Scheduler
- Cron
- Interval
- One-Time
- Offline Execution
- Execution Dedup

### Phase 5
- Artifact
- NativeHostAdapter
- ContainerHostAdapter
- Application
- systemd
- Health Check
- Rollback

### Phase 6
- RBAC
- Audit
- Web UI Completion
- Docker Host Integration Hardening
- Fault Tests
- Performance Tests

---

## 66. 一期关键测试

必须覆盖至少：

- Hub Restart
- Agent Restart
- Docker Agent Restart
- Docker Agent Recreate
- Persistent Volume Missing
- Agent Disconnect
- Hub Disconnect During Execution
- Agent Offline During Hub Script Update
- Agent Offline During Task Update
- Lost Notification
- Duplicate Notification
- Out-of-order Notification
- Revision Lag
- Change Log Window Expiry
- Full Resync
- Sync Dependency Failure
- Duplicate Schedule Trigger
- Offline Scheduled Execution
- Execution Re-upload
- Duplicate Execution Upload
- Timeout
- Cancel
- Process Group Cleanup
- Artifact Download Interruption
- Artifact SHA Failure
- Application Install
- Upgrade
- Health Failure
- Rollback
- Crash During Upgrade
- Docker Host Mount Missing
- Host Adapter Permission Denied
- Capability Mismatch
- Path Escape
- Cross-filesystem Rename

---

## 67. 核心架构不变量

1. Hub 是 Desired State 唯一权威源。
2. Agent 主动连接 Hub。
3. 同步必须是 Notify + Revision Check + Reconciliation。
4. Notification 不承担最终一致性。
5. Agent Revision 成功落盘后才能前进。
6. Agent-owned Schedule 只能由 Agent 触发。
7. Execution ID / Key 必须幂等。
8. Execution Start 前必须先写 Journal。
9. Unknown Remote State 默认 Fail Closed。
10. Artifact 校验失败禁止安装。
11. Managed Application 必须存在恢复路径。
12. systemd 只管理 NodeSteer Managed Application。
13. Native 与 Docker 共享 Agent Core。
14. Native/Docker 差异必须进入 Host Adapter。
15. Docker Agent state 必须 Persistent。
16. Capability 不匹配不得下发 Host Operation。
17. Commit 前不得发送 Desired State Notification。
18. Artifact/File Payload 不走控制长连接。

---

## 68. 后续高级能力架构预留

后续 Workflow 应构建在已有 Task/Execution 之上。

后续 Rolling/Canary 应由 Hub 做 Batch Selection，再复用已有 Application Deployment。

后续 Rule/Event 应触发现有 Task/Workflow，而不是重写 Agent Executor。

后续 Distributed Lock 增加 Coordination Layer。

后续 Hub HA 需要：
- Shared PostgreSQL
- Agent Session Ownership
- Scheduler Leadership
- Execution Ownership

Agent Local Scheduler 逻辑不改变。

---

## 69. 最终架构闭环

```text
Hub Desired State
        ↓
Object + Global Revision
        ↓
Realtime Notify
+ Periodic Check
+ Reconnect Reconcile
        ↓
Agent Local State
        ↓
Scheduler / Executor
        ↓
Execution Journal
        ↓
Hub Reconciliation
```

宿主机闭环：

```text
Agent Core
   ↓
Host Adapter
 ┌───────┴────────┐
Native          Container
   │                │
   └───────┬────────┘
           ↓
       Linux Host
```

Application 闭环：

```text
Artifact
 ↓
Application
 ↓
Cache
 ↓
Host Adapter
 ↓
Atomic Deployment
 ↓
systemd
 ↓
Health
 ↓
Commit / Rollback
```

架构原则：

> Hub 定义意图，Agent 可靠同步、本地执行和有限离线自治；Realtime Notification 解决速度，Revision/Reconciliation 解决最终一致性，Execution ID 解决重复执行，Journal 解决离线可靠性，Host Adapter 解决 Native/Docker 差异，Deployment Transaction 解决应用部署安全性。
