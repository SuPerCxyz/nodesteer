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
- Native Monitoring and Metric History

Agent 是 Execution Plane：
- Local Sync
- Scheduler
- Condition Evaluation
- Process Execution
- Metrics Collection
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
├── Monitoring Manager
├── Metric Store
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

Web/API 的 CORS 为通配来源（`Access-Control-Allow-Origin: *`）且不发送 `Access-Control-Allow-Credentials`；认证统一走 `Authorization: Bearer`，不依赖 Cookie 会话，因此不构成「通配来源 + 凭据」组合。按部署形态收紧（同源部署不发 CORS 头或来源白名单）需新增配置项与部署形态裁决，属独立变更。

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
/var/lib/nodesteer
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
node_metrics
```

`node_metrics` 表示同一 Agent 能读取宿主机指标；Docker 基础模式只能看到容器自身 `/proc`，不声明该能力。Gateway 在 `NODE_METRICS` ingest 时校验该能力，不满足直接拒绝；查询 API 以无样本/不可用状态表达，不以零值代替。

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

节点另有用户可编辑的 `display_name`，仅用于展示：默认空，空值回退 `hostname`，再回退 Node ID；不参与身份、匹配、权限、执行目标或路由，允许重名。Agent 上报的 `hostname`、HELLO 元数据刷新、重新注册与重新纳管均不覆盖显示名。显示名只通过单节点管理接口修改，变更记录 `node.rename` 审计。

首次注册：
- Registration Token
- Authentication
- 使用 Web 纳管命令时采用 Hub 预分配的 Stable Agent ID；直接启动时由 Agent 生成/接收 Stable ID
- Obtain Unique Agent Credential

未来可扩展 mTLS Client Certificate。入站模式已使用 Hub 自建 CA 签发的双向证书、证书指纹 pin 和每节点入站令牌完成二次认证；Agent 服务端证书 SAN 与节点 `endpoint` 绑定。

---

## 16. Agent Gateway 与 Session Manager

Agent Gateway 负责：
- Agent Auth
- Persistent Connection（Agent 主动出站，或 Hub 按节点 endpoint 主动拨号入站）
- Heartbeat
- Realtime Notification
- Task Dispatch
- Execution Status
- Realtime Logs
- Sync Protocol

两种连接方向使用同一套协议、HELLO 注册、SessionManager 和在线状态机。Session 额外记录建立方向（`outbound` / `inbound`），但所有业务发送方无需感知方向。出站会话优先：已有出站会话时 Hub 不拨号，入站会话在出站 HELLO 被接受后关闭；每个节点最多一个已认证会话。

Session Manager 内存维护：

```text
Agent ID
Node ID
Connection ID
Origin（outbound / inbound）
Connected At
Last Heartbeat
Remote Address
Protocol Version
Agent Version
```

Session 只是当前连接，不是业务状态唯一来源。

连接握手门禁：Hub 接受 HELLO（HELLO_ACK `accepted`）之前，Agent 不视为在线——Agent 侧的在线门禁取「握手完成」标志，而不是传输层连接状态；`rejected` 或负载解析失败一律按未接受处理（Fail Closed），断开与新连接均复位该标志。该门禁与「对账完成才 READY」是两道独立关卡（READY 另用于拒绝 Hub 派发/部署）。

Hub 重启丢失 Session 不应破坏 Desired State 或 Execution 数据。

---

## 17. Desired State

Hub 是唯一权威源。

不同 Agent 的 Desired State 可以不同。

Sync Manager 根据：
- Schedule Target
- Group
- Label
- Application Assignment

计算 Agent 应同步的对象。

Agent 不必下载整个 Hub 数据库。

Task 只描述执行内容。没有被任何 Schedule 命中的 Task 不进入该 Agent 的 Desired State；Manual Run 通过 Dispatch 载荷携带完整 Task 定义执行。

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
/var/lib/nodesteer/
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

只读运维查询面：每次部署恢复结束后，Agent 把部署 journal 摘要原子写入 `<data_dir>/deployments-summary.json`（先写 `.tmp` 再 rename）——含「非 `deployed` 的 `DONE` 行按 `completed_kind` 计数」与「仍活动行的 id/应用/阶段/`started_at`」，供运维直接查看不可重放记录（CR-3 终态化后的可见性出口；写失败只记 ERROR，不影响恢复）。

事务与锁：连接使用 `_txlock=immediate`，写事务在 BEGIN 阶段即取写锁，配合 WAL 与 `busy_timeout=15000`（Hub 配置 `sqlite_busy_timeout` 可覆盖），避免 deferred 事务「先读后写」升级写锁时不受 busy_timeout 保护的 `SQLITE_BUSY`/`BUSY_SNAPSHOT`。Gateway 对 HELLO 连线状态、心跳和断连 offline 写入的瞬时 BUSY 做短退避重试，只有认证/协议/会话失效才关闭连接或置 offline。

---

## 30. Local Scheduler

由 Agent 自身实现，不依赖 Linux Cron。

支持：
- Cron
- Interval
- One-Time
- On-Start
- Manual（不自动触发）
- Timezone
- Misfire
- Offline Policy
- Timeout / Retry / Run User

Schedule 数据可包括：

```text
schedule_id
name
task_id
revision
type
target
expression
interval_sec
run_at
timezone
execution_owner
timeout
retry
run_user
offline_policy
misfire_policy
next_run_at
```

`type=manual` 的 Schedule 只承载任务、目标与策略，Agent Local Scheduler 必须跳过，不为其生成自动执行。调度目标、Timeout、Retry、Run User、Offline Policy 均以 Schedule 为准，不从 Task 读取；缺少有效 Target 的调度必须拒绝触发并回报明确错误。

在线门禁与握手同源：本地调度器对 `execution_owner=AGENT` 且 `offline_policy=hub_online_required` 的调度在触发前要求握手完成（HELLO_ACK accepted）；重连窗口内不触发，避免在结果无法同步的窗口内产生执行。握手未完成时本地触发的执行（如 Allow Offline 调度）标记 `offline=true`，等重连对账补传。

---

## 31. Schedule Execution Owner

Schedule 的 `target` 决定目标节点，`execution_owner` 决定自动触发的归属；`manual` 类型不自动触发。

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

Scheduled Execution（Hub 或 Agent 触发）使用：

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

Schedule ID 也不进入 Key：同一 Task 的多个 Schedule 命中同一 Node 与 Scheduled Time 时仍视为同一槽位，保持既有幂等语义。Execution 记录来源 Schedule ID 仅用于展示与排障。

Execution 另外记录实际 Task/Script Revision。

---

## 33. Manual Execution

Manual Run 使用请求级临时目标与策略，不修改 Task 或 Schedule。

目标解析优先级：

```text
schedule_id > target > node_ids > 400
```

`schedule_id` 用于调度计划页「立即执行」：后端使用该 Schedule 的 Target 与策略，并把 `schedule_id` 写入 Execution，`trigger_type` 仍为 manual。`target` 支持 node/group/label；`node_ids` 兼容旧请求，等价于构造 node Target。请求同时携带 `params` 与 `timeout`/`retry`/`run_user`/`offline_policy`，缺省使用安全默认值（300 秒、0 次、当前用户、Hub Online Required）。

Hub：
- Resolve Target
- Create Execution UUID
- Persist PENDING
- Dispatch

Agent：
- Dedup by Execution ID
- Execute

应用类 Task 仍走 `AppManager.DeployTask`，把请求或调度上的策略传入 Execution 与下发载荷。

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

执行超时按 Schedule.Timeout 或 Manual Run 请求中的 Timeout 判定；`0` 使用默认 300 秒。

派发前门禁顺序固定为 disabled → online → capability：disabled 节点返回 `node <id> is disabled`，不产生执行记录，不得退化为 `not online` 文案；`maintenance`/`offline` 仍按 `not online` 处理。

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

Journal 与 `EXECUTION_FINISHED` 上报包含可空 `schedule_id`、`schedule_revision` 与 `created_at`：调度触发/调度页立即执行时写入来源调度及版本，任务页 Manual Run 为空/0，历史记录为空/0。Hub 幂等 Upsert 时，若已有记录的 `schedule_id`/`schedule_revision` 非空而上报为空，保留已有来源。

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

条件归属：
- Task 只保存本地条件（`local` 或本地 `and`）。
- Schedule 只保存远程条件（`remote` 或远程 `and`）。
- 调度执行时 Hub/Agent 把两者合并为 `local AND remote`，本地先评估、远程后评估。
- 任务页 Manual Run 只评估本地条件；调度页立即执行评估两者。
- 应用类任务与命令/脚本任务走相同条件评估路径。

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

远程条件由 Schedule 持有，Task 不再保存远程依赖。任务页试运行不查询远程状态；调度执行与调度页立即执行会查询并评估远程条件。

Hub 保存最新：

```text
node_id
property
value
observed_at
expires_at
```

Remote State 下发字段：
- value
- observed_at（仅诊断，不参与新鲜度判定）
- ttl_sec（观测窗口长度，仅诊断）
- ttl_remaining_sec（自 Hub 判定时刻起的剩余有效秒数，向上取整）

Agent 仅以 Hub 计算的 `ttl_remaining_sec` 判定新鲜度，不做跨主机时钟比较，
彻底消除 Hub/Agent 时钟偏移误判；该字段缺失或 <= 0 一律视为未知并 Fail Closed，
不承诺与不下发该字段的旧 Hub 兼容。

Agent Offline 时只能使用仍在 TTL 内的 Cache；当前协议下 Agent 不维护独立 Cache，离线或应答不可用时条件不可评估，按 Fail Closed 处理。

过期：

```text
UNKNOWN → BLOCKED
```

协议现状（保留分支）：`REMOTE_STATE` 目前仅作为 Hub 对 Agent `REMOTE_STATE_REQ` 的应答类型；Agent 只发送查询请求，不主动上报 `REMOTE_STATE`。因此上方「Hub 保存最新」的写入路径与缓存命中/过期分支为协议对称性与未来扩展保留，当前不是活跃路径。

Remote State 实际由 Hub 在收到请求时实时解析（`online` → 节点当前状态；`last_execution` → 最近一次执行结果），有效性判定以 Hub 在应答请求时计算的 `ttl_remaining_sec` 为准。

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

下载有界：Agent 共用载荷 HTTP 客户端设置 15 分钟总时限与 30 秒响应头超时（Go 无读空闲超时，总时限按保守链路速率覆盖大文件）；超时按失败处理，`defer` 清理 `.tmp`，不残留 0B 临时文件。Artifact 下载、Agent 自升级二进制与文件中继载荷传输均在该客户端约束内。

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
completed_kind
backup_path
app_revision
binary_path
config_path
unit_name
started_at
```

