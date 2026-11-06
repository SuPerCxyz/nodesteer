## ADDED Requirements

### Requirement: 可配置原生指标保留期

Hub SHALL 支持 raw/1m/5m/1h 保留期配置，代码默认值 MUST 保持 7d/90d/365d/730d；配置 MUST 满足 raw >= 1h 且 raw <= 1m <= 5m <= 1h。Prune、aggregation source 范围、backfill 和样本年龄校验 MUST 使用注入值。

#### Scenario: 预览短保留期

- **WHEN** 配置 raw=1d、1m=7d、5m=30d、1h=365d
- **THEN** prune 按该配置删除过期数据
- **AND** 未过期数据不删除

#### Scenario: 非法顺序

- **WHEN** raw > 1m 或 raw < 1h
- **THEN** Hub 拒绝启动并报告配置错误

### Requirement: Prune 与 aggregation 解耦

Hub SHALL 独立执行 rollup 清理；raw 清理 MUST 仅在最近一次 aggregation 成功且不超过 30 分钟时执行，cutoff MUST 取 `min(now-rawRetention, aggregateWatermark)`。

#### Scenario: 聚合失败

- **WHEN** 本轮 aggregation 失败但存在旧 rollup
- **THEN** 1m/5m/1h 过期 rollup 仍被清理
- **AND** raw 不因本轮失败被额外删除

#### Scenario: 无成功聚合

- **WHEN** 进程启动后尚未成功聚合
- **THEN** raw 清理跳过
- **AND** rollup 清理继续

### Requirement: 分批限预算 Prune

Hub SHALL 默认每批 10,000 行、5 秒预算、15ms 间隔清理，每批独立事务；单层失败 MUST NOT 阻塞其他层。0 行删除 MUST NOT 递增 metricGeneration。

#### Scenario: 大表清理

- **WHEN** 过期 raw 超过 10,000 行
- **THEN** prune 跨多批提交
- **AND** 预算耗尽后保留剩余行供下一轮

#### Scenario: Dry run

- **WHEN** 使用 `--prune-native-dry-run`
- **THEN** 只统计各层将删行数
- **AND** 不删除任何行

### Requirement: 增量 vacuum 与空间回收

新库 SHALL 在首次建表前启用 `auto_vacuum=INCREMENTAL`；已有库 SHALL 通过停机 CLI `--enable-incremental-vacuum` 执行一次 `VACUUM` 并校验。普通 migration MUST NOT 执行 VACUUM。

#### Scenario: 新库

- **WHEN** 新建 Hub DB
- **THEN** `PRAGMA auto_vacuum` 为 2

#### Scenario: 停机切换

- **WHEN** 运行 `--enable-incremental-vacuum`
- **THEN** 命令执行 VACUUM 并输出耗时/前后大小
- **AND** 校验 auto_vacuum=2

### Requirement: 可配置增量页回收

Hub SHALL 默认每轮 prune 回收最多 5,000 个 freelist 页（约 20MB），按 500 页小批执行并受 5s prune 预算约束；页数 MUST 可通过 YAML/env 覆盖。Hub SHALL 提供 `--drain-free-pages` 一次性回收当前 freelist，支持 dry-run、最大页数、批大小和时间预算。

#### Scenario: 每轮回收上限

- **WHEN** prune 删除过期数据且 auto_vacuum=INCREMENTAL
- **THEN** 本轮最多回收配置页数
- **AND** 预算耗尽时停止并记录释放页数/耗时/BUSY

#### Scenario: 快速 drain

- **WHEN** 运行 `--drain-free-pages --drain-free-pages-max N`
- **THEN** 按小批回收不超过 N 页
- **AND** dry-run 只报告 freelist 不回收
- **AND** 不删除未过期数据

### Requirement: Prune 统计与日志

Hub SHALL 记录每轮 prune 的耗时、各层删除数、批次数、BUSY、raw 跳过、dry-run、预算命中和最近错误，并提供 `LastNativePruneStats()`。

#### Scenario: 运维审计

- **WHEN** prune 完成
- **THEN** 日志包含各层删除数和耗时
- **AND** 不包含样本值、token 或私钥
