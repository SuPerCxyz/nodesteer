## ADDED Requirements

### Requirement: Execution 来源与创建时间
Agent SHALL 在本地 Journal 与 EXECUTION_FINISHED 上报中持久化可空 schedule_id 与 created_at；手动执行的 schedule_id 为空，调度执行的 schedule_id 必须与触发它的 Schedule 一致。

#### Scenario: 调度执行上报来源
- **WHEN** Agent 执行由 Schedule 触发的任务并上报 EXECUTION_FINISHED
- **THEN** 上报载荷包含该 Schedule 的 ID 与 Execution 创建时间
- **AND** Hub 幂等写入后可在执行记录中展示来源调度

#### Scenario: 手动执行上报
- **WHEN** Agent 执行 Hub 下发的 Manual Run
- **THEN** schedule_id 为空，created_at 为 Hub 创建执行的时间或 Agent 接收时间

### Requirement: Execution 过程时间线字段
Agent SHALL 在执行记录中维护 created_at、start_time、end_time 与终态，供 Hub 和执行详情页还原执行过程。

#### Scenario: 执行时间线
- **WHEN** Execution 从 PENDING 进入 RUNNING 并结束
- **THEN** 本地与上报记录包含创建、开始、结束时间和最终状态