`app_revision` 与 `binary_path` / `config_path` / `unit_name` 为部署上下文绑定，在产生宿主副作用前写入，供恢复时校验 backup 归属。

`from_version` 是该应用本次部署前实际安装的版本：取最近一次成功部署（`phase=DONE` 且 `completed_kind=deployed`）的 `to_version`，无成功部署记录时为空；回滚成功（`rolled_back`）不构成已安装版本，不参与取值。`from_version` 不取应用定义的期望版本。

`completed_kind` 记录 `DONE` 的终态成因：`deployed`（成功部署）、`rolled_back`（回滚成功后收尾，含恢复重放成功）、`prepare_failed`（准备阶段失败）、`superseded`（已被同应用更晚的成功部署取代）、`stale_binding`（Journal 绑定与当前应用定义不一致，备份归属未知）、`legacy_unbound`（旧行缺绑定字段，备份归属未知）、`prepare_interrupted`（准备阶段中断，未产生宿主副作用）。旧记录缺该字段视为未知。

恢复阶段判定为不可重放的记录（`superseded` / `stale_binding` / `legacy_unbound` / `prepare_interrupted`）落 `DONE` 终态并记录成因，离开活动部署集合、重启不再重复告警；四者均不构成取代依据，也不重放回滚。

Phase：
- PREPARING
- STOPPED
- REPLACED
- STARTED
- VERIFYING
- DONE
- ROLLING_BACK
- ROLLBACK_FAILED

