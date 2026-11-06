## Context

上一 change 已把迟到窗口设为 1h、15m 分块，并给 Hub 增加了 2MiB 控制消息限制。生产 DB 继续增长后，1h 窗口 × 每层 10 桶重叠 × 15m chunk 仍产生大量事务；写路径的 series 基数校验又用 `UNION` 扫描 raw+rollup。必须在不删除历史数据、不改变指标语义的前提下降低写放大和锁等待。

## Goals / Non-Goals

**Goals:**

- 每轮聚合在 10s 预算内完成尽可能多的 series，并按游标轮转，避免只推进第一个节点。
- 1m/5m/1h 每 series 每 tier 约 1 个事务；5m 周期下总事务数从约 17,235/min 降到约 1,149/min。
- NODE_METRICS 写锁从约 0.41s/批降到毫秒级。
- BUSY 总等待 <10s，连接关闭立即取消；读请求不被写等待占满连接池。
- 提供可重复、分块、限速的缺失 rollup 回填命令。
- 保留 raw、保留期、`NativeMaxSeries=2000`、协议和旧 API 语义。

**Non-Goals:**

- 不改 Agent 采集/协议，不恢复或重启 18810，不操作远程 Agent。
- 不删除 raw、不缩短保留期、不做数据迁移式重写。
- 不把 late window 简单设为 24h。
- 不追求一次性回填全部历史；回填必须显式指定时间范围。

## Decisions

### 1. 聚合范围与事务

- `NativeLateWindow` 默认 15m。
- 增量 start = `now-15m`；若最后 rollup 桶早于 start，则 start 取最后桶，保证 1h 桶在小时后收敛。
- 正常追赶最多向前 `2h`；更早缺口由 backfill 命令处理。
- chunk = 15m（不小于 tier step）；initial 回填使用 24h chunk，并从该 series 最早 source 开始，避免 7d/90d/365d 单事务。
- 每 tier 每 series 通常 1 个事务；1h tier 的 chunk 为 1h。

### 2. series 目录

schema v29 新增：

```sql
node_metric_series(node_id, metric, series_key, dimensions_json, updated_at,
  PRIMARY KEY(node_id, metric, series_key))
```

迁移时从 `node_metric_samples` 和 `node_metric_rollups` 一次性 `INSERT OR IGNORE` 回填。写入路径先 `COUNT(*)`，再只查本批 distinct series 是否存在；新 series 做上限校验后写入目录。`NativeMaxSeries=2000` 语义不变。节点删除同时删除目录行。

### 3. 聚合预算与游标

`SQLiteStore` 保存每 tier 的内存游标。每轮使用 `time.Now()+budget` 作为 deadline；到预算后保存最后处理位置并返回，下轮从该位置继续。处理完一页后游标归零。预算默认 10s，可通过 Hub 配置/env 覆盖。

### 4. 跳过无 source 的 series

聚合前对 recompute 窗口执行 `SELECT 1 ... LIMIT 1`；无 raw/1m/5m source 的 series 直接跳过，不产生空 DELETE/COMMIT。

### 5. BUSY 与连接

- `busy_timeout` 默认 5s，可配置。
- `retryBusy` 总预算 8s；每次调用带 deadline ctx；重试间隔 100/250/500ms；连接关闭（`conn.done`）立即取消。
- 连接上下文由 `wsConn.sessionContext` 派生，HELLO/心跳/指标写入都使用它。

### 6. 读写连接池

- 写池 `db`：`SetMaxOpenConns(2)`。
- 读池 `readDB`：独立 `sql.DB`，`_pragma=query_only(1)`，`SetMaxOpenConns(8)`。
- `queryer` 默认走读池；事务内仍走 tx。`execer` 走写池。
- 未迁移的直接 `s.db.Query*` 读路径统一改为 `queryer`。

### 7. 回填

`BackfillNativeMetrics(ctx, from, to, options)`：
- 按 1m → 5m → 1h 顺序处理，保证粗 tier 读到已回填的细 tier。
- 每 chunk 独立事务；chunk 默认 15m（1h tier 至少 1h）；chunk 间默认 10ms 限速。
- 无 source 的 chunk 跳过；重复执行幂等；不删除 raw。
- CLI：`nodesteer-hub --backfill-native-from <RFC3339> --backfill-native-to <RFC3339> --backfill-native-chunk 15m --backfill-native-pause 10ms`，运行完退出，不启动服务。

### 8. 观测

- 慢 SQL 日志继续记录 >200ms 操作。
- 聚合/回填记录 duration、series、transactions、busy errors（可选 reporter）。
- 维护日志保留既有聚合失败/清理失败语义。

## Risks / Trade-offs

- v29 迁移在 4.4GB 快照上约 29s，属于一次性启动成本；迁移只建表/回填目录，不改 raw/rollup。
- 15m 窗口不会自动回填更早缺口；必须显式运行 backfill。
- 读池 `query_only` 连接仍能创建 WAL -shm；不改为 `mode=ro`，避免只读 WAL 打开失败。
- 写池 2 连接可能让并发写排队，但不再占用 API 读连接。
- 5s 采样会造成 5–6 倍写入放大，生产推荐 30s；本 change 不改 Agent 默认值。

## Migration

schema v29 只创建目录并回填，不删除历史；旧代码可回滚，但目录表会保留（无害）。回填命令需要显式时间范围。
