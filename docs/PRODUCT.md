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

节点状态支持人工设置：管理员可通过页面「设为在线」或 `PUT /api/nodes/{id}` 把 offline 节点手工置为 online。手工置为 online 的节点在无 Agent 会话时不会被自动清扫（心跳检查只作用于已注册会话，仅 Hub 启动时一次性复位无会话的孤儿 online），会保持 online，直到 Hub 重启或再次人工修改。

向此类节点派发不会静默挂起：派发明确返回 `400 agent offline`，执行记录落 `FAILED` 并携带 block_reason。

`disabled` 节点不进入派发：命中 disabled 节点时返回 `400 node <id> is disabled`；该判定先于 online/offline 通用判定，且不产生执行记录（`maintenance`/`offline` 仍按 `not online` 语义返回）。

节点资源的权限判定先于存在性检查：`DELETE`/`POST`/`PUT /api/nodes/{id}` 对 operator/viewer 在节点存在与不存在时都返回 403，不通过状态码差异泄露节点是否存在（存在性 oracle 关闭）；管理员对不存在的 id 得到 404 `node not found`（不再返回 400 + 存储层原文）。

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

定义校验（Create/Update）：
- `timeout < 0` → 400；`timeout == 0` 归一为默认 300s（与 Task、DB 默认值一致）
- `content` 上限 256 KiB（262144 字节，按字节数判定；超限 → 400）
- `script.timeout` 暂不参与执行超时；执行超时以下发 Task 的 `timeout` 为准

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

One-Time 的 `run_at` 必须晚于当前时间：Create 时过去时间 → 400 `run_at must be in the future`；Update 仅在 `run_at` 实际变化时校验，未修改的历史过去时间记录仍可编辑/禁用，不因该校验被锁死。

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

Hub Online Required 的在线门禁按执行归属分两处落实：`execution_owner=HUB` 时由 Hub 在下发前检查节点状态（离线/禁用明确拒绝）；`execution_owner=AGENT` 时由 Agent 本地调度器在触发前检查「HELLO_ACK accepted（握手完成）」门禁——该门禁不是传输层已连接，重连窗口（连接建立但握手未完成）内不触发，避免在结果无法同步的窗口内产生执行。握手未完成时本地触发的执行（如 Allow Offline 调度）标记 `offline=true`，等重连对账补传。

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
- TTL Remaining Sec

Hub 随 Remote State 下发自判定时刻起的剩余有效秒数（`ttl_remaining_sec`）；
Agent 仅据此判定新鲜度，不做跨主机时钟比较，消除 Hub/Agent 时钟偏移误判；
该字段缺失或 <= 0 视为未知并 Fail Closed，不承诺与不下发该字段的旧 Hub 兼容。
Observed At 与 TTL 保留为诊断字段，不参与新鲜度判定。

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

下载有界：Agent 侧下载 Artifact 与自升级二进制共用载荷 HTTP 客户端，设置 15 分钟总时限与 30 秒响应头超时；超时按失败处理并由 `defer` 清理 `.tmp`，不长期残留 0B 临时文件。

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

数据端点方法固定：`/agent/transfers/{id}/upload` 仅 `POST`、`/agent/transfers/{id}/download` 仅 `GET`；其它方法 → 405 + `Allow: POST` / `Allow: GET`；未知动作或多余路径段 → 404（不得被当作已知动作执行）。

目标节点不得等于源节点：`POST /api/transfers` 命中自传（目标=源）时整批拒绝并返回 `400 self transfer is not allowed`，不落任何目标记录；前端裁剪不构成服务端保证。

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
- completed_kind
- backup_path
- app_revision
- binary_path
- config_path
- unit_name
- started_at

`from_version` 是该应用在本次部署前**实际安装**的版本：取最近一次成功部署（`phase=DONE` 且 `completed_kind=deployed`）的 `to_version`；无成功部署记录时为空。回滚成功（`rolled_back`）不构成已安装版本，不参与 `from_version` 取值；`from_version` 不取应用定义的期望版本。

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

`ROLLBACK_FAILED` 表示回滚已尝试且失败；回滚中途崩溃保留 `ROLLING_BACK`。两者在重启恢复时都要经下方防护校验后才决定是否重放。

Agent Crash/Restart 后必须识别半完成部署并恢复到明确状态。

