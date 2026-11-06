## Why

原生指标 raw/rollup 保留期目前是代码常量，生产预览库已增长到 4.4GB。需要让部署按环境配置更短保留期（预览 raw 1d、1m 7d、5m 30d、1h 1y），同时保持代码默认规格值（7d/90d/365d/730d）。清理必须与聚合解耦、分批限预算、受聚合成功 watermark 保护，并提供一次性 prune/backfill/vacuum 运维命令，避免删除未过期历史或长时间锁库。

## What Changes

- `NativeRetention` 注入 SQLiteStore，替换 Prune/Aggregate/Backfill/样本年龄校验中的硬编码保留期；默认规格值不变。
- Hub 配置/env 增加 raw/1m/5m/1h retention；校验 `raw <= 1m <= 5m <= 1h` 且 raw >= 1h。
- Prune 与 aggregation 解耦：rollup 独立清理；raw 仅在最近一次聚合成功且不超过 30 分钟时删除，上界 `min(now-rawRetention, aggregateWatermark)`。
- Prune 分批（默认 10,000 行/批、5s 预算、15ms 间隔），每批独立事务；层失败不阻塞其他层；仅 auto_vacuum=INCREMENTAL 时小批量 incremental_vacuum。
- 新增 `NativePruneStats` 与日志；0 行删除不递增 metricGeneration。
- CLI：`--prune-native`、`--prune-native-dry-run`、`--enable-incremental-vacuum`；dry-run 只统计；vacuum 停机执行并校验。
- 新库首次建表前启用 auto_vacuum=INCREMENTAL；`journal_size_limit=64MB`。
- 现有库部署时停 Hub 执行一次 `--enable-incremental-vacuum`；不删除未过期历史。

## Capabilities

### Modified Capabilities

- `unified-monitoring`: 增加可配置保留期、watermark 门禁、分批 prune、dry-run 和停机 incremental vacuum 要求；指标语义与默认规格保留期不变。

## Impact

Hub 配置、SQLite schema/打开参数、Prune/维护任务、CLI、文档与测试。不修改 Agent 协议，不重启 18810，不删除未过期数据。
