# unified-monitoring Specification

## Purpose
Provide native monitoring for NodeSteer-managed Linux nodes and external services, using one Agent for metric collection and automation, Hub-owned metric history, and the existing NodeSteer page templates.
## Requirements
### Requirement: Unified monitoring presentation
The system SHALL present node monitoring, history and external service monitoring within the existing NodeSteer page templates, components and styling, without reusing the previous Nocturne page style or requiring a Nocturne page.

#### Scenario: Node metrics
- **WHEN** an authorized user opens a monitored node
- **THEN** the system shows its current metrics, history, events and related task records using the existing NodeSteer interface

#### Scenario: Node operation
- **WHEN** the user chooses a node action from monitoring
- **THEN** the system navigates to the existing node detail at `/agents/{id}` rather than a monitoring-specific node page

### Requirement: Single Agent metric collection
The system SHALL collect CPU, memory, load, disk and network metrics through the same NodeSteer Agent that executes tasks. The Agent SHALL declare a `node_metrics` capability, default to a 30-second interval, and support a bounded interval range of 5 to 300 seconds.

#### Scenario: Native Agent reports metrics
- **WHEN** a Native or Docker Host Integration Agent with metrics enabled connects
- **THEN** it declares `node_metrics` and reports samples on its configured interval

#### Scenario: Metrics unavailable
- **WHEN** metrics are disabled or the deployment mode cannot read host metrics
- **THEN** the Agent does not declare `node_metrics` and the system shows metrics as unavailable instead of fabricating values

#### Scenario: Old Agent
- **WHEN** a connected Agent does not declare `node_metrics`
- **THEN** the node remains manageable and executable, but its monitoring status is unavailable and metric-based rules do not trigger

### Requirement: Native metric protocol and quality
The system SHALL accept `NODE_METRICS` batches containing `node_id`, `sequence`, `timestamp`, `metric`, `dimensions`, `value` and `quality`. Quality `1` SHALL mean a valid value and quality `2` SHALL mean missing with a null value. A real zero SHALL remain a valid sample and SHALL NOT be treated as missing.

#### Scenario: Missing sample
- **WHEN** a collector cannot read a metric
- **THEN** the Agent reports a missing sample with a null value and quality `2`, or no sample for that round, rather than reporting zero

#### Scenario: Zero value
- **WHEN** CPU usage is genuinely 0 percent
- **THEN** the sample is stored and displayed as a valid zero, distinct from a gap or missing value

#### Scenario: Out-of-order or duplicate samples
- **WHEN** a batch is retried, duplicated or arrives out of order
- **THEN** the Hub deduplicates and orders samples without overwriting a confirmed valid point with conflicting data

### Requirement: Bounded buffering and reconnect
The Agent SHALL buffer metric batches in a bounded queue while disconnected and retransmit them in order after reconnect. The system SHALL preserve visible gaps when buffered data is dropped or unavailable.

#### Scenario: Disconnect and reconnect
- **WHEN** the Hub connection is lost and later restored
- **THEN** the Agent retransmits buffered batches in order, and the system shows any dropped or missing interval as a gap rather than a continuous line

### Requirement: Native metric history, aggregation and retention
The Hub SHALL store native metric samples in SQLite and provide queryable raw and aggregated history. The default retention SHALL be raw 7 days, 1-minute 90 days, 5-minute 1 year and 1-hour 2 years.

Current implementation uses fixed code constants; retention configuration is not yet available.

#### Scenario: Range query
- **WHEN** an authorized user requests a node metric over a time range
- **THEN** the Hub selects an appropriate aggregation tier and returns points, tier, step and gaps for that range

#### Scenario: Late or corrected sample
- **WHEN** a late or corrected sample arrives for an existing bucket
- **THEN** the affected aggregation bucket is recomputed and the query reflects the corrected data

#### Scenario: Retention cleanup
- **WHEN** samples exceed their configured retention
- **THEN** the Hub removes expired data without deleting unexpired tiers or unrelated monitoring records

### Requirement: Node overview and history
The system SHALL build the monitoring overview by traversing NodeSteer nodes directly, showing online status, CPU, memory, load, disk, network, latest sample time and alerts. It SHALL NOT require a separate monitoring-node binding.

Current implementation returns a placeholder alert count of 0; rule alert aggregation is not yet implemented.

#### Scenario: Overview without bindings
- **WHEN** a NodeSteer node exists and has native samples
- **THEN** it appears in the monitoring overview with its current metrics and latest sample time without any binding step

#### Scenario: History drill-down
- **WHEN** the user selects a node metric in the overview
- **THEN** the system shows its raw or aggregated history and preserves gaps and missing values

#### Scenario: Stale or absent data
- **WHEN** a node has no recent samples or no metric capability
- **THEN** the overview shows unavailable, waiting or stale state rather than zero values

### Requirement: External targets and observations
The system SHALL manage external HTTP(S), TCP and ICMP targets with probe location, timeout, current and historical status and response time. It SHALL reserve target kinds and validation branches for future database and message queue adapters, without claiming implemented adapters.

