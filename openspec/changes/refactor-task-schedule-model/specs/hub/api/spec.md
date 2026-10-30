## ADDED Requirements

### Requirement: Task 与 Schedule 契约
系统 SHALL 在 REST API 中让 Task 只包含执行内容字段，让 Schedule 包含 Task ID、Name、Target 与执行策略；Task 响应不得再返回 target、timeout、retry、run_user、offline_policy 或 schedule 字段。

#### Scenario: 读取任务定义
- **WHEN** 客户端调用 GET /api/tasks 或 GET /api/tasks/{id}
- **THEN** 响应中的 Task 不含 Target 与执行策略字段
- **AND** Task 仍包含类型、命令/脚本/应用、参数、条件、启用状态与修订号

#### Scenario: 读取调度计划
- **WHEN** 客户端调用 GET /api/schedules 或 GET /api/schedules/{id}
- **THEN** 响应包含 name、target、timeout、retry、run_user 与现有触发器/策略字段

#### Scenario: 创建缺少目标的调度
- **WHEN** 客户端创建 Schedule 但未提供有效 Target
- **THEN** 系统返回 400，不持久化 Schedule

### Requirement: Manual Run 请求契约
系统 SHALL 让 POST /api/tasks/{id}/run 接受请求级 Target 与执行策略，并对每个解析出的节点创建 Execution；请求不得修改 Task 或 Schedule。请求 MAY 携带 `schedule_id`：提供时使用该 Schedule 的 Target 与策略，并把来源写入 Execution。

#### Scenario: 试运行指定目标
- **WHEN** 客户端提交包含 target、timeout、retry、run_user、offline_policy 的 Run 请求
- **THEN** 系统按目标节点创建 Execution 并返回执行记录
- **AND** Task 与 Schedule 定义保持不变

#### Scenario: 兼容显式节点列表
- **WHEN** 客户端提交 node_ids 而非 target
- **THEN** 系统按 node_ids 构造临时 node Target 并执行

#### Scenario: 从调度计划立即执行
- **WHEN** 客户端提交 schedule_id
- **THEN** 系统使用该 Schedule 的 Target 与策略创建 Execution
- **AND** Execution 记录 schedule_id，trigger_type 仍为 manual

### Requirement: Execution 契约
系统 SHALL 在 Execution API 中返回可空 schedule_id 与 created_at，并保持既有状态、退出码、stdout/stderr、offline、synced 与 block_reason 字段。

#### Scenario: 查询调度执行
- **WHEN** 客户端查询由 Schedule 触发的 Execution
- **THEN** 响应包含对应 schedule_id 与 created_at

#### Scenario: 查询历史执行
- **WHEN** 客户端查询不包含来源调度的历史 Execution
- **THEN** schedule_id 为空或省略，其余字段正常返回
