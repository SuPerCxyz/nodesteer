## Purpose

修复 Web UI 一期缺口：Node Detail 真实分配数据、Application Versions/Env/Health、Task Schedule 区块与执行历史、Script Parameters/Environment、Group 编辑、Condition remote/and 编辑。

## ADDED Requirements

### Requirement: Node Detail Managed Applications
系统 SHALL 使用真实分配数据展示节点的 Managed Applications。

#### Scenario: 展示已分配应用
- **WHEN** 打开 Node Detail
- **THEN** Managed Applications 分区调用 `/applications/{id}/nodes` 反向关联展示真实分配的应用
- **AND** 不再使用执行记录启发式推断

### Requirement: Application 增强
系统 SHALL 支持 Version 历史查看、Environment 输入、真实 Health 展示。

#### Scenario: 应用详情
- **WHEN** 打开 Application 编辑页
- **THEN** 可查看版本历史、编辑 environment、展示已分配节点

### Requirement: Task 增强
系统 SHALL 在 Task 编辑页提供 Schedule 区块与 Execution History。

#### Scenario: 任务编辑
- **WHEN** 打开 Task 编辑页
- **THEN** 可查看关联 Schedules、按 task_id 查看执行历史