`ROLLING_BACK` 含回滚中途崩溃留下的记录；`ROLLBACK_FAILED` 表示回滚已尝试且失败，两者在重启恢复时都要经下方防护校验后才决定是否重放。

Agent Crash 后读取 Journal，判断继续还是 Rollback。

重放回滚前必须通过双重防护，任一命中即不改动任何文件、不重放回滚，记录落 `DONE` 终态并记录成因（备份文件保留，供人工检查）：

1. Superseded：同 application 已存在 `started_at` 更晚、`phase=DONE` 且 `completed_kind=deployed`（成功部署）的部署；准备阶段失败（`prepare_failed`）与回滚成功（`rolled_back`）不构成取代，旧记录缺 `completed_kind` 视为未知，同样不构成取代；
2. Binding：Journal 记录的 `app_revision` 与部署时解析的 `binary_path` / `config_path` / `unit_name` 与恢复时的当前应用定义一致；记录缺失或任一不一致视为未知。

通过校验后以 Journal 记录的路径重放。

一期默认优先安全恢复 Previous；无法确认归属的记录不得覆盖更晚的 DONE 部署，需人工检查。

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

monitoring_http_targets
monitoring_http_observations
monitoring_rules
monitoring_rule_events
monitoring_rule_steps
monitoring_rule_samples

node_metric_samples          # 原生指标 raw（schema v23）
node_metric_rollups          # 原生指标 1m/5m/1h 聚合（schema v23）

