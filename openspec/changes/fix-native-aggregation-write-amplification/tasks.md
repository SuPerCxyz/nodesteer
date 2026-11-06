## 1. 聚合窗口与预算

- [x] 1.1 `NativeLateWindow` 1h → 15m；维护周期 1m → 5m；Hub 配置/env 可覆盖。
- [x] 1.2 增量范围改为 15m 窗口 + 最后桶，正常追赶上限 2h。
- [x] 1.3 每轮 10s 预算 + tier 游标轮转；initial 回填分块。
- [x] 1.4 跳过窗口内无 source 的 series。

## 2. Series 目录

- [x] 2.1 schema v29 `node_metric_series`，迁移回填现有 raw/rollup。
- [x] 2.2 写入路径只查目录，保持 2000 series 上限。
- [x] 2.3 series 查询/批量查询改读目录；节点删除清理目录。

## 3. BUSY 与读写隔离

- [x] 3.1 `busy_timeout` 默认 5s，可配置。
- [x] 3.2 `retryBusy` 总预算 8s，绑定 `conn.done`/session ctx。
- [x] 3.3 写池 2 / 只读池 8，读池 `query_only`。
- [x] 3.4 非事务只读 Query 改用 `queryer`。

## 4. 回填

- [x] 4.1 `BackfillNativeMetrics` 按 1m → 5m → 1h 分块、限速、幂等。
- [x] 4.2 CLI `--backfill-native-from/to/chunk/pause`。
- [x] 4.3 回填测试覆盖 15m/1h 缺口恢复与重复幂等。

## 5. 验证

- [x] 5.1 `make test`、`make lint` 通过。
- [x] 5.2 `go test -race -count=1` store/hub/hubserver/api 通过。
- [x] 5.3 真实 4.4GB 快照 v29 迁移 29.2s，目录回填 1,924 series。
- [x] 5.4 快照聚合：10s 预算命中，tier-series 4,496，tx 12,129，BUSY 0；游标单测确认轮转。
- [x] 5.5 快照 backfill 40m：86.9s，tx 10,492，BUSY 0；1m 114,718 / 5m 22,437 / 1h 1,498 桶；raw 不变，重复回填 rollup 总数不变。
- [x] 5.6 retryBusy 默认预算 8s（单测断言 <10s）、conn.done 取消、读写池隔离测试通过。

## 6. 文档

- [x] 6.1 `docs/ARCHITECTURE.md` 已同步。
- [x] 6.2 `docs/DEPLOYMENT.md` 已同步。

## 验证记录

- 快照：预览 DB 只读备份 `/tmp/opencode/nodesteer-perf2/hub-snapshot.db`（4.4GB，7,102,825 raw，2,751,966 rollup，1,924 series）。未改原 DB。
- v29 migration：29.188s。
- series 基数校验：旧 UNION 扫描单节点 2.588s；新目录 `COUNT(*)` 1ms、`IN` 查询 <1ms。
- 聚合两轮（10s 预算，回填后快照）：round1 10.1s / 1,923 tier-series / 2,872 tx；round2 6.9s / 3,849 tier-series / 11,026 tx；两轮合计覆盖全部 5,772 tier-series，BUSY 0。首次追赶存在 truncated（最后桶早于 2h），稳态目标约 1,149 tx/min。
- bounded backfill（40 分钟，store 路径）：86.9–96.7s，tx 10,492，BUSY 0；1m 114,718 / 5m 22,437 / 1h 1,498 桶；raw 行数不变；重复回填 rollup 总数不变。
- CLI backfill（10 分钟，快照副本）：10.55s，series 3,848，tx 2,998，BUSY 0；命令运行完退出，不启动 Web/Gateway。
- `make test`、`make lint` 通过；`go test -race -count=1` store/hub/hubserver/api 全部通过。
