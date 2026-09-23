# agent/executions Specification

## Purpose
Agent 执行引擎负责 Execution 幂等、Journal、Process Runner、Timeout/Cancel、日志收集与离线上传，保证执行可靠性与可恢复性。
## Requirements
### Requirement: 执行前先写 Journal
系统 SHALL 在执行启动前先持久化 Journal（INSERT RUNNING），然后才启动进程；完成后更新结果。

#### Scenario: 写 Journal 后启动
- **WHEN** Agent 准备执行 Task
- **THEN** 先写入 RUNNING Journal 记录，成功后才启动进程

### Requirement: Execution 幂等去重
系统 SHALL 按 Execution ID（Manual）与逻辑 Key Task ID+Node ID+Scheduled Time（Scheduled）去重，相同 Execution 不得重复执行。

#### Scenario: 重复 Execution ID
- **WHEN** Agent 收到已执行过的 Execution ID
- **THEN** Agent 忽略重复请求并返回已存在结果

#### Scenario: 相同 Schedule Slot 去重
- **WHEN** 同一逻辑 Key 的 Execution 已存在
- **THEN** Agent 不重复执行

### Requirement: Process Runner
系统 SHALL 支持 Working Directory、Environment、Run User、Process Group、stdout/stderr 分离、Timeout；Timeout 时先 SIGTERM 进程组，Grace Period 后 SIGKILL 进程组。

#### Scenario: 正常执行
- **WHEN** 执行 Command 或 Script
- **THEN** 使用配置的工作目录、环境与 Run User 启动，捕获 stdout/stderr

#### Scenario: 超时清理进程组
- **WHEN** 执行超过 Timeout
- **THEN** 对进程组先 SIGTERM，宽限期后 SIGKILL，确保子进程被清理

#### Scenario: 取消执行
- **WHEN** 收到 CANCEL_EXECUTION
- **THEN** 终止整个进程组并将状态置为 CANCELED

### Requirement: Journal 离线上传
系统 SHALL 在 Hub 不可达时将 Execution 标记 synced=false，Hub 恢复后上传并由 Hub 幂等 Upsert，收到 ACK 后标记 synced=true。

#### Scenario: 离线执行后上传
- **WHEN** Hub 恢复且存在 synced=false 的 Execution
- **THEN** Agent 上传 Execution 与日志，收到 ACK 后标记 synced=true

### Requirement: Agent 重启恢复 Journal
系统 SHALL 在启动时读取 Journal，对旧 RUNNING 且进程不存在的 Execution 标记 FAILED（reason=agent_restarted）。

#### Scenario: 重启标记中断执行
- **WHEN** Agent 重启发现 Journal 中 RUNNING 的 Execution 进程不存在
- **THEN** 标记 FAILED reason=agent_restarted

### Requirement: 日志持久化与上传
系统 SHALL 将日志持久化到本地并支持重连后上传；Hub 断开不影响本地日志；限制 stdout/stderr/total 大小，超限截断并标记。

#### Scenario: 离线期间日志
- **WHEN** Hub 断开期间执行并产生日志
- **THEN** 日志持久化到本地，重连后上传不丢失

#### Scenario: 日志超限
- **WHEN** 日志超过大小限制
- **THEN** 截断并标记 truncated

