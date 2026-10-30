## ADDED Requirements

### Requirement: Task 只承载本地条件
系统 SHALL 只允许 Task.Condition 包含 `local` 或由本地条件组成的 `and`；Task 中出现 `remote` 条件（含嵌套）时 SHALL 返回 400。

#### Scenario: 创建带本地条件的任务
- **WHEN** 管理员提交 `condition.type=local` 或本地 `and`
- **THEN** 系统校验并持久化本地条件

#### Scenario: 任务写入远程条件
- **WHEN** 管理员在 Task 中提交 `remote` 条件或混合 `and`
- **THEN** 系统返回 400，不持久化

### Requirement: Schedule 只承载远程条件
系统 SHALL 允许 Schedule.Condition 包含 `remote` 或由远程条件组成的 `and`；Schedule 中出现 `local` 条件（含嵌套）时 SHALL 返回 400。

#### Scenario: 创建带远程条件的调度
- **WHEN** 管理员为调度计划配置远程节点在线或 `last_execution` 条件
- **THEN** 系统校验节点/任务引用并持久化 Schedule.Condition

#### Scenario: 调度写入本地条件
- **WHEN** 管理员在 Schedule 中提交 `local` 条件或混合 `and`
- **THEN** 系统返回 400，不持久化

### Requirement: 旧条件拆分为本地与远程
系统 SHALL 在升级迁移中把旧 Task.Condition 拆分为本地与远程两部分：本地部分保留在 Task，远程部分复制到该 Task 的全部 Schedule；混合 `and` SHALL 递归拆分且不得丢失条件。

#### Scenario: 混合条件迁移
- **WHEN** 旧 Task 包含本地和远程条件的 `and`
- **THEN** Task 只保留本地部分，全部关联 Schedule 获得远程部分

#### Scenario: 无调度的旧任务
- **WHEN** 旧 Task 有远程条件但没有 Schedule
- **THEN** 远程条件写入该 Task 的迁移 manual 调度；无法绑定时记录告警并保留本地部分
