## Context

`NativeRawRetention` 等常量同时用于聚合 source 范围、backfill、prune 和样本年龄校验。预览需要更短保留期，但不能改变代码默认规格值。Prune 目前单事务删除全部过期行，且聚合失败时被跳过；raw 删除缺少“聚合已推进”的保护。

## Goals / Non-Goals

**Goals:**

- 预览可配置 raw 1d、1m 7d、5m 30d、1h 1y；代码默认 7d/90d/365d/730d。
- Prune 与 aggregation 解耦，rollup 独立清理；raw 受 watermark 门禁。
- 分批、限预算、层间隔离、可统计、可 dry-run。
- 停机 CLI 启用 incremental vacuum；新库自动启用。
- 不删除未过期数据，不重启 18810。

**Non-Goals:**

- 不改变指标采集、协议、rollup 语义或默认规格保留期。
- 不在普通 migration 中执行 VACUUM。
- 不做后台自动 VACUUM；incremental_vacuum 仅小批量释放页。

## Decisions

### 1. NativeRetention

```go
type NativeRetention struct {
    Raw, OneMinute, FiveMinute, OneHour time.Duration
}
```

零字段分别回退规格默认值；`Validate` 要求 `raw >= 1h` 且 `raw <= 1m <= 5m <= 1h`。`SQLiteStore` 保存注入值，所有 Prune/Aggregate/Backfill sourceRetention 与 Insert 最大样本年龄使用该值。`NativeMaxSampleAge` 改为 `rawRetention + 1h`。

### 2. Prune 策略

- 每层独立循环：raw、1m、5m、1h。
- 默认 `BatchRows=10000`、`Budget=5s`、`Pause=15ms`；每批独立事务。
- raw cutoff：
  - `now-rawRetention`；
  - 若最近聚合成功时间不存在或超过 30 分钟：跳过 raw；
  - 否则 cutoff = `min(now-rawRetention, aggregateWatermark)`。
- rollup cutoff 仅由各层 retention 决定，不受聚合失败影响。
- 某层出错记录并继续下一层；预算耗尽停止后续层。
- 删除行数 >0 才递增 `metricGeneration`。
- 删除后若 `auto_vacuum=INCREMENTAL`，按配置页数回收 freelist：默认 5,000 页/轮（约 20MB），每批 500 页，受 prune 5s 预算约束；记录释放页数、耗时、BUSY。
- 新增 `--drain-free-pages` 一次性 CLI，支持 `--drain-free-pages-max/batch/budget` 与 dry-run，可在 Hub 运行或停机时使用。

### 3. 统计

`NativePruneStats` 包含 duration、各层删除数、batches、busy errors、last success/error、raw skipped、dry-run、budget hit。`LastNativePruneStats` 供测试/运维读取；每轮 Info/Warn 日志。

### 4. CLI

- `--prune-native`：按当前 retention 执行一次 prune 后退出。
- `--prune-native-dry-run`：只统计将删行数，不删除。
- `--enable-incremental-vacuum`：`PRAGMA auto_vacuum=INCREMENTAL; VACUUM;`，校验 `auto_vacuum=2`，输出耗时/前后文件大小；要求 Hub 停止，约需 2x 空间。
- 参数 `--prune-native-batch`、`--prune-native-budget` 可覆盖默认。

### 5. auto_vacuum

- 新库写 DSN 加 `_pragma=auto_vacuum(2)`，并在 Migrate 前显式设置。
- 已有库 DSN pragma 不会立即改变 header，必须 CLI VACUUM；普通 migration 不执行。
- `journal_size_limit=64MB`。

### 6. 维护解耦

`startNativeMetricMaintenanceWithDelay` 不再在 aggregate 失败时 return；prune 总会执行。raw 安全性由 watermark 门禁保证。

## Risks / Trade-offs

- VACUUM 需要额外磁盘和停机时间；4.4GB 库可能数分钟。
- Prune 分批仍会写 WAL；5s 预算和 15ms 间隔限制单轮影响，但大库可能需要多轮。
- 预览短保留期会删除超过 1d 的 raw、7d 的 1m 等；这是用户确认的策略。
- watermark 仅在进程内；Hub 重启后需至少一次成功聚合才恢复 raw 清理。
- incremental_vacuum 只释放部分页，不保证文件立即缩小。

## Migration

schema 不变；新库 auto_vacuum 在首次建表前设置。已有库通过显式 `--enable-incremental-vacuum` 切换，需停机并备份。
