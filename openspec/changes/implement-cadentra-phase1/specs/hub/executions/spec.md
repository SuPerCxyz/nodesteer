## Purpose

Hub 管理 Execution 生命周期（Manual/Scheduled）、幂等、日志、取消与审计，Execution 可靠性由 Execution ID/Key、Journal 与 Idempotent Upsert 保证。

## ADDED Requirements

### Requirement: Manual Run
系统 SHALL 支持 Web Run Now：Hub 创建 Execution UUID、持久化 PENDING、向 Agent Dispatch；Agent 按 Execution ID 去重执行。Agent Offline 时 Manual Run 明确返回不可执行，不进入长期等待队列。

#### Scenario: Manual Run 成功
- **WHEN** 管理员对在线 Agent 执行 Run Now
- **THEN** Hub 创建 PENDING Execution，下发后 Agent 去重并执行，最终更新 SUCCESS/FAILED

#### Scenario: Agent Offline 时 Manual Run
- **WHEN** 目标 Agent Offline 时管理员执行 Run Now
- **THEN** Hub 明确返回不可执行错误，不创建长期等待队列

### Requirement: Execution 状态机
系统 SHALL 支持 PENDING→RUNNING→{SUCCESS,FAILED,TIMED_OUT,CANCELED}，Condition 不满足时为 SKIPPED。

#### Scenario: 执行成功
- **WHEN** Execution 正常完成
- **THEN** 状态更新为 SUCCESS 并记录 Exit Code

#### Scenario: 执行超时
- **WHEN** 执行超过 Task Timeout
- **THEN** 状态更新为 TIMED_OUT 并清理进程组

### Requirement: Execution 幂等
系统 SHALL 保证 Scheduled Execution 使用 Task ID + Node ID + Scheduled Time 稳定逻辑 Key（数据库 UNIQUE），相同 Schedule Slot 只执行一次；Manual Run 使用 Hub 创建的 Execution UUID 去重。

#### Scenario: 重复触发不重复执行
- **WHEN** 同一 Schedule Slot 被重复触发（重复通知或 Agent 重启）
- **THEN** 仅执行一次，重复请求被幂等忽略

#### Scenario: Manual Execution 去重
- **WHEN** Hub 重复下发同一 Execution UUID
- **THEN** Agent 只执行一次

### Requirement: Execution 记录字段
系统 SHALL 记录 Execution ID、Task ID、Task Revision、Script Revision、Node ID、Trigger Type、Scheduled Time、Start/End Time、Status、Exit Code、stdout、stderr、Offline 标志。

#### Scenario: 查看 Execution 详情
- **WHEN** 查看 Execution 详情
- **THEN** 展示上述全部字段及日志

### Requirement: Realtime 与 Offline Logs
系统 SHALL 在 Hub 在线时 Realtime Stream 日志；Hub 离线时日志保留在本地，重连后上传；Hub 重启不得丢失日志。

#### Scenario: 离线执行日志上传
- **WHEN** Agent 离线期间执行完成，Hub 恢复后
- **THEN** Agent 上传 Execution 记录与日志，Hub 幂等 Upsert 后 ACK，Agent 标记 synced=true

### Requirement: 日志大小限制
系统 SHALL 限制 max_stdout_bytes、max_stderr_bytes、max_total_log_bytes，超限截断并标记，不得写满磁盘。

#### Scenario: 日志超限截断
- **WHEN** 执行输出超过日志上限
- **THEN** 日志被截断并标记 truncated，Execution 状态不受截断影响

### Requirement: Execution 对账
系统 SHALL 在 Agent 重连时对账 Unsynced Executions 与 Running Execution State；Agent 重启发现旧 RUNNING 但进程不存在时标记 FAILED（reason=agent_restarted）。

#### Scenario: Agent 重启恢复
- **WHEN** Agent 重启后发现本地 Journal 中存在 RUNNING 但进程已不存在
- **THEN** Agent 将该 Execution 标记 FAILED reason=agent_restarted 并在重连后上报

### Requirement: 取消执行
系统 SHALL 支持通过控制通道下发 CANCEL_EXECUTION，Agent 终止对应进程组。

#### Scenario: 取消执行
- **WHEN** 管理员取消正在运行的 Execution
- **THEN** Agent 终止进程组并将状态更新为 CANCELED

### Requirement: 审计关联
系统 SHALL 记录 Task 执行相关审计事件（Execute）。

#### Scenario: 记录执行审计
- **WHEN** 管理员触发 Task 执行
- **THEN** Audit Log 记录 Execute 事件
