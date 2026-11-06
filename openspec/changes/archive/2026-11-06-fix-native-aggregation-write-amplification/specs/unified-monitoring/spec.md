## ADDED Requirements

### Requirement: 聚合窗口与周期

Hub SHALL 默认使用 15 分钟迟到窗口、5 分钟维护周期，并允许配置覆盖。增量聚合 SHALL 每 series 每 tier 约一个事务，正常追赶不超过 2 小时；更早缺口 MUST 通过显式回填处理。

#### Scenario: 常规聚合

- **WHEN** 1,915 个 series 在 5 分钟周期内到达
- **THEN** 聚合按 10s 预算和 tier 游标轮转处理
- **AND** 每个 series 的 1m/5m/1h 重算各约一个事务

#### Scenario: 长缺口

- **WHEN** 最后 rollup 桶早于 2 小时
- **THEN** 聚合从 2 小时边界截断并记录
- **AND** 不自动回填更早历史

### Requirement: Series 目录

Hub SHALL 使用 `node_metric_series` 目录维护每节点 series 元数据，NODE_METRICS 写入 MUST NOT 在写事务内 UNION 扫描全部 raw/rollup。目录 MUST 保持 `NativeMaxSeries=2000` 上限语义。

#### Scenario: 已有节点写入

- **WHEN** 节点已有 1,900 个 series 并写入已有 series 样本
- **THEN** 写入只查询目录中本批 series 是否存在
- **AND** 不扫描该节点全部 raw/rollup

#### Scenario: 新 series 超限

- **WHEN** 新 series 会使节点超过 2,000 个
- **THEN** 写入返回 `ErrNativeSeriesLimit`
- **AND** 目录和 raw 不写入该批次

### Requirement: BUSY 重试上限与连接取消

Hub SHALL 将 SQLite `busy_timeout` 默认设为 5 秒，Gateway BUSY 重试总预算 MUST 小于 10 秒，并在连接关闭或 session ctx 取消时立即停止。

#### Scenario: 连接关闭

- **WHEN** retryBusy 等待期间 Agent WebSocket 关闭
- **THEN** 重试立即返回
- **AND** 不再等待剩余 busy_timeout

### Requirement: 读写连接池隔离

Hub SHALL 使用独立只读连接池处理非事务查询，写路径使用独立小连接池；读池 MUST 禁止写入。

#### Scenario: 写锁持有期间读取

- **WHEN** 写事务持有 SQLite 写锁
- **THEN** 只读查询仍能通过读池返回
- **AND** 不等待写连接释放

### Requirement: 缺失 rollup 回填

Hub SHALL 提供显式、分块、限速、幂等的 bounded backfill，按 1m → 5m → 1h 顺序恢复指定时间范围，不删除 raw。

#### Scenario: 恢复 30 分钟缺口

- **WHEN** 运行 `--backfill-native-from`/`--backfill-native-to` 覆盖缺口
- **THEN** 1m、5m、1h 桶恢复
- **AND** 重复运行不改变结果
- **AND** raw 行数不变

## MODIFIED Requirements

### Requirement: 原生指标聚合窗口

既有 1 小时迟到窗口与每层 10 桶重叠语义 SHALL 改为 15 分钟窗口 + 最后桶 + 2 小时追赶上限；raw 保留期和 `node_id + metric + dimensions + timestamp` 幂等语义不变。

#### Scenario: 1h 桶收敛

- **WHEN** 当前小时结束且上一小时存在 5m 数据
- **THEN** 上一小时 1h 桶在下一轮聚合中重算
- **AND** 不重算更早于 2 小时的桶

#### Scenario: 常规聚合

- **WHEN** 已有 rollup 且新样本按 30s 周期到达
- **THEN** Hub 重算最后桶与最近 15 分钟窗口
- **AND** raw 保留期和重复样本幂等语义不变

#### Scenario: 超过窗口

- **WHEN** 样本早于 15 分钟迟到窗口
- **THEN** raw 继续保留
- **AND** rollup 重算范围在窗口起点截断，更早缺口需显式回填
