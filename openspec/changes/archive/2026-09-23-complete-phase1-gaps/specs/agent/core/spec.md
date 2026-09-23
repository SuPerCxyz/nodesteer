## Purpose

补齐 Agent 端一期功能缺口：Cron 触发、Manual 幂等、同步事务、READY 门禁、Jitter、Realtime Log、Retry、Remote Condition 查询、日志上限、WorkingDir/RunUser 接线。

## ADDED Requirements

### Requirement: Cron Schedule 本地触发
系统 SHALL 支持 Cron 类型 Schedule 在 Agent Local Scheduler（execution_owner=agent）侧按标准 cron 表达式触发。

#### Scenario: Cron 本地到点
- **WHEN** Agent-owned Cron Schedule 到达触发时刻
- **THEN** Agent 本地执行并持久化 Journal，重连后上报

### Requirement: 同步事务
系统 SHALL 在一次同步中对脚本/任务/调度/应用进行事务性持久化。

#### Scenario: 同步中途失败
- **WHEN** 同步过程中任一条目写入失败
- **THEN** 整个同步回滚，本地不出现半同步状态，Revision 不前进

### Requirement: READY 门禁
系统 SHALL 在 Reconnect Reconciliation 完成前不将 Agent 视为就绪。

#### Scenario: 对账完成前
- **WHEN** Agent 重连后配置对账尚未完成
- **THEN** Agent 不被标记 READY，不接收新任务

### Requirement: 周期 Jitter
系统 SHALL 为 Heartbeat 与 Revision 周期校验加入随机抖动。

#### Scenario: 抖动生效
- **WHEN** 多个 Agent 同时运行
- **THEN** 各 Agent 的周期检查时间随机偏移，避免同步冲击

### Requirement: Realtime Log
系统 SHALL 在执行期间将 stdout/stderr 分片实时上报 Hub。

#### Scenario: 执行产生日志
- **WHEN** 执行产生日志且 Hub 在线
- **THEN** Agent 分片发送 LOG_CHUNK，Hub 记录于 Execution

#### Scenario: Hub 离线
- **WHEN** Hub 离线
- **THEN** 本地日志仍完整记录，重连后整包上报

### Requirement: Task Retry
系统 SHALL 在 Task 执行失败时按 Retry 次数重试。

#### Scenario: 失败重试
- **WHEN** 执行失败且 Retry > 0
- **THEN** Agent 在失败后重新执行，最多 Retry 次

### Requirement: Manual 执行幂等
系统 SHALL 按 Execution ID 去重 Manual Run，且同一 Task+Node 的多次手动执行均生效。

#### Scenario: 同一任务多次手动执行
- **WHEN** 同一 Task 在同一 Node 连续手动执行
- **THEN** 每次生成独立执行记录，互不阻塞

### Requirement: 总日志上限
系统 SHALL 同时限制 stdout、stderr 与两者合计的最大字节数。

#### Scenario: 超过总上限
- **WHEN** 日志总量超过配置上限
- **THEN** 截断并标记 Truncated，不无限增长

### Requirement: WorkingDir 与 RunUser
系统 SHALL 将 Script/Task 的 WorkingDir 与 RunUser 传入进程执行。

#### Scenario: 工作目录生效
- **WHEN** Script 配置 WorkingDir
- **THEN** 进程在指定目录启动

### Requirement: Remote Condition 查询
系统 SHALL 在评估 Remote Node Condition 时向 Hub 发起查询并获取真实状态。

#### Scenario: 远程条件求值
- **WHEN** 任务配置远程条件
- **THEN** Agent 通过 Hub 获取目标节点状态，未过期时正常求值，否则 Fail Closed