恢复重放回滚前必须通过双重防护，任一命中即不改动文件、不重放回滚，记录落 `DONE` 终态并记录成因（备份文件保留，供人工检查）：
- Superseded：同 application 已存在更晚且 `phase=DONE`、`completed_kind=deployed`（成功部署）的部署；准备阶段失败与回滚成功不构成取代，旧记录缺 `completed_kind` 视为未知，同样不构成取代；
- Binding：Journal 记录的 `app_revision` 与部署时的 `binary_path` / `config_path` / `unit_name` 与当前定义一致；记录缺失或任一不一致视为未知，不重放。

不得用陈旧 backup 覆盖更晚的 DONE 部署。

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

审计记录以逻辑触发为单位：一次 Manual Run 只产生一条 `execute` 审计记录（`resource=task`），与执行内部按 Retry 重试的次数无关；每次 attempt 的细节只体现在执行双流日志（stdout/stderr）中，不产生额外审计行。

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
/api/applications/{id}/nodes
/api/applications/{id}/executions
/api/executions
/api/audit
/api/settings
```

Web UI 调用真实 API。

方法语义：
- `/api/audit`、`/api/me`、`/metrics` 仅接受 `GET`（其它方法 → 405 + `Allow: GET`）
- `/api/logout`、`/api/login` 仅接受 `POST`（其它方法 → 405 + `Allow: POST`；`GET /api/logout` 不再注销，非 `POST` 的 `/api/login` 不再建立会话）
- `/api/tasks/{id}/run` 仅接受 `POST`（`GET`/`PUT` 等 → 405 + `Allow: POST`，且不进入 RunNow、不产生执行记录）
- `/api/applications/{id}/revisions`、`/api/applications/{id}/nodes` 仅接受 `GET`（其它方法 → 405 + `Allow: GET`）；`/api/applications/{id}/assign`、`/api/applications/{id}/deploy` 仅接受 `POST`（其它方法 → 405 + `Allow: POST`）
- `/api/scripts/{id}/revisions`（含 `/{版本号}`）仅接受 `GET`（`PUT` 等 → 405 + `Allow: GET`，不再落入脚本本体写路径）
- `/api/artifacts/{id}/download` 仅接受 `GET`/`HEAD`（其它方法 → 405 + `Allow: GET, HEAD`，且不返回文件内容）
- 其余端点方法不支持一律 405；方法守卫先于角色鉴权与存在性判定，不因权限差异把 405 退化为 403（未认证仍是 401）
- 405 一律附 `Allow` 头（全端点统一，不再有例外；此前 scripts/tasks/schedules/groups/artifacts/applications/login 等端点缺失，缺陷 QA-20261018-F6 已补齐），如 `/api/users`（`GET, POST`）、`/api/users/{id}`（`PUT`）、`/api/executions/{id}`（`GET`）、`/api/nodes/{id}`（`GET, POST, PUT, DELETE`；`revoke-credential` 为 `POST`）、`/api/transfers/{id}`（`GET, POST`；动作子路径为 `POST`）

会话与响应键名：
- `/api/login`（本地密码登录）返回 `{"token": "...", "user": <会话对象>}`；`/api/me` 直接返回会话对象
- 会话对象键名固定 snake_case：`token`、`user_id`、`username`、`role`、`expires`，OIDC 登录可能附带 `avatar`（空值省略）
- 原 PascalCase 键（`Token`/`UserID`/`Username`/`Role`/`Expires`）已废弃，消费方必须按 snake_case 解析

状态码契约：
- `PUT /api/schedules/{id}` / `/api/applications/{id}` / `/api/scripts/{id}` / `/api/tasks/{id}`：id 不存在（请求体合法）→ 404，错误体不回传底层存储错误原文；字段校验失败仍为 400
- `POST /api/schedules`（`type=one_time`）：`run_at` 在过去 → 400 `run_at must be in the future`；`PUT /api/schedules/{id}` 仅在 `run_at` 实际变化时拒绝（未改值的历史过去时间记录放行，仍可编辑/禁用）
- `DELETE /api/scripts/{id}` / `/api/tasks/{id}` / `/api/schedules/{id}` / `/api/groups/{id}` / `/api/artifacts/{id}` / `/api/applications/{id}`：id 不存在 → 404 + 归一化 not found 文案（不再静默返回 200）；客户端删除重试必须按 404 处理
- `/api/nodes/{id}`：`DELETE`/`POST`/`PUT` 先鉴权后存在性——operator/viewer 对存在与不存在的 id 均 403（不通过状态码差异泄露存在性）；admin 对不存在 id → 404 `node not found`
- `POST /api/nodes`（集合写路径）：id 不存在 → 404 `node not found`（状态与标签两路径一致，不再 500 / 静默 200）；status 非法字符串 → 400 `invalid node status: <值>`
- 子资源写端点不存在 id → 404 归一化文案（不再 400 + `sql: no rows in result set`）：`POST /api/nodes/{id}/revoke-credential` → `node not found`；`POST /api/transfers/{id}/retry`、`/cancel` → `transfer not found`；`POST /api/executions/{id}/cancel` → `execution not found`；业务错误（如 transfer 已取消）仍保持 400 + 业务文案
- `PUT /api/users/{id}`：用户不存在 → 404 `user not found`（不再静默 200）；其余持久化失败保持 500
- 资源子路径：`/api/<resource>/{id}/<未知子路径>` 一律 404 + 归一化 not found 文案（任意方法），不落入基础资源分支被读取/改写/删除；已知子路径追加多余段（如 `/run/extra`、`/revisions/1/extra`）同样 404；末尾斜杠归一为同一资源（`/api/tasks/{id}/` 等价于 `/api/tasks/{id}`）
- `/api/users/{id}`、`/api/executions/{id}`：不支持 `DELETE` → 405 + `Allow`（`Allow: PUT` / `Allow: GET`）
- `POST /api/users`：用户名重复 → 400 `username already exists`（不返回数据库层 UNIQUE 原文；并发创建由唯一索引兜底为同文案）
- 存储层与内部错误：SQLITE_BUSY/LOCKED 家族 → 503 `storage busy, please retry`（可重试语义；全写路径归一化，含并发 `DELETE /api/schedules/{id}` 路径）；其余 → 500 `internal error`（OIDC 启动失败等专用路径保留自身文案）；驱动原文只进 Hub 日志，不回响应体，且 Hub 对内部错误写 error 级日志；连接已关闭/客户端取消（`context.Canceled`、`net.ErrClosed`、`io.EOF` 等）仅日志降级（不写 error 级日志），HTTP 响应语义不变
- `POST /api/transfers` 自传（目标=源）→ 400 `self transfer is not allowed`，不落库
- 派发/手动运行命中 disabled 节点 → 400 `node <id> is disabled`
- `POST /api/groups`：name 缺失或空白 → 400；组名忽略大小写与首尾空白判重
- `PUT /api/groups/{id}`：id 不存在 → 404（不做 upsert）

响应形状：
- `GET /api/applications/{id}/nodes`：无绑定节点时返回 `[]`，不返回 `null`

列表分页与 `X-Total-Count`：
- 服务端分页端点只有 `/api/executions` 与 `/api/audit`（唯一带 `limit`/`offset` 的列表），二者返回 `X-Total-Count`（计数失败时省略该头并记日志），并经 `Access-Control-Expose-Headers` 暴露给浏览器
- 其余列表端点（nodes/tasks/scripts/applications/schedules/users/groups/transfers）无服务端分页参数，全量返回、由 Web 客户端分页，不返回 `X-Total-Count`；该边界为有意设计，新增分页属能力扩展

CORS 现状：
- `Access-Control-Allow-Origin: *`；Methods `GET, POST, PUT, DELETE, OPTIONS`；Headers `Content-Type, Authorization, X-NodeSteer-Agent-Token, X-NodeSteer-Agent-ID`；Expose `X-Total-Count`
- 不发送 `Access-Control-Allow-Credentials`，认证走 `Authorization: Bearer`（无 Cookie 会话），浏览器不随请求携带凭据，因此不构成「通配来源 + 凭据」组合
- 按部署形态收紧（同源部署不发 CORS 头或使用来源白名单）需要新增配置项与部署形态裁决，属独立变更

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

Agent SQLite 使用 WAL，连接以 `_txlock=immediate` 打开写事务（BEGIN 阶段即取写锁），配合 `busy_timeout=5000` 与外键约束；避免 WAL 下 deferred 事务「先读后写」升级写锁时不受 busy_timeout 保护的 `SQLITE_BUSY`。

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

部署 journal 只读查询面：恢复结束后 Agent 在 `<data_dir>/deployments-summary.json` 原子输出摘要（非 `deployed` 的 `DONE` 行按 `completed_kind` 计数 + 仍活动行 id/应用/阶段/时间），供运维查看不可重放记录。

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