#### Scenario: Probe failure
- **WHEN** a configured target cannot be reached
- **THEN** the system shows the failed observation, time and reason rather than fabricating a zero or healthy value

### Requirement: Generic HTTP response values
The system SHALL support authenticated HTTP collection and extracting JSON fields as state, number, boolean or timestamp, with explicit freshness and expiry semantics; credentials SHALL be protected and excluded from logs.

#### Scenario: Approaching expiry
- **WHEN** an HTTP response contains the configured expiry timestamp
- **THEN** the system records the actual timestamp and makes it available to expiry rules

#### Scenario: Missing field
- **WHEN** the requested field is missing or invalid
- **THEN** the system records an error or unknown observation rather than inventing a value

### Requirement: Metric history integrity
The system SHALL preserve distinctions between zero and missing data, sampling times and gaps, and SHALL use stable NodeSteer node identity without guessing from names or addresses.

#### Scenario: Node reconnects
- **WHEN** monitoring data is delayed or missing while a node disconnects and later reconnects
- **THEN** historical results preserve the gaps and any valid backfilled observations

#### Scenario: Conflicting samples
- **WHEN** two samples for the same node, metric, dimensions and timestamp conflict
- **THEN** the system records the conflict or rejects the later sample according to the quality rules, and does not silently choose a value

### Requirement: Nocturne removal
The system SHALL NOT require Nocturne for node metrics, history, bindings or monitoring pages. Nocturne binding, proxy, page entry, environment variables and deployment guidance SHALL be removed. Existing Nocturne history SHALL NOT be migrated or displayed as current NodeSteer monitoring data.

#### Scenario: No Nocturne configured
- **WHEN** NodeSteer is deployed without any Nocturne service
- **THEN** native node monitoring and external target monitoring remain available according to their implemented capabilities

#### Scenario: Existing Nocturne history
- **WHEN** a deployment previously used Nocturne
- **THEN** old Nocturne history is not imported, backfilled or shown in the new native monitoring views

### Requirement: API JSON 压缩

Hub API SHALL 在客户端声明 `Accept-Encoding: gzip` 时压缩 JSON 响应，并设置 `Content-Encoding: gzip` 与 `Vary: Accept-Encoding`。二进制、SSE、无 body 状态码和已编码响应 MUST 保持原样。

#### Scenario: 大 JSON 响应

- **WHEN** 客户端请求监控历史 JSON 并接受 gzip
- **THEN** 响应使用 gzip 编码
- **AND** 解压后 JSON 与未压缩响应语义一致

#### Scenario: 二进制下载

- **WHEN** 客户端下载 Agent 二进制或 artifact 并接受 gzip
- **THEN** 响应不设置 `Content-Encoding: gzip`
- **AND** 文件字节不变

### Requirement: 静态资源压缩与缓存

Hub 静态文件服务 SHALL 对文本资源提供 gzip（可选预压缩 brotli），设置 HTML `no-cache`、哈希资源 `immutable`、其他静态资源短缓存，并支持 ETag/Last-Modified 304。

#### Scenario: 哈希 JS 首次与再次加载

- **WHEN** 浏览器请求 `/assets/index-<hash>.js`
- **THEN** 首次返回 gzip 内容与一年 immutable 缓存头
- **AND** 再次携带 `If-None-Match` 返回 304 且无 body

#### Scenario: HTML 入口

- **WHEN** 浏览器请求 `/` 或 SPA 回退
- **THEN** 返回 `Cache-Control: no-cache`

### Requirement: Overview 查询裁剪与缓存

Hub overview SHALL 只查询页面需要的 metric，latest 时间窗口不超过 15 分钟，采样周期推导窗口不超过 15 分钟；SHALL 使用进程内缓存并在指标写入 generation 变化时失效。

#### Scenario: 大库 overview

- **WHEN** 数据库包含百万级 raw 样本且 overview 被请求
- **THEN** 查询只扫描所需 metric 的最近窗口
- **AND** 15s 内重复请求命中缓存

### Requirement: 单节点摘要与批量接口

Hub SHALL 提供单节点 overview 摘要、批量 series 和批量 history 接口，同时保持旧接口兼容。

#### Scenario: 节点详情批量加载

- **WHEN** 客户端请求 `series/batch` 与 `history/batch`
- **THEN** 单次请求返回最多 64 个 metric/series 的结果
- **AND** 旧 `/series` 与 `/metrics` 接口行为不变

### Requirement: 查询索引与慢查询观测

Hub SHALL 通过 schema v28 增加 execution/file transfer/audit 的 created_at 索引，并记录超过阈值的 API/SQL 耗时。

#### Scenario: 执行列表排序

- **WHEN** 执行列表按 `created_at DESC` 查询
- **THEN** 查询计划使用 created_at 索引

#### Scenario: 慢请求

- **WHEN** API 或原生指标 SQL 超过阈值
- **THEN** 记录 method/path 或操作名与耗时
- **AND** 不记录查询值、token 或私钥

### Requirement: BUSY 可重试错误不得中断已认证连接

