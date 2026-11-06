# NodeSteer 完整产品功能文档

版本：v1.1
文档类型：产品功能设计基线
产品阶段：一期实施范围 + 后续高级能力规划

## 1. 产品定位

NodeSteer 是一套面向 Linux 服务器的轻量级 Hub-Agent 自动化管理、任务调度与二进制应用部署平台。

监控与联动是已确认的扩展目标：同一个 NodeSteer Agent 采集节点指标并由 Hub 原生保存历史，统一展示 Linux 节点与外部服务监控；通过人工或规则触发既有节点任务，支持跨节点批次、验证和显式补偿。HTTP(S)、TCP、ICMP 和通用 HTTP 字段采集已接入；数据库、消息队列等服务保留后续扩展边界。Nocturne 已移除，已有 Nocturne 历史不迁移、不展示。当前实现与剩余验收以 `openspec/changes/unified-monitoring-automation/tasks.md` 和实际代码、验证为准。

系统由 Hub 和 Agent 两部分组成。

Hub 提供：
- Web 管理
- 节点与节点组管理
- Script 管理
- 任务定义（Task）管理
- 调度计划（Schedule）管理
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
- 节点指标采集（CPU、内存、负载、磁盘、网络）
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
10. 任务定义（Task）管理与 Revision
11. Manual Run（请求级目标与策略）
12. Cron / Interval / One-Time / On-Start / Manual Schedule
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

### 3.2 Hub-Agent 双向连接

统一通信模型支持两种方向，出站优先：

```text
Agent ─────────────→ Hub Agent Gateway
Hub ───────────────→ Agent :18444 TLS listener
```

连接建立后为双向控制通道：

```text
Hub ⇄ Agent
```

Agent 默认优先主动连接 Hub，不需要开放远程管理端口；当 Hub 位于内网、Agent 位于公网时，管理员签发入站 bundle 后，Agent 开放一个仅接受 Hub mTLS 拨号的 TLS listener，由 Hub 按节点 `endpoint` 主动建立连接。两种方向共用同一协议、注册和在线状态机，每个节点最多一个已认证会话。

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
node_metrics=true
```

Docker 基础模式可以：

```text
managed_systemd=false
application_deploy=false
node_metrics=false
```

`node_metrics` 表示同一 Agent 能读取宿主机指标；Docker 基础模式只能看到容器自身 `/proc`，不声明该能力。Docker Host Integration 和 Native 模式可声明。旧 Agent 不声明时，监控显示不可用，不以零值代替。`node_metrics` 不参与 Task/Deployment 下发，仅用于监控查询和指标规则求值。

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
| 内网 | 公网 | 是，Agent 提供入站 TLS listener，Hub 主动拨号 |
| 内网 A | 内网 B | 网络本身已互通时支持 |
| 隔离内网 A | 隔离内网 B | 一期不负责打通 |

Hub 内网 + 公网 Agent：

```text
Hub（内网）
     ↓ 主动拨号 wss://<node.endpoint>:18444
公网 Agent TLS listener（mTLS + 每节点令牌）
```

Hub Web UI 可以继续仅暴露在内网。入站模式默认关闭；只有管理员签发 bundle 并显式启用后，Agent 才监听 18444/tcp。

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

入站模式额外使用 Agent 的 `18444/tcp`（可配置）：Hub 通过 `wss://<node.endpoint>` 拨号，TLS 最低 1.2，必须通过 mTLS、证书指纹 pin、节点令牌和来源 CIDR 校验。Hub 不需要对该端口做入站映射，只需能出站访问节点 endpoint。

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

Desired State 的任务集合按 `Schedule.Target` 计算：只有被命中该节点的 Schedule 引用的 Task、Script 与 Schedule 才会同步到 Agent。没有被任何 Schedule 引用的 Task 不参与节点同步，但仍可通过 Manual Run 执行。

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

节点支持独立的显示名 `display_name`，与 Agent 上报的 `hostname` 解耦：

- 新节点显示名默认为空，页面回退显示 `hostname`；`hostname` 也为空时回退节点 ID。
- Operator 和 Administrator 可通过 `POST/PUT /api/nodes/{id}` 设置或清除显示名；Viewer 只读。批量 `POST /api/nodes` 不接受显示名。
- 显示名去除首尾空白后最多 64 个 Unicode 码点，拒绝控制字符；空值表示清除。显示名不要求唯一。
- 重新注册、HELLO 元数据刷新和重新纳管不会覆盖或清空显示名。
- 显示名只用于展示，不参与节点身份、Agent ID、hostname 匹配、权限、执行目标或路由；所有内部关联继续使用节点 ID。
- 显示名实际变化时记录 `node.rename` 审计（旧值、新值、操作者和时间）。

