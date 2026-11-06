## Context

上一 change 已解决连接稳定性、内存补报和聚合事务分块。本轮只处理 Hub 读路径与传输：overview 全量 latest 查询、6h 采样周期扫描、无缓存、无压缩静态资源/JSON。当前 DB 约 164 万 raw、63 万 rollup。

## Goals / Non-Goals

**Goals:**

- overview 1.2s → 0.1–0.3s；Dashboard 首次数据可用 1.4s → 0.4–0.6s。
- e59 详情 JSON 2.14MB → 约 0.25MB；tna 454KB → 约 70KB。
- 单页静态资源 2.57MB → 约 0.4–0.6MB；热加载接近 0 传输。
- 提供批量 series/history 与单节点摘要接口，为前端把 73–85 请求降到个位数做准备。
- 保持旧 API、字段、权限、状态语义不变。

**Non-Goals:**

- 不改 Agent 协议、指标采集频率、raw/rollup 保留期或聚合算法。
- 不修改前端资源文件本身（logo/字体/前端请求合并由后续前端变更处理）。
- 不引入外部缓存服务或数据库；缓存只在 Hub 进程内存。
- 不改变 2MiB Agent 控制消息读限制；大 payload 后续用分片/流式处理。

## Decisions

### 1. gzip 中间件而不是逐个 handler 改造

`api.Routes()` 外层包装 gzip response writer：仅在请求声明 gzip、响应 `Content-Type` 为 JSON、状态码允许 body 且未设置 `Content-Encoding` 时启用。统一设置 `Vary: Accept-Encoding`。二进制下载、SSE、304/204、HEAD 原样透传。这样不改 100+ 个 `writeJSON` 调用点，也不影响现有 JSON 契约。

### 2. 静态文件使用内存元数据缓存 + 运行时 gzip

`webFS` 是嵌入式只读文件系统。自定义 static handler 按需读取文件、计算 SHA-256 ETag 并缓存 gzip 字节；支持 `If-None-Match` 返回 304。若存在同名 `.br` 预压缩文件且客户端接受 br，优先返回。缓存条目上限 512，避免无界内存。

缓存策略：
- `index.html` / SPA 回退：`no-cache`。
- `assets/` 或文件名含内容哈希：`public, max-age=31536000, immutable`。
- 其他字体/图片等：`public, max-age=86400`。
- 设置 `Last-Modified`（文件系统有 modtime 时）并支持 `If-Modified-Since`。

### 3. overview 只取需要的 metric + 15min 窗口

overview 需要 `cpu.usage`、`memory.usage`、`load.1/5/15`、`disk.usage`、`network.rx_bps/tx_bps`、`system.uptime`、`system.temperature_c`。新增 `LatestNodeMetricSamplesForMetricsBatch`，SQL 限定 `node_id IN (...) AND metric IN (...) AND timestamp >= now-15m`，避免扫描全部 metric/series。采样周期查询窗口从 6h 改为 15min。

### 4. 15s overview 缓存按 generation 失效

SQLiteStore 在 raw 写入/清理提交后递增 `metricGeneration`。API 缓存条目录入 generation、nodeID 集合和查询时刻；generation 变化立即失效，节点状态每次请求仍从 `ListNodes` 读取，因此不缓存 online/offline。TTL 15s 兜底节点增删。

### 5. 单节点摘要与批量接口

- `GET /api/monitoring/nodes/{id}/overview`：返回单个 `nativeOverviewNode`，复用同一裁剪查询/缓存。
- `GET /api/monitoring/nodes/{id}/series/batch?metric=a&metric=b`：一次返回多个 metric 的 series 目录，最多 64 个 metric。
- `GET /api/monitoring/nodes/{id}/history/batch?metric=a&series=...&metric=b&series=...&start=&end=&step=`：按索引配对 metric/series，一次返回多个 history，最多 64 组。
- 旧 `/series`、`/metrics`、`/latest`、`/overview` 保持原字段与错误语义。

### 6. 缓存

- overview：15s，按 node 集合/generation。
- series：60s，按 node + metric 集合；不因每批写入失效。
- history：30s，按 node/metric/series/range/step/generation。
- 所有缓存仅进程内存，节点删除/重启自然清理；无外部依赖。

### 7. 索引与慢查询

schema v28 增加仅索引迁移，不重建表：
- `executions(created_at DESC)`
- `executions(node_id, created_at DESC)`
- `executions(status, created_at DESC)`
- `file_transfers(created_at DESC)`
- `file_transfers(source_node_id, created_at DESC)`
- `audit_logs(created_at DESC)`

API 中间件记录 >500ms 请求（method/path/status/duration）；native store 对超过 200ms 的查询 log warning，只含操作名/节点/耗时，不含值或查询参数。metrics 包增加 API 请求总数与慢请求计数。

## Risks / Trade-offs

- 15s overview 缓存意味着节点指标最多陈旧 15s；状态字段不从缓存读取，避免离线状态滞后。
- 静态 gzip 缓存会占用约 0.5–1MiB 常驻内存；有上限，且嵌入资源数量固定。
- 批量接口单次最多 64 组，避免单请求放大数据库压力。
- 索引迁移在 1GB 级 DB 上需要短暂构建时间；只创建索引，不改数据。

## Migration

schema v28 仅 `CREATE INDEX IF NOT EXISTS`。旧 Hub 可回滚；多出的索引不影响旧代码。