alert_rules                  # 阈值告警规则（schema v24）
alert_states                 # 规则+节点+指标+序列当前状态
alert_events                 # 活动/历史告警事件
notification_channels        # 通知渠道，只存环境变量名引用
alert_notifications          # 通知发件箱
alert_notification_attempts  # 每次发送尝试
```

监控相关表为增量迁移（`schema_migrations` 版本 12 起；版本 23 创建原生指标表，版本 24 创建告警/通知表，版本 28 增加列表查询索引）。原生指标存储、1m/5m/1h 聚合、后台周期聚合/清理、gateway `NODE_METRICS` ingest 和查询 API 已接入；告警评估、通知发件箱和渠道 API 已接入。`monitoring_node_bindings` 为旧 Nocturne 绑定遗留表，不再参与新链路，物理删除由后续独立迁移决定。升级失败按本节迁移失败处理，回退恢复 Hub DB 备份。旧 Nocturne 历史不迁移、不回填。

核心索引字段关系型。

Task / Schedule / Application Revision 可采用：

```text
Core Indexed Fields
+
Definition JSON
```

避免一期 Schema 被未来高级功能锁死。

`schedules` / `executions` 字段：

- `schedules` 的定义 JSON 增加 `name`、`target`（node/group/label）、`timeout`、`retry`、`run_user`，`type` 增加 `manual`；`task_id` 与触发器、时区、owner、offline/misfire 策略、enabled、revision 保持。
- `executions` 增加可空 `schedule_id` 与 `schedule_revision`，并补充必要索引；`created_at` 沿用既有列并回填空值，同时在 API 与 Agent 协议中对外暴露。`schedule_id`/`schedule_revision` 不参与 Scheduled Execution 的唯一约束。
- Agent 本地 SQLite 的 `executions` 同步增加 `schedule_id`/`schedule_revision`，并持久化 `created_at`；Hub 与 Agent 的 schema 均由各自启动迁移自动升级。

数据迁移（Hub 启动时执行，单事务内完成）：

1. 读取旧 Task 定义，把 `target`、`timeout`、`retry`、`run_user`、`offline_policy` 复制到其全部 Schedule。
2. 重写 Task 定义 JSON，移除上述旧字段；重写 Schedule 定义 JSON，补齐新字段。
3. 对有 Target 但没有 Schedule 的旧 Task，创建一条禁用的 `manual` 迁移调度，名称为 `迁移：<task name>`，保留原目标与策略。
4. 历史 Execution 的 `schedule_id`/`schedule_revision` 保持为空/0；`created_at` 按 `COALESCE(start_time, scheduled_time, end_time, now)` 回填。
5. 拆分旧 Task.Condition：本地部分保留在 Task，远程部分复制到该 Task 的全部 Schedule；混合 `and` 递归拆分，不得丢失条件。
6. 有目标无调度的旧任务，远程条件写入迁移 manual 调度；确实无法绑定时告警并保留本地部分。
7. 迁移完成后提升 Global Revision，触发 Agent 全量对账。

迁移失败时 Hub 启动报错，不继续以半迁移状态运行。旧 Hub 不支持直接读取新库；回滚必须恢复 Hub DB 备份与旧二进制。

连接层：SQLite 以 WAL、`_txlock=immediate`（写事务 BEGIN 阶段即取写锁）、`busy_timeout(15000)` 与外键约束打开，消除 WAL 下 deferred 事务升级写锁时的 `SQLITE_BUSY` 随机失败，并发写按序等待而非直接报错。

存储层错误不回传客户端：SQLITE_BUSY/LOCKED 家族归一化为 503 `storage busy, please retry`，其余内部错误归一化为 500 `internal error`；驱动原文只写 error 级结构化日志。

存储层并发约束（硬约束；违反会造成连接池死锁挂起，缺陷 QA-20261018-F1/F2 的根因）：
- 禁止在持有 `rows`（未关闭的查询结果，占用 1 条池连接）时再发起需要第二条池连接的查询：列表读取必须单条 SQL 一次取回（如 `file_transfers LEFT JOIN file_transfer_targets`），不得在 `rows.Next()` 循环内嵌套查询。
- 写事务闭包（`runMutationTx`/`WithTx`）内只允许用事务上下文（`txctx`）调 store——`queryer(ctx)`/`execer(ctx)` 在事务上下文中复用事务连接；禁止用外层 `ctx` 在事务内发起新的存储调用（等于持写锁再向连接池借第二条连接）。
- 连接池固定 `SetMaxOpenConns(8)`；上述两类嵌套借用都会在 ≥8 并发时耗尽连接池并死锁挂起（只能靠客户端断开释放），挂起期间拖垮全库端点。

资源写路径（UPDATE/DELETE）校验命中行数（`execExpectRow`）：零行命中返回 `sql.ErrNoRows`，由 API 层归一化为 404，不得静默成功（缺陷 QA-20261018-F5）。

适用范围（与实现一致）：**对外 API 可见的资源写方法必须校验**（已接入 15 个 store 方法，覆盖 nodes/groups/scripts/tasks/schedules/artifacts/applications 的更新与删除）。其余直连 `s.db` 的写方法经审计确认调用点全在事务外、且语义为幂等删除或内部状态机（无对外 404 语义需求），当前视为安全；后续统一改造方向为全量 `queryer(ctx)/execer(ctx)` + CI 静态禁令 + 池=1 表驱动守卫（见 QA-20261018 代码评审 minor-04）。

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

NODE_METRICS
MONITOR_PROBE
MONITOR_PROBE_RESULT
```

