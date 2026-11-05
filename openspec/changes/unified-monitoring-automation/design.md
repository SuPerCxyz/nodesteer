## Context

NodeSteer 的 Task 负责执行内容，Schedule 负责绑定任务、目标和执行策略；manual Schedule 不由既有调度器自动执行。监控需要覆盖节点自身指标和外部服务；原实现依赖独立 Nocturne 服务，节点身份、认证、历史存储和许可证均与 NodeSteer 分离。新架构改为一个 NodeSteer Agent 同时采集指标和执行自动化，Hub 原生保存时序数据；Nocturne 已移除。参见 proposal。

**当前实现状态**：Agent 指标采集、`NODE_METRICS` 协议和 `node_metrics` 能力已落地；Hub schema v23、原生存储/聚合/后台清理、gateway ingest、`/api/monitoring/nodes/*` 查询 API、`node_metric` 原生规则和前端总览/历史均已接入；Nocturne 后端、路由、配置和绑定已移除，遗留 `monitoring_node_bindings` 表保留但不参与新链路。最终回归、真实大规模压测、ACK/重传确认、overview N+1/索引/节点删除清理和完整 E2E 仍在进行。未验证项在 `tasks.md` 保持未勾。

## Goals / Non-Goals

**Goals:** 用一个 Agent 完成节点指标采集与任务执行；由 Hub 原生保存、聚合和查询历史；以 NodeSteer 页面展示节点总览、历史和告警；让 `node_metric` 规则使用原生样本；保留外部探测、规则、编排、验证和补偿能力。

**Non-Goals:** 本次不交付数据库或消息队列采集器，不移植 Nocturne UI 或历史，不实现推测任意脚本逆操作，不改变 Task/Schedule 的既有职责，不把指标采集扩展成通用日志/追踪平台。

## Decisions

### 1. 一个 Agent，采集与执行共用连接

- 同一个 NodeSteer Agent 既执行任务，也采集 Linux 节点指标；不新增第二个监控 Agent。
- Agent 在 HELLO/Heartbeat 声明 `node_metrics` 能力。Native 模式、Docker Host Integration 模式支持；Docker 基础模式只能看到容器自身 `/proc`，不声明该能力。
- `metrics_enabled` 默认 true，`metrics_interval` 默认 30s，运行时限制在 5s–300s；零值回退 30s。
- 旧 Agent 不声明 `node_metrics` 时，Hub/Web 显示“指标不可用”，不生成零值、不推断健康状态，也不触发依赖指标的规则。当前 API 以无样本状态表达不可用；显式能力检查和规则原生链路待接入。
- 采集失败（如 `/proc` 不可读）产生 `quality=missing` 的缺失样本或该轮无样本；不得用 0 代替未知。

### 2. `NODE_METRICS` 批量协议

```text
NODE_METRICS
  node_id
  sequence        # 同一 Agent 进程内单调递增
  samples[]
    timestamp     # UTC
    metric
    dimensions    # 可选 map<string,string>
    value         # number 或 null
    quality       # 1=valid, 2=missing
```

- 单批最多 500 个样本；一轮采集超过上限时拆批。
- 发送失败或断线时进入有界内存队列（最多 10 批），恢复后按入队顺序重传；容量溢出丢弃最旧批次并记录日志，缺口必须可见，不得静默补零。
- Hub 以 `node_id + metric + dimensions + timestamp` 去重；`sequence` 用于排序、诊断和识别乱序/重复。目标上冲突样本不得静默覆盖已确认的有效点；当前存储层使用 UPSERT，ingest 层仍需补冲突检测和审计。
- 协议为增量消息，不改变既有消息语义；部署时先升级 Hub 再升级 Agent。旧 Agent 继续执行任务，但无指标。

### 3. 指标名、维度与质量

| Metric | Unit | Dimensions | 说明 |
|---|---|---|---|
| `cpu.usage` | % | `cpu=all` 或 `cpu=<n>` | 基于 `/proc/stat` 差值；首次采集只建立基线 |
| `memory.usage` | % | 无 | `(MemTotal - MemAvailable) / MemTotal`；缺失时 quality=missing |
| `load.1` / `load.5` / `load.15` | 1 | 无 | `/proc/loadavg` |
| `disk.usage` | % | `mount`, `device` | 跳过伪文件系统、只读挂载和 `/proc`、`/sys`、`/dev`；最多 32 个挂载点 |
| `network.rx_bps` / `network.tx_bps` | B/s | `interface` | 基于 `/proc/net/dev` 差值；跳过 `lo`；计数器回绕时跳过该周期 |

- `quality=1` 表示有效值；`quality=2` 表示缺失，`value` 必须为 `null`。
- 真实零值（0%）是有效样本，与缺失、无数据、采集失败严格区分。
- 无样本表示 gap，不表示 0；查询和图表必须在 gap 处断线。
- 时间戳由 Agent 提供并统一为 UTC；时钟偏差会影响新鲜度判断，部署需保持 NTP 同步。当前存储未保存接收时间，诊断依赖节点状态和样本时间。

