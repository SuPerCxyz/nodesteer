## Why

预览 Hub 的 DB 已增长到约 4.3GB、6.7M raw、1,915 series。当前聚合每分钟对每个 series 重算完整 1h，并叠加每层 10 桶重叠，产生约 17,235 个写事务/分钟；单轮 36–58s 失败，5m/1h 桶停摆。同时每批 NODE_METRICS 在写事务内用 `UNION` 扫描该节点全部 raw+rollup（约 0.41s/批），`retryBusy` 最多 4×15s 且不监听连接关闭，造成 ping/ACK 超时和重连补报正反馈；API 与写共享 8 连接池，login 多次等待 15s。

本 change 只修复聚合写放大、series 扫描、BUSY 重试、读写池隔离和缺失 rollup 回填，不改变指标语义、保留期或协议。

## What Changes

- 迟到重算窗口默认 1h → 15m；维护周期 1m → 5m；均支持 Hub 配置/env 覆盖。
- 聚合增量范围改为“15m 窗口 + 最后桶”，正常追赶最多 2h；每 tier 每 series 约 1 个事务。
- 新增 `node_metric_series` 目录（schema v29），从现有 raw/rollup 回填；NODE_METRICS 写入只查目录，不再 UNION 扫描。
- 每轮聚合最多 10s（可配置），按 tier 游标轮转；跳过窗口内无 source 的 series；initial 回填分块。
- `busy_timeout` 默认 15s → 5s；`retryBusy` 总预算 8s，并绑定 `conn.done`/session ctx，连接关闭立即停止。
- 写连接池 2、只读连接池 8；store 只读查询走独立 `query_mode` 连接。
- 新增一次性 bounded backfill：`nodesteer-hub --backfill-native-from ... --backfill-native-to ...`，分块、限速、不删 raw。
- 聚合/回填记录耗时、series 数、事务数、BUSY；文档说明 5s 采样不推荐生产，默认 30s。

## Capabilities

### Modified Capabilities

- `unified-monitoring`: 增加聚合窗口/周期、series 目录、读写隔离、BUSY 重试上限与 bounded backfill 要求；指标语义、raw/rollup 保留期不变。

## Impact

Hub 存储层、Gateway 重试、Hub 配置、维护任务、SQLite schema v29、部署文档与测试。Agent 协议、远程 Agent 和 18810 运行实例不在改动范围。