`NODE_METRICS` 为指标增量消息：`node_id`、`sequence`、`timestamp`、`metric`、`dimensions`、`value`、`quality`。`quality=1` 为有效，`quality=2` 为缺失且 `value=null`。旧 Agent 不发送该消息；新 Hub 消费该消息。部署时先升级 Hub 再升级 Agent。

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

协议版本随两次模型重构提升：1→2（Task/Schedule 模型，`schedule_id`/`created_at`），2→3（条件归属拆分与 `schedule_revision`）。当前版本为 3。`RUN_EXECUTION`/`EXECUTION_FINISHED`/`DEPLOY_REQUEST`/`DEPLOY_RESULT` 增加可选 `schedule_revision`；`schedule_id`/`schedule_revision` 不参与 Scheduled Execution Key。`NODE_METRICS`、`MONITOR_PROBE`/`MONITOR_PROBE_RESULT` 为协议 3 的增量消息，不改变既有消息语义。新 Hub 拒绝旧协议版本 Agent，旧 Hub 不识别新 Agent，Hub/Agent 必须协同升级；Web 与 Hub 同版本发布。

HELLO 包含：
- protocol_version
- agent_version
- deployment_mode
- host_integration
- capabilities

HELLO 首次注册使用 Registration Token；Hub 返回的 Agent Credential 持久化后用于后续连接和 Agent Artifact 请求。

HELLO_ACK 语义：仅 `accepted` 表示 Hub 接受本次连接（注册/鉴权通过），Agent 据此置在线握手标志；`rejected` 或负载解析失败一律按未接受处理（Fail Closed），断开与新连接均复位标志。

节点纳管命令可通过 `agent_id`、`node_name` / `node_ip`（Native YAML）或 `NODESTEER_AGENT_ID`、`NODESTEER_NODE_NAME` / `NODESTEER_NODE_IP`（Docker 环境变量）指定 HELLO 上报的身份与节点地址。节点地址支持 IPv4、IPv6 或 DNS 主机名，并在生成命令时保持原值；未指定时 Agent 继续使用本机探测值。Web 页面生成命令时使用当前页面地址作为 Hub 地址来源，并保留配置的 Agent Gateway 端口；生成成功后 Hub 会先保存一条离线待接入节点记录，Agent 首次 HELLO 时绑定该记录。

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

统一控制面支持两种等价承载方式：

```text
Agent → Hub Agent Gateway          （出站优先）
Hub → Agent :18444 TLS listener    （Hub 在内网、Agent 在公网时的回退）
```

Hub 仅在以下条件同时满足时主动拨号：节点 `endpoint` 已配置、入站 bundle 已签发、节点不是 `disabled`、当前无已有会话且不在断开宽限期内。拨号目标只来自管理员配置的节点 endpoint；Hub 在拨号前重新解析 DNS，并按 CIDR/端口策略拒绝 loopback、link-local、云 metadata、multicast 和未授权端口（RFC1918 默认允许）。

支持 Hub 公网/内网及 Agent 公网/内网，只要至少一个方向可达。出站会话始终优先；入站只是回退，不改变 Hub 是 Desired State 唯一权威源。

控制长连接不承载大文件。入站数据面复用同一 TLS 端口，由 Hub 发起 HTTPS push/pull：源文件由 Hub 拉取、目标文件由 Hub 推送、Artifact 与 Agent 二进制由 Hub 推送，全部落临时文件并校验大小/SHA-256 后原子提交。数据面请求必须同时通过 mTLS、Hub 客户端证书 pin、每节点令牌、`session_id` 与 `node_id` 绑定；transfer 授权 5 分钟有效。能力缺失或阶段不可用时仍返回 409 `inbound_data_plane_unavailable` / `inbound_stage_unavailable` 或落 `BLOCKED`，不得静默超时。

