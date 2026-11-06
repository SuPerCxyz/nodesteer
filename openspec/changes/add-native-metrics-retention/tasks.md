## 1. 保留期配置

- [x] 1.1 `NativeRetention` 注入 SQLiteStore；默认 7d/90d/365d/730d。
- [x] 1.2 Hub YAML/env 配置 raw/1m/5m/1h；校验顺序与 raw>=1h。
- [x] 1.3 Prune/Aggregate/Backfill/样本年龄校验使用注入值；`NativeMaxSampleAge=raw+1h`。

## 2. Prune

- [x] 2.1 Prune 与 aggregation 解耦；rollup 独立清理。
- [x] 2.2 raw watermark 门禁（成功且 <=30m；cutoff=min(now-raw, watermark)）。
- [x] 2.3 分批 10,000/5s/15ms；每批独立事务；层间失败隔离。
- [x] 2.4 `NativePruneStats` + `LastNativePruneStats` + Info/Warn 日志。
- [x] 2.5 0 行删除不递增 metricGeneration。
- [x] 2.6 incremental_vacuum 页数可配置（默认 5,000/轮，500/批，受 5s 预算）；journal_size_limit=64MB。

## 3. CLI / auto_vacuum

- [x] 3.1 `--prune-native` 与 `--prune-native-dry-run`。
- [x] 3.2 `--enable-incremental-vacuum`（停机 VACUUM、校验、输出耗时/大小）。
- [x] 3.3 新库首次建表前 auto_vacuum=INCREMENTAL。
- [x] 3.4 `--drain-free-pages`（dry-run/max/batch/budget）。

## 4. 测试

- [x] 4.1 保留期校验/env/default；样本年龄对齐。
- [x] 4.2 边界、分批、预算、dry-run、watermark、层独立。
- [x] 4.3 新库 auto_vacuum=2；VACUUM 生效与 freelist 释放。
- [x] 4.6 vacuum 页数默认/覆盖、5000 页分批、预算停止、freelist 下降、数据不误删。
- [x] 4.4 维护测试改为 aggregate 失败仍 prune。
- [x] 4.5 `make test`、`make lint`、相关 `-race` 通过。

## 5. 隔离验证

- [x] 5.1 4.4GB 副本 dry-run：raw 766,116，1m/5m/1h 0。
- [x] 5.2 实际 prune：17.17s、80 批、raw 766,116（约 2.6h），BUSY 0。
- [x] 5.3 1m/5m/1h 在保留期内行数不变；重复 prune 0 行。
- [x] 5.4 新库 auto_vacuum=2；临时库 VACUUM 后 freelist=0；CLI `--enable-incremental-vacuum` 实测 auto_vacuum=2、11ms。
- [x] 5.5 4.4GB 副本启用 incremental vacuum：45.9s，文件 4.71GB → 3.95GB；删除 300k raw 后 freelist 28,583 页（约 111MB），`--drain-free-pages` 17.37s / 6 批回收至 0，文件 3.7GB → 3.6GB。

## 6. 文档

- [x] 6.1 `docs/ARCHITECTURE.md` 已同步。
- [x] 6.2 `docs/DEPLOYMENT.md` 已同步。

## 验证记录

- 快照：`/tmp/opencode/nodesteer-perf2/verify.db`（4.4GB，7,102,825 raw）。保留期设为 raw 24h / 1m 7d / 5m 30d / 1h 365d。
- dry-run：raw 766,116 行可删，1m/5m/1h 均为 0。
- 实际 prune：17.17s，80 批，删除 raw 766,116，BUSY 0；raw 7,102,825 → 6,336,709。
- 保留期内 1m/5m/1h 行数不变；重复 prune 0 行。
- CLI dry-run（`--prune-native-dry-run`）实测 raw 362,057，1m/5m/1h 0，未删除。
- 新库 `PRAGMA auto_vacuum=2`；临时库 `--enable-incremental-vacuum` 路径经 store 测试，VACUUM 后 freelist=0。
- `make test`、`make lint` 通过；`go test -race -count=1` store/hubserver/cmd/hub 通过。