Hub SHALL 在 HELLO 连线状态写入、心跳更新和断连 offline 写入路径对 `SQLITE_BUSY`/`SQLITE_LOCKED` 做短退避重试。认证失败、协议错误、会话被替换或节点不存在仍按既有语义处理。

#### Scenario: HELLO 遇到瞬时 BUSY

- **WHEN** Agent 完成认证但 `markConnected` 或心跳写入首次返回 BUSY
- **THEN** Hub 重试写操作并在成功后返回 accepted 的 HELLO_ACK
- **AND** 不因该瞬时错误关闭连接

#### Scenario: 心跳遇到瞬时 BUSY

- **WHEN** 已认证会话的 HEARTBEAT 更新首次返回 BUSY
- **THEN** Hub 重试并继续发送 HEARTBEAT_ACK
- **AND** 不把节点标记 offline

### Requirement: Agent 内存补报缓冲有硬上限

Agent SHALL 在内存中保留未确认的指标批次，默认 20 批、可配置最大 60 批，并始终受 4MiB 序列化字节上限约束。任一上限先到即淘汰最旧批次并记录丢弃计数；缓冲不落盘。

#### Scenario: 断连超过批数上限

- **WHEN** Agent 断连期间累计批次超过配置批数
- **THEN** 队列只保留最新的有界批次
- **AND** 记录被丢弃的批次数与缺口

#### Scenario: 断连超过字节上限

- **WHEN** 累计序列化大小超过 4MiB
- **THEN** 队列淘汰最旧批次直到满足字节上限
- **AND** 不创建磁盘文件或数据库表

### Requirement: 指标批次仅在写成功或 ACK 后出队

Agent SHALL 区分本地入队与 WebSocket 写成功。Hub 声明 `node_metrics_ack` 能力时，Agent 仅在该批次对应 ACK 成功后出队；未声明时回退为 WebSocket 写成功后出队。写失败、半开连接和 ACK 超时都不得丢弃批次。

#### Scenario: 半开连接

- **WHEN** Agent 已把批次放入发送队列但 WebSocket 写超时
- **THEN** 该批次仍保留在补报队列
- **AND** 重连后按 sequence/时间戳重发

#### Scenario: ACK 超时

- **WHEN** Hub 已入库但 ACK 在超时时间内未到达
- **THEN** Agent 重发同一批次
- **AND** Hub 以主键 UPSERT 保持幂等

#### Scenario: 旧 Hub

- **WHEN** Hub 的 HELLO_ACK 未声明 `node_metrics_ack`
- **THEN** Agent 在 WebSocket 写成功后出队，不等待 ACK

### Requirement: Hub 重算 1h 迟到窗口

Hub SHALL 在每次原生指标聚合时额外重算最近 1h 的 1m/5m/1h 桶，并按时间分块提交事务。重复或乱序批次必须幂等；超过迟到窗口的样本保留 raw 但不扩展 rollup 重算范围，并记录截断。

#### Scenario: 补报 15 分钟前样本

- **WHEN** 先写入并聚合当前样本，再补报 15 分钟前的样本
- **THEN** 对应 1m、5m、1h 桶重算并包含补报值

#### Scenario: 补报 1 小时前样本

- **WHEN** 补报样本位于 1h 迟到窗口内
- **THEN** 聚合重算该时间桶
- **AND** 重复补报不改变结果

#### Scenario: 超过迟到窗口

- **WHEN** rollup 最后桶早于 1h 窗口
- **THEN** 聚合从窗口起点截断并记录
- **AND** 不删除更老的 raw 数据

### Requirement: Agent WebSocket 与执行资源有界

Agent SHALL 将 WebSocket 发送队列限制为 32 帧和 1MiB 估算字节、读限制为 2MiB，执行输出限制为每流 512KiB/总计 1MiB，SQLite 连接池限制为 2，容器扫描在超过 16 个容器时降为 60s 且上限 32。

#### Scenario: 发送队列满

- **WHEN** WebSocket 写端变慢且队列达到帧数或字节上限
- **THEN** 指标批次留在补报队列而不是增长 WS 队列
- **AND** 控制消息优先于指标/日志获得发送预算

#### Scenario: 高容器节点

- **WHEN** 节点运行超过 16 个容器
- **THEN** 完整容器扫描周期降为 60s、最多采集 32 个容器
- **AND** 仍上报 `container.collection.discovered_count/sampled_count/limit`

### Requirement: 原生指标聚合窗口

Hub SHALL 将既有“每层向前 10 桶”语义扩展为“10 桶与 1h 迟到窗口取更早者”，并保持 `node_id + metric + dimensions + timestamp` 幂等与 raw 保留期不变。

#### Scenario: 常规聚合

- **WHEN** 已有 rollup 且新样本按 30s 周期到达
- **THEN** Hub 重算最后 10 个桶与最近 1h 窗口的并集
- **AND** raw 保留期和重复样本幂等语义不变

#### Scenario: 超过窗口

- **WHEN** 样本早于 1h 迟到窗口
- **THEN** raw 继续保留
- **AND** rollup 重算范围在窗口起点截断