节点纳管必须填写 `endpoint`（域名或 IP，缺省端口 `18444`），作为 Hub 主动拨号地址；`endpoint` 与 `node_ip` 分离，仅 Administrator 可修改。系统自动选择出站或入站，`connection_mode` 只读。新增字段：当前/上次连接方式、入站 bundle 状态、证书指纹/有效期、最近握手/错误。入站数据面支持 Hub pull 源文件、push 目标文件、push Artifact 与 Agent 二进制；只有真实能力缺失或阶段不可用时才返回明确 409/BLOCKED。

Node 页面显示至少：
- Node ID
- Display Name
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
- `timeout < 0` → 400；`timeout == 0` 归一为默认 300s（与 Schedule、Manual Run 策略和 DB 默认值一致）
- `content` 上限 256 KiB（262144 字节，按字节数判定；超限 → 400）
- `script.timeout` 暂不参与执行超时；执行超时以 Schedule.Timeout 或 Manual Run 请求中的 `timeout` 为准

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

UI 名称：任务定义。

Task 是可复用的执行内容定义，只描述「执行什么」，不绑定节点、触发器或执行策略。同一 Task 可以被多个 Schedule 引用。

一期字段：
- Name
- Type
- Parameters / Param Values
- Script / Command / Application 与 App Operation
- Condition
- Enabled
- Revision
- Created At / Updated At

Task 不包含：
- Target（节点/分组/标签）
- Timeout
- Retry
- Run User
- Offline Policy
- Schedule

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

节点绑定与执行策略统一由 Schedule 承载。Task 详情只读展示引用它的 Schedule 与执行历史；创建 Task 不需要选择目标节点。

---

## 22. Manual Run

Web 提供 Run Now（任务定义页「试运行」）。

Manual Run 使用请求级临时目标与执行策略，不修改 Task 或 Schedule：

- `schedule_id`：可选；从调度计划页「立即执行」时传入，使用该 Schedule 的目标与策略，并把来源写入 Execution
- `target`：node / group / label 临时目标
- `node_ids`：兼容旧请求的显式节点集合，等价于构造 node Target
- `params`：参数覆盖
- `timeout` / `retry` / `run_user` / `offline_policy`：本次执行的策略

目标解析优先级：`schedule_id` > `target` > `node_ids` > 400；提供 `schedule_id` 时忽略请求级 target/策略。未提供目标或目标解析不出任何节点时返回 400，不创建 Execution。策略缺省值：300 秒、0 次重试、当前用户、Hub Online Required。

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

同一 Manual Run 对每个目标节点创建一条 Execution；应用类 Task 仍走应用部署/操作链路，并携带本次请求的策略。

一期 Agent Offline 时 Manual Run 不进入长期等待队列，明确返回不可执行。

---

## 23. Schedule

UI 名称：调度计划。

Schedule 是任务、节点目标与执行策略的唯一绑定入口。

一期字段：
- Name：可选；缺省由任务名 + 触发器摘要生成
- Task ID
- Target：Single Node / Multiple Nodes / Node Group / Label
- Type：Cron / Interval / One-Time / On-Start / Manual
- Expression / Interval / Run At
- Timezone（必须保存 IANA Timezone）
- Execution Owner
- Timeout：默认 300 秒；`0` 表示使用默认值；负数拒绝
- Retry：默认 0；负数拒绝
- Run User：可空；空表示当前用户
- Offline Policy
- Misfire Policy
- Enabled
- Revision

`manual` 类型不自动触发，只承载「任务 + 目标 + 策略」，用于固定目标绑定和旧数据迁移；Hub 与 Agent 的自动调度器都跳过 manual。manual 不要求 expression、interval 或 run_at。

同一 Task 可以被多个 Schedule 引用；不同 Schedule 的目标、策略和触发器互不覆盖。

One-Time 的 `run_at` 必须晚于当前时间：Create 时过去时间 → 400 `run_at must be in the future`；Update 仅在 `run_at` 实际变化时校验，未修改的历史过去时间记录仍可编辑/禁用，不因该校验被锁死。

