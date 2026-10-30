## MODIFIED Requirements

### Requirement: Complete reconciliation

The Hub SHALL provide enough desired-state information for an Agent to remove objects no longer assigned to it, including full resync.

#### Scenario: Removed target

- **WHEN** a schedule target changes so that an Agent no longer matches it
- **THEN** the next sync response identifies the stale task and related schedule for deletion
- **AND** the Agent can converge without a manual restart

### Requirement: 执行状态与取消
系统 SHALL 完整支持 Execution 状态机 PENDING/RUNNING/SUCCESS/FAILED/SKIPPED/CANCELED/TIMED_OUT。

#### Scenario: 取消执行
- **WHEN** 用户对 RUNNING 执行发起取消
- **THEN** Hub 通知 Agent 取消并记录 CANCELED 状态

#### Scenario: 超时执行
- **WHEN** 执行超过 Schedule.Timeout 或 Manual Run 请求中的 Timeout
- **THEN** Agent 终止进程组并上报 TIMED_OUT

### Requirement: Run User 接线
系统 SHALL 支持以指定用户运行任务。

#### Scenario: 指定运行用户
- **WHEN** Schedule 或 Manual Run 请求配置了 run_user，且 Script 未覆盖该配置，且 Agent 以 root 运行
- **THEN** 执行进程以该用户身份启动
- **AND** 未配置时保持现状（当前用户）

## ADDED Requirements

### Requirement: 调度目标解析
系统 SHALL 在调度触发时从 Schedule.Target 解析目标节点，并在 Manual Run 时从请求级 Target 解析目标节点；不得再读取 Task 上的目标字段。

#### Scenario: 调度按计划目标执行
- **WHEN** 启用的 Schedule 到达触发时刻
- **THEN** Hub 或 Agent 按该 Schedule 的 Target 解析节点并为每个节点创建 Execution

#### Scenario: 手动执行使用临时目标
- **WHEN** 用户从任务定义页发起试运行并提供临时 Target
- **THEN** 系统按该 Target 解析节点，不修改任务定义或任何 Schedule

#### Scenario: 无目标的手动执行
- **WHEN** Manual Run 请求既没有 Target 也没有可解析的节点
- **THEN** 系统返回 400，不创建 Execution
