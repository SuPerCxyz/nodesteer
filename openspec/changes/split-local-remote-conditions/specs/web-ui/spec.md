## ADDED Requirements

### Requirement: 任务本地条件编辑
Web UI SHALL 在任务定义编辑器中只提供本地条件编辑，远程条件不再出现在任务定义页面。

#### Scenario: 任务编辑器
- **WHEN** 用户编辑任务定义
- **THEN** 只能配置本地资源条件
- **AND** 页面不展示远程节点/任务依赖条件

### Requirement: 调度远程条件编辑
Web UI SHALL 在调度计划编辑器中提供远程条件编辑，支持节点在线与 `last_execution` 条件，并在保存时提交到 Schedule.Condition。

#### Scenario: 调度编辑器远程条件
- **WHEN** 用户编辑调度计划
- **THEN** 可配置远程节点在线或另一任务上次执行结果条件
- **AND** 保存后调度详情显示远程条件摘要

### Requirement: 执行详情调度修订号
Web UI SHALL 在执行详情中展示来源调度的 Revision（当 `schedule_revision > 0` 时），并保留来源调度链接。

#### Scenario: 查看调度执行
- **WHEN** 用户打开由调度触发的执行详情
- **THEN** 展示来源调度名称与修订号

#### Scenario: 任务页手动执行
- **WHEN** 执行无来源调度修订号
- **THEN** 不展示空的修订号字段