创建或更新 Schedule 时必须提供可解析的 Target；缺失或无法解析 → 400。

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

`manual` Schedule 不参与自动触发，因此不产生 owner 之间的自动触发竞争；手动执行时仍使用该 Schedule 的目标与策略。

---

## 25. Offline Policy

一期策略由 Schedule 或 Manual Run 请求提供：
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

条件归属：
- Task.Condition 只承载本地条件，作为任务自身的安全执行前提，随任务复用。
- Schedule.Condition 只承载远程条件，作为调度依赖。
- 调度执行时合并为 `local AND remote`，本地条件先评估、远程条件后评估。
- 任务页「试运行」只评估 Task 本地条件；调度页「立即执行」评估 Task 本地 + Schedule 远程条件。
- 应用类任务的手动执行同样经过条件评估，不因部署/操作路径绕过。

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

远程条件归属 Schedule，不写入 Task。任务页试运行不使用远程条件；调度计划页「立即执行」使用该调度的远程条件。

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

UI 名称：执行记录。

一期记录：
- Execution ID
- Schedule ID（可空；调度触发时记录来源，Manual Run 与历史记录为空）
- Schedule Revision（可空；调度触发/调度页立即执行时记录来源版本，任务页 Manual Run 为 0）
- Task ID
- Task Revision
- Script Revision
- Node ID
- Trigger Type
- Scheduled Time
- Created At
- Start Time
- End Time
- Status
- Exit Code
- stdout / stderr（含截断标记）
- Offline / Synced
- Block Reason

状态：
- PENDING
- RUNNING
- SUCCESS
- FAILED
- SKIPPED
- CANCELED
- TIMED_OUT
- BLOCKED

执行记录同时承载「执行过程」与「执行结果」：

- 执行过程：Created At / Start Time / End Time / Status / Block Reason 构成的时间线；PENDING、RUNNING 等进行中状态同样可见，并保留实时日志。
- 执行结果：终态状态、耗时、退出码、stdout/stderr 摘要与截断标记。
- 来源调度：调度触发的执行展示 Schedule 名称与跳转；Manual Run 和历史记录显示为空或「历史记录/未知来源」。

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

Schedule ID 不进入幂等 Key。同一 Task 的多个 Schedule 命中同一节点与同一 Scheduled Time 时仍视为同一槽位，保持既有幂等语义；Execution 记录来源 Schedule ID 仅用于展示与排障。

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

Journal 与上报包含可空 `schedule_id` 与 `created_at`：调度触发时记录来源调度，Manual Run 为空，历史记录为空。

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

入站模式：Hub 通过 Agent 的 HTTPS 数据面推送 Artifact（`PUT /agent-data/v1/artifacts/{id}`），Agent 按 SHA-256 写入本地内容寻址缓存；控制消息只携带 `transport=inbound_push` 和元数据，不依赖 Hub base URL。

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

入站模式：源 Agent 为 inbound 时由 Hub 通过 `GET /agent-data/v1/transfers/{id}/source` 拉取到 staging；目标为 inbound 时由 Hub 通过 `PUT /agent-data/v1/transfers/{id}/target` 推送。混合方向仍以 Hub staging 中转，支持 offset 续传、大小/SHA-256 校验、临时文件和原子替换。

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

