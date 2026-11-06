## Why

监控页面当前的主要成本不是数据量，而是传输与重复查询：overview 每次扫描约 164 万 raw 样本、耗时 1.1–1.4s；节点详情发出 73–85 个请求、e59 响应 2.14MB；静态资源 2.57MB/页且 API/静态资源均无压缩或缓存。Hub 需要在保持既有 API 契约与数据语义不变的前提下降低查询量、响应体积和重复请求数。

## What Changes

- API JSON 按 `Accept-Encoding` gzip，设置 `Content-Encoding`/`Vary`；二进制、SSE、304 跳过。
- Hub 静态文件服务支持 gzip（运行时）和可选预压缩 brotli；HTML `no-cache`，哈希资源 `immutable` 一年，其余静态资源短缓存；ETag/Last-Modified 与 304。
- overview 只查询页面需要的 metric，采样周期窗口从 6h 缩到 15min；增加按 metric/node 的 latest 批量查询；增加 15s 服务端缓存（指标写入 generation 变化即失效）。
- 新增单节点 overview 摘要接口；新增批量 series 与批量 history 接口，旧接口保持兼容。
- series 目录 60s 缓存、history 30s 缓存（带 generation/键失效）。
- 新增 `executions(created_at DESC)`、`file_transfers(created_at DESC)`、`audit_logs(created_at DESC)` 等查询索引（schema v28）。
- API 与原生指标 SQL 增加慢请求日志/计数；不记录查询参数和敏感数据。

## Capabilities

### Modified Capabilities

- `unified-monitoring`: 增加查询裁剪、批量接口、缓存与传输压缩要求；指标语义、保留期、聚合规则不变。

## Impact

Hub API、HTTP 中间件、静态服务、监控查询层、SQLite 索引、测试与 `docs/ARCHITECTURE.md`。不提升协议版本，不改 Agent、不改 raw/rollup 保留期，不删除历史数据。