### 4. Hub 原生 SQLite 时序存储、聚合与保留

- raw 样本写入 `node_metric_samples`（schema v23），按 `node_id`、`metric`、维度哈希和时间建索引。
- 聚合层：`1m`、`5m`、`1h`，每桶保存 count、avg、min、max、quality 汇总和缺口信息；1m 由 raw 聚合，5m 由 1m 聚合，1h 由 5m 聚合。
- 默认保留：raw 7d、1m 90d、5m 1y、1h 2y；目标可配置，当前实现为代码常量，尚无配置项。清理任务周期性删除过期数据，禁止无限增长。
- 迟到、重复或更正样本只影响对应时间桶；聚合必须可重算，不能把旧桶永久固化。当前聚合实现从已有最大桶向前重叠两个桶重算。
- 查询按时间范围选择能覆盖该范围的最细 tier；跨 tier 时允许 raw 精化边界。返回 `state`、`tier`、`stepNs`、`points`、`gaps`，缺失与无效点保留原始语义。
- SQLite 继续使用 WAL、`_txlock=immediate`、`busy_timeout` 和外键约束；大批量写入和清理不得长时间阻塞 API。

### 5. 查询 API 与前端

目标 API（查询接口和 `NODE_METRICS` ingest 已接入）：

```text
GET /api/monitoring/nodes/overview          # 每个 NodeSteer 节点的当前状态与最新指标
GET /api/monitoring/nodes/{id}/series       # 指标与维度序列
GET /api/monitoring/nodes/{id}/metrics      # 范围历史，返回 tier/points/gaps
GET /api/monitoring/nodes/{id}/latest       # 最新样本
```

- `overview` 直接遍历 NodeSteer 节点，返回在线状态、CPU、内存、负载、磁盘、网络、最近采样时间、状态和告警数；不返回“绑定”概念。当前告警数仍为占位 0，规则告警汇总待接入。
- Web 总览使用现有 NodeSteer 布局、组件和样式；点击节点查看历史；节点操作跳转 `/agents/{id}`。
- 无 `node_metrics` 能力、无样本或样本过期时显示不可用/等待数据，不显示 0。
- 历史默认 1h，支持 1h/6h/24h/7d 和指标/维度筛选；图表按 gap 断线，质量列区分有效、缺失和不完整。

### 6. 规则使用原生样本

- `node_metric` 目标已改为引用 NodeSteer `node_id`、`metric` 和 series/dimensions，不再保存 Nocturne 节点 ID 或序列。
- Hub 从原生样本（或写入后的持久化样本）求值；不得把展示摘要当作触发依据。
- 保留现有语义：新鲜度/最大样本延迟、持续时间、条件恢复、冷却、触发去重、gap/缺失/乱序/冲突中断在途事件、失败暂停、未知结果人工确认或放弃、验证与显式补偿。
- 外部 HTTP(S)、TCP、ICMP 仍是独立监控对象，探测位置、凭据、CIDR、TLS 和超时边界不变；数据库/消息队列仅保留 target kind 边界，不声称已实现。
- 采样与执行之间仍存在时间窗口；不得声称严格一次执行或任意脚本可自动回滚。

### 7. 移除 Nocturne

- Nocturne 后端、路由、配置、绑定 API 和页面入口已移除。已有 Nocturne 历史不迁移、不回填、不在新界面展示。旧 `monitoring_node_bindings` 表保留为遗留数据，但不再被新链路读取；是否物理删除由后续独立迁移决定。
- 这是架构变更，不复制 Nocturne 代码或历史；NodeSteer 保持 MIT。

## Risks / Trade-offs

- 时序数据持续增长 → 分层聚合、保留期、索引和周期清理；容量规划按节点数 × 指标数 × 采样频率评估。
- 迟到/重复/乱序样本导致聚合和规则误判 → 幂等去重、tier 重算、gap 和冲突中断；不把缺失当零。
- Agent 时钟偏差或断线重传 → 保持 NTP、缺口可见；目标上记录接收时间用于诊断，当前存储未保存接收时间。
- 旧 Agent 无指标能力 → 明确显示不可用，规则不触发；部署先升级 Hub 再升级 Agent。
- 移除 Nocturne 影响已有部署 → 配置和页面入口已移除，旧历史不迁移；升级前备份 Hub DB，回退恢复备份。
- 规则与执行之间仍有时间窗口 → 保留显式验证、补偿和未知状态人工处置，不声称严格一次执行。

## Migration Plan

- schema v23 已加入原生时序表和索引；不复制、不回填 Nocturne 历史。
- Nocturne 配置、代理、绑定 API 和页面入口已删除；遗留 `monitoring_node_bindings` 表保留但不参与新链路。
- 升级前备份 Hub DB 和 Agent 数据；先升级 Hub，确认迁移和健康检查，再升级 Agent。
- 回退恢复升级前 Hub DB 备份和旧二进制；旧 Nocturne 数据保持原状，不随 NodeSteer 回退迁移。
- 最终回归和 P3/E2E 未完成前，不得在部署文档中声称全部验证通过。