入站模式下 `ArtifactURL` 为空，Agent 只从本地 Artifact 缓存取文件；缓存缺失时部署返回 `BLOCKED artifact_not_staged`，不得尝试访问不可达的 Hub URL。自升级由 Hub 先推送二进制到 Agent 固定 staging，再由 `RUN_EXECUTION(agent_binary_staged=true)` 触发本地替换。

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
Monitoring
Rules（联动规则）
Nodes
Groups
Scripts
任务定义（Task）
调度计划（Schedule）
Applications
Artifacts
执行记录（Execution）
Settings
```

### 43.1 页面结构、工具栏与操作位置

全站列表页统一采用四段式结构，顺序固定：

1. 页头：标题在左，主创建或主操作在右，优先复用 `NodeSteerHeader` 的 action 插槽；
2. 表格工具栏：搜索在左，筛选紧随搜索之后，视图选项、批量操作、导出等次要操作在右侧；
3. 内容表格；
4. 底部分页。

统一要求：

- 不新增手动刷新按钮；保留现有自动轮询、加载态和错误重试。
- 列表页的工具栏、表格/列表、空态与分页统一置于同一张 `Card` 内；加载与错误态使用同一规范的状态卡片。
- 空态使用统一 `EmptyState`，已有页头创建入口时不得在空态重复创建 CTA；无页头创建入口的页面可在空态提供恢复操作。
- 检索、筛选、列显隐、排序、分页和每页数量沿用现有 `DataTable` 能力与 storageKey 持久化。
- 危险操作（删除、停止、取消、撤销凭据）不与主创建/主操作并排；统一进入 overflow 菜单或独立危险区域，并使用统一确认弹窗。取消确认不得发起业务请求。
- 页头主操作统一使用黑色 `header-action` 按钮（近黑底白字，深色主题使用深灰底和可见边框，焦点环可见）；品牌蓝 `primary` 只用于表单内提交等非页头主操作。危险操作保持 `destructive` 红色，次要操作保持 `outline`。
- 桌面最低支持 1920×1080；在该分辨率下不得出现横向滚动、按钮重叠或右侧空列。主操作保持页头右侧，窄屏可换行但不改变阅读顺序。移动端使用既有抽屉和堆叠布局。
- 页面级主操作只保留一个；次要操作和视图选项放在工具栏右侧，不重复放置。
- 不改变以 URL query 为状态源的页面行为：执行记录继续保留 query 筛选和服务端分页；文件传输继续保留 3 秒轮询；节点批量操作继续使用底部浮动条与既有键盘行为；审计空结果仍保留搜索和分页。

### 43.2 配色与状态语义

- 全站使用分层 token：primitives → semantic → domain；业务页面禁止硬编码颜色和 Tailwind 原始调色板类。
- 默认启用品牌蓝主色与独立侧栏 surface；图表使用冷色/中性色 `series-1..16`，告警/错误色物理分离。
- 状态语义：pending/uploading/delivering/running → info；maintenance/stale → warning；critical/firing → danger；resolved → success；silenced/unknown/空值 → neutral。
- 黄色、橙色、红色只用于告警、错误、危险；Swap、CPU iowait、磁盘写等普通指标不得使用告警色。
- 普通/告警图表色板、对比度门槛和旧配色回滚方式见 `docs/UI_GUIDELINES.md` 与 `docs/CHART_PALETTE.md`。
- 默认配色为 **Azure Pop（A）**（`azure-theme.css` + `data-palette='azure'`）；`spectrum`（E）、上一版品牌蓝和更早旧色分别通过 `web/src/config/palette-mode.ts` 的 `PALETTE_MODE='spectrum'` / `'new'` / `'legacy'` 回滚，不提供设置页开关。

明确例外：

- 告警页三个 Tab 各自保留创建按钮，位置统一到 Tab 内容顶部右侧，并使用黑色 `header-action` 主按钮，不提升到页头。
- 编辑页的保存、上传等提交按钮仍在表单底部，不提升到页头。
- 设置页保存属于表单操作，仍留在表单内。
- 节点监控的时间范围选择保留在图表内容区。
- 详情页的危险操作（停止、取消等）放入 overflow 菜单或独立危险区域，不与返回、编辑等主操作并排。

Dashboard：
- 顶部按单列分区：自动化控制（运行中/执行失败/成功率、正在执行的任务）在上，节点监控概览（在线节点/有指标节点/活动告警、一行式节点指标表）在下；两区分别占满主内容区可用宽度，保留侧栏、页边距与 2400px 最大内容宽度；活动告警汇总尚未接入，显示“未接入”，不将占位值显示为健康结论
- 下方：托管应用状态、同步状态、最近执行、即将执行的调度
- 左侧导航分为“自动化控制（任务/调度/执行/联动规则）”“监控（节点监控/服务监控/告警）”“节点（节点/分组）”等分组
- 节点指标不可用、无样本或过期时显示不可用/等待，不以 0 代替

Monitoring（导航分组：节点监控 / 服务监控 / 告警）：
- 节点监控页（`/monitoring`）：
  - 节点概览：仪表盘与 `/monitoring` 共用一行式表格，直接遍历 NodeSteer 节点，显示在线状态、CPU、内存、磁盘、负载 1/5/15、网络、温度、运行时长和最近采样；不维护“监控节点绑定”。活动告警汇总尚未接入，显示“未接入”。1920px 及以上负载/网络/温度固定在进度条右侧同一行，行高约 60–65px，不得出现右侧空列；低于 1920px 允许堆叠，390px 保持流式回退且不横向溢出。
  - 节点详情（`/monitoring/nodes/{id}`）：顶栏显示节点状态和主机信息（实际 inventory hostname、运行时长、发行版、内核、CPU、内存，缺失显示未知，不以可配置节点名冒充 hostname）、时间范围和节点操作；CPU 默认显示总量并可展开分核，内存显示五条独立曲线，文件系统按挂载点和设备显示真实已用和总容量，IOPS 仅显示已确认整块设备。容器图按稳定 ID 显示各容器曲线及采集完整性；旧身份和未知设备分类明确提示。图表保留 gap/断点，不补 0。
  - 时间范围：1h/3h/6h/12h/24h/7d + 自定义范围（最大 30 天），默认 1h。
  - 节点操作：跳转 `/agents/{id}`，复用现有节点详情、任务和调度入口；`/agents` 列表不再重复显示监控卡，详情顶部显示一行监控概览。
- 服务监控页（`/monitoring/services`）：
  - 外部目标：HTTP(S)、TCP、ICMP 探测，记录探测位置、可用状态与响应耗时。
  - 观察记录：最新观察与历史观察，包含状态、值、错误与耗时。
  - 服务目标管理：创建/编辑/删除、凭据引用、采集间隔/超时/最大延迟。
- 告警页（`/monitoring/alerts`）：
  - 规则页签：节点/指标/序列、操作符/阈值、持续时间、恢复时间、冷却、severity（info/warning/critical）、通知渠道、启停、静默和事件查看；评估周期默认 30s，无样本/过期/离线不触发。
  - 通知渠道页签：webhook、Slack、钉钉、企业微信、Telegram、Email（SMTP）；敏感值只保存环境变量名（`url_env`/`token_env`/`password_env`/`to_env`），API 不回显；支持测试发送。
  - 事件页签：pending/firing/resolved/silenced/unknown；支持确认（ack）和静默；告警只通知/记录，不触发任务。
- 自动化控制（`/rules`）：
  - 联动规则：基于监控目标/原生指标的条件触发、条件恢复、事件及关联执行。
  - 人工触发（复用现有 Task 手动执行）与规则自动触发。
  - 跨节点批次、验证与显式补偿；未知结果停止自动推进并要求人工确认。

一期 Monitoring 页面、告警页、通知渠道和 `/api/monitoring/*` 仅 Administrator 可见；普通角色直接访问相关页面时，在发起监控请求前显示权限反馈；Operator/Viewer 的只读访问不在本期范围。Node 指标不可用、无样本或样本过期时显示不可用/等待，不以 0 代替。

Node Detail：
- 顶部一行监控概览（与 `/monitoring` 节点表一致）；`/agents` 列表不再重复显示监控卡
- Overview
- Metrics（原生指标与历史）
- Inventory
- Labels
- 调度计划（按 Schedule.Target 命中该节点）
- 调度引用的任务定义
- Managed Applications
- 执行记录
- Sync

Script：
- Create/Edit/Clone
- Enable/Disable/Delete
- Revision History

任务定义（Task）：
- Create/Edit
- Enable/Disable
- Run Now（试运行：选择临时目标与策略）
- Parameters
- Condition
- 关联调度（只读）
- Execution History
- 不提供 Target、Timeout、Retry、Run User、Offline Policy 编辑
- 删除

调度计划（Schedule）：
- Create/Edit
- Name
- Task
- Target（节点/分组/标签）
- Type（Cron / Interval / One-Time / On-Start / Manual）
- Expression / Interval / Run At / Timezone
- Execution Owner
- Timeout / Retry / Run User
- Offline Policy / Misfire Policy
- Enable/Disable
- Run Now（使用调度自身目标与策略）
- Execution History（详情显示近期执行，查看全部按来源 Schedule 精确筛选）
- 删除

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

执行记录（Execution）：
- Metadata（含来源 Schedule）
- 执行过程（Created At / Start Time / End Time / Status / Block Reason 时间线）
- 执行结果（状态、耗时、退出码、stdout/stderr 摘要与截断标记）
- 日志（实时/离线、stream 筛选、搜索、跟随、换行、复制）
- Offline / Synced
- Revision
- Cancel（仅 RUNNING）

所有状态必须来自真实后端，不允许永久 Fake Data。

---

## 44. 权限

一期：
- Administrator
- Operator
- Viewer

Administrator：全部权限，含原生监控页面、节点指标历史、告警/通知渠道与监控规则配置。

Operator：
- 查看
- Run Task / Manual Run
- 查看日志
- Deploy Application
- 操作 Managed Application

Viewer：只读。

入站连接相关：修改节点 `endpoint`、下载/轮换入站 bundle、测试连接仅限 Administrator；Operator/Viewer 可查看 endpoint、连接方向、证书指纹和有效期，任何角色都不能读取令牌明文或私钥。

一期监控页面与 `/api/monitoring/*` 仅 Administrator 可用，Operator/Viewer 的只读访问不在本期范围。

---

## 45. Audit

一期至少记录：
- Login
- Script Create/Modify/Delete
- 任务定义（Task）Create/Modify/Delete/Execute
- 调度计划（Schedule）Modify
- Artifact Upload
- Application Create/Modify/Deploy/Upgrade
- Node Enable/Disable
- 监控 target/rule Create/Modify/Delete
- 告警规则/通知渠道 Create/Modify/Delete、测试发送、静默、事件确认

监控规则触发执行通过 `monitoring_rule_events` 追溯，事件保存规则快照、观察值及关联 execution；该链路不重复写 `audit_logs`。节点指标采集本身不逐点写审计。告警只通知/记录，不触发任务；告警规则/渠道/确认/静默/测试发送写审计。

审计记录以逻辑触发为单位：一次 Manual Run 只产生一条 `execute` 审计记录（`resource=task`），与执行内部按 Retry 重试的次数无关；每次 attempt 的细节只体现在执行双流日志（stdout/stderr）中，不产生额外审计行。

---

## 46. REST API

API-first。

`GET /api/executions` 支持可选 `schedule_id`、`node_id` 精确过滤，可组合使用，并保持分页列表与总数一致；不传或空值保持原查询行为。

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
/api/monitoring/latest
/api/monitoring/http-targets
/api/monitoring/rules
/api/monitoring/nodes/overview
/api/monitoring/nodes/{id}/series
/api/monitoring/nodes/{id}/metrics
/api/monitoring/nodes/{id}/latest
/api/monitoring/alerts/rules
/api/monitoring/alerts/events
/api/monitoring/alerts/events/{id}/ack
/api/monitoring/alerts/rules/{id}/silence
/api/monitoring/notification-channels
/api/monitoring/notification-channels/{id}/test
```

Web UI 调用真实 API。`/api/monitoring/nodes/*` 原生查询 API 已实现；Nocturne 后端、路由、配置和绑定已移除，遗留 `monitoring_node_bindings` 表保留但不参与新链路。告警 API 和通知渠道仅 Administrator 可用；`overview` 的告警数当前为占位 0，规则告警汇总未接入。

方法语义：
- `/api/audit`、`/api/me`、`/metrics` 仅接受 `GET`（其它方法 → 405 + `Allow: GET`）
- `/api/logout`、`/api/login` 仅接受 `POST`（其它方法 → 405 + `Allow: POST`；`GET /api/logout` 不再注销，非 `POST` 的 `/api/login` 不再建立会话）
- `/api/tasks/{id}/run` 仅接受 `POST`（`GET`/`PUT` 等 → 405 + `Allow: POST`，且不进入 RunNow、不产生执行记录）
- `/api/applications/{id}/revisions`、`/api/applications/{id}/nodes` 仅接受 `GET`（其它方法 → 405 + `Allow: GET`）；`/api/applications/{id}/assign`、`/api/applications/{id}/deploy` 仅接受 `POST`（其它方法 → 405 + `Allow: POST`）
- `/api/scripts/{id}/revisions`（含 `/{版本号}`）仅接受 `GET`（`PUT` 等 → 405 + `Allow: GET`，不再落入脚本本体写路径）
- `/api/artifacts/{id}/download` 仅接受 `GET`/`HEAD`（其它方法 → 405 + `Allow: GET, HEAD`，且不返回文件内容）
- 其余端点方法不支持一律 405；方法守卫先于角色鉴权与存在性判定，不因权限差异把 405 退化为 403（未认证仍是 401）
- 405 一律附 `Allow` 头（全端点统一，不再有例外；此前 scripts/tasks/schedules/groups/artifacts/applications/login 等端点缺失，缺陷 QA-20261018-F6 已补齐），如 `/api/users`（`GET, POST`）、`/api/users/{id}`（`PUT`）、`/api/executions/{id}`（`GET`）、`/api/nodes/{id}`（`GET, POST, PUT, DELETE`；`revoke-credential` 为 `POST`）、`/api/transfers/{id}`（`GET, POST`；动作子路径为 `POST`）

运行模型契约（Task / Schedule / Execution）：

- REST 路径与资源名保持不变；Task、Schedule、Execution 的 JSON 字段按新模型调整。
- Task 响应只包含执行内容：`id`、`name`、`type`、`parameters`、`param_values`、`script_id`、`command`、`application_id`、`app_operation`、`condition`（仅本地条件）、`enabled`、`revision`、`created_at`、`updated_at`；不得再返回 `target`、`timeout`、`retry`、`run_user`、`offline_policy`、`schedule`。
- Schedule 响应包含 `task_id`、`name`、`target`（`type=node|group|label`，以及对应的 `node_ids`、`group_ids`、`label_key`、`label_value`）、`condition`（仅远程条件）、`type`（`cron|interval|one_time|on_start|manual`）、`expression`、`interval_sec`、`run_at`、`timezone`、`execution_owner`、`timeout`、`retry`、`run_user`、`offline_policy`、`misfire_policy`、`enabled`、`revision` 与时间戳。
- `POST /api/schedules` / `PUT /api/schedules/{id}`：缺少有效 `target` → 400；`timeout`、`retry` 为负数 → 400；`type=manual` 不要求表达式、间隔或 `run_at`；`condition` 含本地条件 → 400。
- `POST /api/tasks/{id}/run` 接受 `schedule_id`、`target`、`node_ids`、`params`、`timeout`、`retry`、`run_user`、`offline_policy`；目标解析优先级 `schedule_id` > `target` > `node_ids`，都没有或解析为空 → 400；请求不修改 Task 或 Schedule。`schedule_id` 请求使用该调度远程条件；任务页请求只使用 Task 本地条件。
- Execution 响应新增可空 `schedule_id`、`schedule_revision` 与 `created_at`；既有状态、`exit_code`、`stdout`/`stderr`、截断标记、`offline`、`synced`、`block_reason` 字段保持。

会话与响应键名：
- `/api/login`（本地密码登录）返回 `{"token": "...", "user": <会话对象>}`；`/api/me` 直接返回会话对象
- 会话对象键名固定 snake_case：`token`、`user_id`、`username`、`role`、`expires`，OIDC 登录可能附带 `avatar`（空值省略）
- 原 PascalCase 键（`Token`/`UserID`/`Username`/`Role`/`Expires`）已废弃，消费方必须按 snake_case 解析

状态码契约：
- `PUT /api/schedules/{id}` / `/api/applications/{id}` / `/api/scripts/{id}` / `/api/tasks/{id}`：id 不存在（请求体合法）→ 404，错误体不回传底层存储错误原文；字段校验失败仍为 400
- `POST /api/schedules`（`type=one_time`）：`run_at` 在过去 → 400 `run_at must be in the future`；`PUT /api/schedules/{id}` 仅在 `run_at` 实际变化时拒绝（未改值的历史过去时间记录放行，仍可编辑/禁用）
- `POST /api/schedules` / `PUT /api/schedules/{id}`：缺少有效 `target` → 400；`timeout`、`retry` 为负数 → 400
- `POST /api/tasks/{id}/run`：请求既无 `target` 也无 `node_ids`，或解析不出任何目标节点 → 400，不创建 Execution，不修改 Task/Schedule
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

原生监控与联动相关的 Rule/Event、跨节点编排和显式补偿已纳入已确认的扩展范围，具体实现状态以 `unified-monitoring-automation` change 和实际行为为准；以下列表的其他能力仍维持原有范围。

- Workflow DAG
- Task Dependency
- Parallel Execution
- Conditional Branch
- 通用 Rule Engine（原生监控规则除外）
- 通用 Event Engine（原生监控事件除外）
- Self-Healing（已确认的原生监控联动及显式补偿除外）
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
- Rule Engine（通用能力仍为后续；原生监控规则在一期以专用规则承载，节点指标原生数据源已接入）
- Event Engine（通用能力仍为后续；原生监控事件在一期以专用规则承载）
- Self-Healing（通用能力仍为后续；原生监控联动及显式补偿在一期承载）
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
18. Task 只描述执行内容，不承载目标与执行策略。
19. Schedule 是任务、节点目标与执行策略的唯一绑定入口。
20. Desired State 按 Schedule.Target 计算；无 Schedule 引用的 Task 不单独同步，但仍可 Manual Run。

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
