## ADDED Requirements

### Requirement: Journal 调度修订号
Agent SHALL 在本地 Journal 与 EXECUTION_FINISHED 上报中持久化 `schedule_revision`；任务页手动执行可为 0。

#### Scenario: 调度执行上报修订号
- **WHEN** Agent 执行由 Schedule 触发的任务
- **THEN** Journal 与上报包含该 Schedule 的 Revision

#### Scenario: 手动执行
- **WHEN** Agent 执行 Hub 下发的任务页 Manual Run
- **THEN** schedule_revision 为 0

### Requirement: 应用类任务条件
Agent SHALL 对应用部署/操作类任务执行与命令/脚本任务相同的条件评估，不得跳过 Condition Engine。

#### Scenario: 应用部署条件不满足
- **WHEN** 应用类 Task 的条件评估为 false 或 unknown
- **THEN** 不执行应用操作，记录 SKIPPED 或 BLOCKED
