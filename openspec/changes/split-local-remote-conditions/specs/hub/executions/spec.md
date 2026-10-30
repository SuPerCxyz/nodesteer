## ADDED Requirements

### Requirement: Execution 调度修订号
系统 SHALL 为 Schedule 触发的 Execution 记录可空 `schedule_revision`，用于追溯触发时的调度版本；任务页 Manual Run 与历史记录的 `schedule_revision` 允许为 0。

#### Scenario: 调度触发记录修订号
- **WHEN** Schedule 触发创建 Execution
- **THEN** Execution 持久化该 Schedule 的 Revision
- **AND** 执行详情可展示来源调度修订号

#### Scenario: 任务页手动执行
- **WHEN** 用户从任务定义页试运行
- **THEN** Execution 的 schedule_revision 为 0 或不返回

#### Scenario: 调度页立即执行
- **WHEN** 用户从调度计划页立即执行
- **THEN** Execution 记录该 Schedule 的当前 Revision