两个完全隔离且无任何网络路径的内网不属于一期解决范围。

---

## 59. TLS 与 Agent 安全

Hub-Agent 必须支持 TLS；入站连接必须使用最低 TLS 1.2 的 mTLS，禁止明文回退。

首次注册：
- Registration Token

注册后：
- Unique Agent Credential
- Revoke Capability

入站连接：
- Hub 自建 CA 签发 Agent 服务端证书与 Hub 客户端证书
- 双向校验证书链、EKU 与证书指纹 pin
- `HUB_HELLO` / `HUB_HELLO_ACK` 使用每节点入站令牌做 HMAC proof；令牌本身不在连接上传输
- Hub 校验 Agent 证书链、SAN 与 endpoint 一致、EKU serverAuth 及节点证书指纹 pin
- Agent 校验 Hub 客户端证书链、EKU clientAuth 与 Hub 客户端证书指纹 pin
- 任一失败立即关闭连接，不回退明文或跳过 pin
- 入站令牌以 AES-256-GCM 密文存储，API/日志/审计不返回明文
- 未安装完整 bundle 时 Agent 不监听任何管理端口

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

内部与存储层错误必须写 error 级结构化日志（至少 method、path、error 原文；不含 query，避免 `agent_token` 等敏感值），响应侧同时归一化，不回传驱动原文。

日志级别语义：连接已关闭/客户端取消（`context.Canceled`、`net.ErrClosed`、`io.EOF`、`use of closed network connection`、`connection reset by peer`、`broken pipe` 等，经 `hub.IsConnectionGone` 统一判定）属于正常清理场景，不是服务端错误——API 错误路径降级为 Debug（`api request canceled or connection gone`，仅 method/path；HTTP 响应仍为归一化状态码），Gateway 在途写失败降级为 WARN；真实错误（如 `io.ErrUnexpectedEOF`、websocket 写限额超限）仍保持 error 级（缺陷 QA-20261018-F8、OBS-20261018-SCALE-02）。

### 61.1 原生节点监控

同一个 NodeSteer Agent 同时执行任务和采集节点指标，不引入第二个监控 Agent。数据流：

```text
Agent (/proc, /host/proc)
  ↓  CPU / memory / load / disk / network, 默认 30s
NODE_METRICS 批量样本 (node_id, sequence, timestamp, metric, dimensions, value, quality)
  ↓
Hub 校验 / 去重 / 排序
  ↓
SQLite raw (默认 7d) → 1m (90d) → 5m (1y) → 1h (2y)
  ↓
overview / series / metrics / latest API
  ↓
Web 节点总览、历史、规则求值
```

