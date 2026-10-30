## MODIFIED Requirements

### Requirement: Task Retry
系统 SHALL 在 Task 执行失败时按 Retry 次数重试。

#### Scenario: 失败重试
- **WHEN** 执行失败且 Schedule.Retry 或 Manual Run 请求中的 Retry > 0
- **THEN** Agent 在失败后重新执行，最多 Retry 次

### Requirement: WorkingDir 与 RunUser
系统 SHALL 将 Script 的 WorkingDir 与 Schedule/Manual Run 的 RunUser 传入进程执行。

#### Scenario: 工作目录生效
- **WHEN** Script 配置 WorkingDir
- **THEN** 进程在指定目录启动

#### Scenario: 运行用户生效
- **WHEN** Schedule 或 Manual Run 请求配置 RunUser，且 Script 未覆盖该配置
- **THEN** 进程以该用户身份启动

## ADDED Requirements

### Requirement: Schedule 目标与策略
Agent SHALL 从同步的 Schedule 读取 Target、Timeout、Retry、Run User、Offline Policy、Misfire Policy 与 Execution Owner，并在本地触发时使用这些字段；Agent SHALL NOT 从 Task 读取目标或执行策略。

#### Scenario: Agent-owned 调度触发
- **WHEN** Local Scheduler 触发 execution_owner=agent 的 Schedule
- **THEN** Agent 使用该 Schedule 的 Target 确定本机执行，并使用其 Timeout/Retry/Run User/Offline Policy

#### Scenario: 仅手动调度
- **WHEN** Schedule 的类型为 manual
- **THEN** Local Scheduler 不自动触发该 Schedule

#### Scenario: 缺少目标
- **WHEN** 同步或下发的 Schedule 没有有效 Target
- **THEN** Agent 拒绝该调度触发并回报明确错误，不执行任务
