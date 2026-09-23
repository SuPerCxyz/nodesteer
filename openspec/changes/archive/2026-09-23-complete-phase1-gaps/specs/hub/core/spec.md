## Purpose

补齐 Hub 端一期功能缺口：Cron Schedule 触发、Remote Node Condition、Tombstone 删除同步、Execution/权限/审计一致性。

## ADDED Requirements

### Requirement: Cron Schedule 触发
系统 SHALL 支持 Cron 类型的 Schedule 在 Hub（execution_owner=hub）侧按标准 cron 表达式正确触发。

#### Scenario: Cron 到点触发
- **WHEN** 存在启用的 Cron Schedule 且已到触发时刻
- **THEN** Hub 为该 Schedule 创建 Execution 并分发给目标节点

#### Scenario: Cron 表达式非法
- **WHEN** Schedule 的 Cron 表达式无法解析
- **THEN** 该 Schedule 被跳过并记录错误，不影响其他 Schedule

### Requirement: Remote Node Condition
系统 SHALL 支持远程节点条件求值：`node == ONLINE`、`node.last_execution(task) == SUCCESS`。

#### Scenario: 查询在线状态
- **WHEN** 条件引用另一节点的在线状态
- **THEN** 返回该节点 Value/ObservedAt/TTL，未过期的真实值参与求值

#### Scenario: 状态过期
- **WHEN** Remote State 超出 TTL 或不可确认
- **THEN** 视为 UNKNOWN，条件评估结果 Fail Closed（BLOCKED），不得将 UNKNOWN 当作 TRUE

### Requirement: Tombstone 删除同步
系统 SHALL 在删除 Script/Task/Schedule/Application 时产生 Tombstone，供 Agent 同步删除本地副本。

#### Scenario: 删除对象产生 Tombstone
- **WHEN** Hub 删除任一 Desired State 对象
- **THEN** 该对象的删除记录进入 Agent 同步响应，Agent 停止调度并删除本地副本、保留历史 Execution

### Requirement: Manual Run 幂等
系统 SHALL 支持同一 Task 在相同节点上的多次手动执行。

#### Scenario: 连续手动执行
- **WHEN** 同一 Task 对同一节点连续触发多次 Manual Run
- **THEN** 每次创建独立 Execution 并执行，不被上一次记录阻塞

### Requirement: 执行状态与取消
系统 SHALL 完整支持 Execution 状态机 PENDING/RUNNING/SUCCESS/FAILED/SKIPPED/CANCELED/TIMED_OUT。

#### Scenario: 取消执行
- **WHEN** 用户对 RUNNING 执行发起取消
- **THEN** Hub 通知 Agent 取消并记录 CANCELED 状态

#### Scenario: 超时执行
- **WHEN** 执行超过 Task.Timeout
- **THEN** Agent 终止进程组并上报 TIMED_OUT

### Requirement: Operator 权限
系统 SHALL 限定 Operator 角色仅可查看、运行任务、查看日志、部署与操作 Managed Application，不可创建/编辑/删除定义类对象。

#### Scenario: Operator 写定义被拒
- **WHEN** Operator 尝试创建/编辑/删除 Script/Task/Schedule/Group/Artifact/Application
- **THEN** 返回 403

### Requirement: Application Upgrade 审计
系统 SHALL 区分记录应用部署动作：deploy / upgrade / start / stop / restart。

#### Scenario: 升级审计
- **WHEN** 对应用执行升级
- **THEN** 审计日志记录动作类型为 upgrade