- Agent 声明 `node_metrics` 能力；Native 与 Docker Host Integration 支持，Docker 基础模式不声明。旧 Agent 不声明时页面显示不可用，规则不触发，不以 0 代替。
- 补报缓冲为纯内存：默认 20 批、配置最大 60 批、硬上限 4MiB（序列化字节计费），任一上限先到即淘汰最旧并记录丢弃；不落盘、不建指标表。WebSocket 写成功或收到 `NODE_METRICS_ACK` 后才出队；旧 Hub 未声明 `node_metrics_ack` 能力时回退写成功即出队。ACK 超时/写失败保留批次并按最旧优先重发，Hub 主键 UPSERT 保证幂等。Agent WebSocket 发送队列 32 帧 + 1MiB 估算字节、读限制 2MiB；Hub 同步响应超过 2MiB 时返回明确错误而不是静默断链。
- CPU：`cpu.usage` 及组成 `cpu.user`、`cpu.nice`、`cpu.system`、`cpu.idle`、`cpu.iowait`、`cpu.irq`、`cpu.softirq`、`cpu.steal`；维度 `cpu=all|<n>`，per-core 最多 32 核，`cpu=all` 始终上报。
- 内存/Swap：`memory.usage`、`memory.total_bytes`、`memory.used_bytes`、`memory.available_bytes`、`memory.cached_bytes`、`memory.buffers_bytes`、`memory.free_bytes`（MemFree）；used 保持 total−available 的既有语义。Swap 为 `memory.swap_total_bytes/used_bytes/free_bytes/usage`。Swap 未启用时 0 是有效值。
- 磁盘/Inode/I/O：`disk.usage`、`filesystem.total_bytes/used_bytes`、`filesystem.inodes_usage/used/free`、`disk.read_bps`、`disk.write_bps`、`disk.read_iops`、`disk.write_iops`；维度 `mount/device`。inventory.hostname 读取宿主 `/proc/sys/kernel/hostname`，不可用时未知，不以可配置节点名替代；文件系统 used 使用 Bfree，而既有 disk.usage 继续使用 Bavail；块设备分类从宿主 sysfs 采集并随 inventory 更新，不可确认时为 unknown。
- 网络：`network.rx_bps/tx_bps`、`network.rx_errors/tx_errors`、`network.rx_dropped/tx_dropped`；维度 `interface`，速率按采样间隔换算为每秒值。
- 系统：`system.uptime`、`system.processes`、`system.processes_running`、`system.temperature_c`、`system.fan_rpm`；无传感器或读取失败时输出缺失/无数据，不伪造 0。
- 容器：`container.cpu.usage`、`container.memory.usage`、`container.network.rx_bps/tx_bps`，维度 `container` 和稳定 `container_id`，旧的 name-only 历史保留独立身份；自动探测 Docker/Podman socket（`/var/run/docker.sock`、`/run/docker.sock`、`/run/podman/podman.sock`、`/var/run/podman/podman.sock`），Host Integration 使用宿主根目录下的 socket。无 socket 或容器不可读时没有容器样本，不显示 0。
- 采集上限：CPU 32 核、磁盘设备 32、网络接口 32、容器 32；容器额外枚举至 33（表示至少 33），同批同时间戳上报无维度 `container.collection.discovered_count/sampled_count/limit`。超过 16 个容器时完整扫描周期至少 60s，≤16 时至少 30s；两次扫描之间仍上报汇总。仅新鲜、对齐且有效的计数可判完整性；失败或旧 Agent 为未知。容器样本及计数同批入库，series 上限不变。
- `quality=1` 为有效值，`quality=2` 为缺失且 `value=null`；真实 0 是有效样本，gap 不等于 0。
- Hub 以 `node_id + metric + dimensions + timestamp` 幂等去重，`sequence` 用于排序和诊断；迟到/更正样本触发对应聚合桶重算。聚合增量范围取「最后桶向前 10 桶」与「`now-1h` 迟到窗口」的并集，按 15 分钟分块提交事务；超过 1h 窗口的样本保留 raw 但不扩展 rollup 重算，并记录截断。连续断档继续由空桶/gap 表达。Hub 启动后首轮聚合延迟 30s，先允许 Agent HELLO/心跳完成。
- 查询按范围选择最细可用 tier，返回 `state`、`tier`、`stepNs`、`points`、`gaps`；跨 tier 时允许 raw 精化边界。
- overview 只查询页面渲染所需的 10 个 metric，latest 与采样周期推导窗口均限制为最近 15 分钟；结果按节点集合缓存 15s，raw 写入/清理 generation 变化立即失效。节点状态不进入缓存。
- 新增单节点 `GET /api/monitoring/nodes/{id}/overview`、批量 `series/batch` 与 `history/batch`；旧 `series`/`metrics`/`latest`/`overview` 契约不变。series 目录缓存 60s，history 缓存 30s。
- Hub API JSON 在客户端接受 gzip 时压缩（`Content-Encoding: gzip`、`Vary: Accept-Encoding`）；二进制/SSE/304 不压缩。静态资源运行时 gzip、可选预压缩 brotli；HTML `no-cache`，哈希资源一年 immutable，其余静态资源短缓存，ETag/Last-Modified 支持 304。
- schema v28 增加 executions/file_transfers/audit_logs 的 `created_at` 查询索引；API >500ms 与原生指标 SQL >200ms 记录慢日志和计数，日志不包含查询值或敏感信息。
- `node_metric` 规则从原生样本求值，保留新鲜度、gap、乱序/冲突中断、去重、冷却、失败暂停、验证和补偿语义。外部 HTTP(S)/TCP/ICMP 探测仍是独立监控对象。
- Nocturne 后端、路由、配置和绑定已移除；`monitoring_node_bindings` 仅作为遗留表保留，不参与新链路。旧 Nocturne 历史不迁移、不回填、不展示。
- `overview` 的告警数当前为占位 0，规则告警汇总未接入；不得把该字段写成已实现的告警统计。

NodeSteer 不建设完整可观测性平台：不采集应用日志、链路追踪或任意自定义指标；数据库/消息队列适配器仍只保留 target kind 边界。

当前 Agent 采集、`NODE_METRICS` 协议与 ACK、能力声明、gateway ingest、v23 存储/聚合/后台清理、原生查询 API、原生规则和前端总览/历史均已接入；真实大规模压测、overview N+1/索引/节点删除清理等 P3 和完整回归矩阵仍在进行。

