## ADDED Requirements

### Requirement: 条件归属契约
系统 SHALL 在 Task API 中只接受本地条件，在 Schedule API 中只接受远程条件；Run 请求 SHALL 根据来源决定条件范围。

#### Scenario: Task API 拒绝远程条件
- **WHEN** 客户端向 Task API 提交 remote 条件
- **THEN** 返回 400，不持久化

#### Scenario: Schedule API 拒绝本地条件
- **WHEN** 客户端向 Schedule API 提交 local 条件
- **THEN** 返回 400，不持久化

#### Scenario: Run 请求条件来源
- **WHEN** 客户端提交 schedule_id 或 target 发起 Manual Run
- **THEN** schedule_id 请求使用对应 Schedule 的远程条件
- **AND** 任务页请求只使用 Task 本地条件

### Requirement: Execution 调度修订契约
系统 SHALL 在 Execution JSON 中返回可选 `schedule_revision`；旧记录与无来源调度的执行允许为 0 或省略。

#### Scenario: 查询调度执行
- **WHEN** 客户端查询由 Schedule 触发的 Execution
- **THEN** 响应包含 schedule_id 与 schedule_revision

#### Scenario: 查询历史执行
- **WHEN** 客户端查询无来源调度的 Execution
- **THEN** schedule_revision 为 0 或省略，其余字段正常返回
