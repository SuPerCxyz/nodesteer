## MODIFIED Requirements

### Requirement: Manual Run
系统 SHALL 支持 Web Run Now：管理员为 Task 提供请求级 Target（node/group/label）与执行策略（Timeout、Retry、Run User、Offline Policy），Hub 为每个目标节点创建 Execution UUID、持久化 PENDING、向 Agent Dispatch；Agent 按 Execution ID 去重执行。Agent Offline 时 Manual Run 明确返回不可执行，不进入长期等待队列。

#### Scenario: Manual Run 成功
- **WHEN** 管理员对在线 Agent 执行 Run Now 并提供有效 Target
- **THEN** Hub 创建 PENDING Execution，下发后 Agent 去重并执行，最终更新 SUCCESS/FAILED

#### Scenario: Agent Offline 时 Manual Run
- **WHEN** 目标 Agent Offline 时管理员执行 Run Now
- **THEN** Hub 明确返回不可执行错误，不创建长期等待队列

#### Scenario: 缺少或无效目标
- **WHEN** Manual Run 请求未提供 Target，或 Target 无法解析出任何节点
- **THEN** Hub 返回 400，不创建 Execution

### Requirement: Execution 状态机
系统 SHALL 支持 PENDING→RUNNING→{SUCCESS,FAILED,TIMED_OUT,CANCELED}，Condition 不满足时为 SKIPPED。

#### Scenario: 执行成功
- **WHEN** Execution 正常完成
- **THEN** 状态更新为 SUCCESS 并记录 Exit Code

#### Scenario: 执行超时
- **WHEN** 执行超过 Schedule.Timeout 或 Manual Run 请求中的 Timeout
- **THEN** 状态更新为 TIMED_OUT 并清理进程组

### Requirement: Execution 记录字段
系统 SHALL 记录 Execution ID、Task ID、Task Revision、Script Revision、Node ID、Trigger Type、Scheduled Time、Created At、Start/End Time、Status、Exit Code、stdout、stderr、Offline 标志，以及可空的 Schedule ID。

#### Scenario: 查看 Execution 详情
- **WHEN** 查看 Execution 详情
- **THEN** 展示上述全部字段及日志
- **AND** 调度触发的执行展示来源 Schedule，历史记录允许 Schedule ID 为空

## ADDED Requirements

### Requirement: Execution 来源与过程时间
系统 SHALL 为调度触发的 Execution 记录 Schedule ID，并在创建时记录 Created At；手动执行与旧历史记录的 Schedule ID 允许为空。

#### Scenario: 调度触发记录来源
- **WHEN** Schedule 触发创建 Execution
- **THEN** Execution 持久化该 Schedule 的 ID
- **AND** 执行详情可反查调度计划名称与触发器

#### Scenario: 手动执行无来源调度
- **WHEN** 用户通过 Manual Run 创建 Execution
- **THEN** Execution 的 Schedule ID 为空，Trigger Type 为 manual