实现与验证进度见 `openspec/changes/unified-monitoring-automation/tasks.md`；该目标不改变本文件 §67 的核心架构不变量。

### 61.2 告警与通知

告警是原生指标的独立消费方，只负责通知与记录，**不触发任务**；任务联动仍由 `monitoring.Rule` 和 `/rules` 执行链负责。

- schema v24 创建 `alert_rules`、`alert_states`、`alert_events`、`notification_channels`、`alert_notifications`、`alert_notification_attempts`。
- 规则按 `rule + node + metric + series` 去重；状态为 `pending`、`firing`、`resolved`、`silenced`、`unknown`（内部正常态为 `ok`）。支持持续时间、恢复时间、冷却、severity（info/warning/critical）、启停、静默和事件确认。
- 评估循环默认 30s；无样本、样本过期、节点离线或查询失败时 fail-closed：不触发、不恢复，活动状态保持并等待有效样本。通知发件箱默认 5s 扫描。
- 活动事件在数据库中通过部分唯一索引保证同一规则/节点/指标/序列最多一个；重复评估不重复通知。
- 通知渠道：webhook、Slack、钉钉、企业微信、Telegram、Email（SMTP）。配置只保存非敏感字段和环境变量名（`url_env`、`token_env`、`password_env`、`to_env`）；实际 secret 在发送时从 Hub 进程环境读取，API 和日志不回显。
- 通知采用发件箱：评估只写事件/通知记录，独立发送循环处理；默认最多 3 次尝试、指数退避，配置/环境变量错误直接失败，测试发送不要求渠道启用。
- API：`/api/monitoring/alerts/rules`、`/api/monitoring/alerts/events`、`/api/monitoring/alerts/events/{id}/ack`、`/api/monitoring/alerts/rules/{id}/silence`、`/api/monitoring/notification-channels`、`/api/monitoring/notification-channels/{id}/test` 等，全部 Administrator 可用；通知发送记录查询 API 尚未实现。
- `overview.alerts` 仍为占位 0；告警页展示规则/渠道/事件，但概览的规则告警汇总未接入。

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
Reconnect（出站成功则优先；否则 Hub 重新拨号入站）
 ↓
Reconciliation
```

### Inbound Session Lost

```text
入站会话断开
 ↓
Hub 保留节点原有 pending/offline 状态并更新 inbound_last_error
 ↓
宽限期后 Agent 出站优先重连
 ↓
出站仍不可达时 Hub 按 endpoint 重新拨号
 ↓
HELLO_ACK accepted 后才置 online
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

仅 `.tmp`，成功 SHA256 后才 rename；下载受 15 分钟总时限与 30 秒响应头超时约束，超时按失败处理并清理 `.tmp`。

### Application Upgrade Crash

依赖 Deployment Journal + Previous Backup 恢复；重放前须通过 superseded 与 binding 校验，陈旧 Backup 不得覆盖更晚的 DONE 部署。

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
- Task/Schedule Migration（目标与策略复制、迁移 manual 调度）
- Task 无 Schedule 的不同步与 Manual Run
- Manual Run 临时目标与策略
- Duplicate Schedule Trigger
- Schedule Target 变更后的 Desired State 收敛
- Manual Schedule 不自动触发
- Execution 来源 Schedule 与 Created At
- Agent Protocol Version Mismatch
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
2. Agent 必须建立或接受 Hub 发起的已认证连接；Hub 仍是唯一权威源。
3. 同步必须是 Notify + Revision Check + Reconciliation。
4. Notification 不承担最终一致性。
5. Agent Revision 成功落盘后才能前进。
6. Agent-owned Schedule 只能由 Agent 触发。
7. Execution ID / Key 必须幂等；Scheduled Key 为 Task ID + Node ID + Scheduled Time，不含 Schedule ID。
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
19. Task 只描述执行内容，Schedule 是任务、节点目标与执行策略的唯一绑定入口。
20. Desired State 按 Schedule.Target 计算；无 Schedule 引用的 Task 不参与同步，但仍可 Manual Run。

---

## 68. 后续高级能力架构预留

后续 Workflow 应构建在已有 Task/Execution 之上。

后续 Rolling/Canary 应由 Hub 做 Batch Selection，再复用已有 Application Deployment。

后续通用 Rule/Event 能力应触发现有 Task/Workflow，而不是重写 Agent Executor；原生监控已通过专用规则复用现有 Task/Execution，并由同一 Agent 采集指标、Hub 原生存储历史，不改变该方向。

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
