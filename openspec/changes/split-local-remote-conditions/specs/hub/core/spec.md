## ADDED Requirements

### Requirement: 条件合并与评估顺序
系统 SHALL 在执行前把 Task 本地条件与 Schedule 远程条件合并为一个 `and` 条件树，本地条件在前、远程条件在后；任一本地条件不满足时 SHALL 为 SKIPPED，远程状态未知时 SHALL 为 BLOCKED。

#### Scenario: 调度触发合并条件
- **WHEN** Schedule 触发且 Task 与 Schedule 都配置了条件
- **THEN** 执行载荷包含合并后的条件树
- **AND** Agent 先评估本地条件，再评估远程条件

#### Scenario: 本地条件不满足
- **WHEN** Task 本地条件为 false
- **THEN** Execution 状态为 SKIPPED，不执行任务

#### Scenario: 远程条件未知
- **WHEN** 远程条件无法确认或状态过期
- **THEN** Execution 状态为 BLOCKED，不执行任务

### Requirement: 手动执行条件范围
系统 SHALL 让任务页试运行只评估 Task 本地条件；从调度计划页立即执行时 SHALL 评估 Task 本地条件与 Schedule 远程条件。

#### Scenario: 任务页试运行
- **WHEN** 用户从任务定义页发起试运行
- **THEN** 只使用 Task 本地条件，不使用任何 Schedule 远程条件

#### Scenario: 调度页立即执行
- **WHEN** 用户从调度计划页发起立即执行
- **THEN** 同时使用 Task 本地条件与该 Schedule 的远程条件

### Requirement: 应用类任务条件生效
系统 SHALL 让应用部署/操作类任务的手动执行走与调度执行相同的条件评估路径，不得因 `DEPLOY_REQUEST` 路径绕过条件。

#### Scenario: 应用类任务手动执行
- **WHEN** 用户手动执行带条件的应用类 Task
- **THEN** 先评估 Task 本地条件（以及调度页来源的远程条件），满足后才执行应用操作
