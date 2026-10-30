## ADDED Requirements

### Requirement: Schedule 目标驱动同步
系统 SHALL 在 Schedule 的 Target、Enabled、Task 引用或 Group/Label 成员变化后重新计算受影响 Agent，并在事务 Commit 后通知；Agent 只同步其节点命中的 Schedule 及其 Task 与 Script。

#### Scenario: 调度目标变更
- **WHEN** 管理员修改 Schedule.Target，使某节点不再命中
- **THEN** Hub 在 Commit 后通知受影响 Agent
- **AND** 下一次同步返回该节点应删除的 Schedule 及其不再被引用的 Task

#### Scenario: 调度启用状态变更
- **WHEN** Schedule 被启用或禁用
- **THEN** 命中的 Agent 收到变更通知并在本地同步 enabled 状态

#### Scenario: 无调度的任务定义
- **WHEN** Task 没有被任何 Schedule 引用
- **THEN** Hub 不因该 Task 单独向 Agent 同步任务定义
- **AND** Manual Run 仍可通过 Dispatch 载荷执行该 Task
