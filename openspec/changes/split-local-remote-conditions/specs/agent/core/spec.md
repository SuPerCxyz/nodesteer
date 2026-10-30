## ADDED Requirements

### Requirement: Agent 条件合并
Agent SHALL 在本地调度触发时合并 Task 本地条件与 Schedule 远程条件，并按照本地优先、远程在后的顺序评估。

#### Scenario: Agent-owned 调度条件
- **WHEN** Agent 本地触发带条件的 Schedule
- **THEN** 执行载荷包含 Task 本地条件与 Schedule 远程条件的合并树
- **AND** 本地条件失败为 SKIPPED，远程未知为 BLOCKED

#### Scenario: 无远程条件
- **WHEN** Schedule 未配置条件
- **THEN** Agent 只评估 Task 本地条件

### Requirement: 条件评估结果可追溯
Agent SHALL 在执行记录中保留条件评估结果对应的终态与 block_reason，并随 Execution 上报。

#### Scenario: 条件阻断
- **WHEN** 条件评估为 false 或 unknown
- **THEN** Journal 与 Hub 记录 SKIPPED 或 BLOCKED 及原因
