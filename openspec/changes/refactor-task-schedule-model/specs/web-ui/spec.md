## MODIFIED Requirements

### Requirement: Real application and node state

The Web UI SHALL display real application health, deployment results, execution history, current assignments, and schedules whose node, group, or label target matches a node, together with the tasks referenced by those schedules.

#### Scenario: Node detail

- **WHEN** a user opens a node detail page
- **THEN** the page lists schedules whose target matches the node and the tasks referenced by those schedules
- **AND** displays current application assignments and health from the API

### Requirement: Complete task run navigation

The Web UI SHALL render the dedicated task run page when a user chooses the task run action.

#### Scenario: Open task run page

- **WHEN** a user selects “Run Now” from a task list or task detail page
- **THEN** `/tasks/:taskId/run` renders the run confirmation view with the selected task, a target selector (node/group/label), execution strategy fields, loading/error state, confirmation action, and return action

### Requirement: Schedule 创建与编辑
系统 SHALL 提供 Schedule 创建与编辑界面。

#### Scenario: 创建 Schedule
- **WHEN** 用户填写名称、任务、目标节点/分组/标签、类型（cron/interval/one_time/on_start/manual）、表达式、时区、owner、timeout/retry/run_user、offline/misfire 策略并保存
- **THEN** 调用真实 API 创建 Schedule 并显示在列表

#### Scenario: 编辑 Schedule
- **WHEN** 用户修改现有 Schedule 并保存
- **THEN** 调用真实 API 更新

### Requirement: Task 增强
系统 SHALL 在 Task 详情页提供关联 Schedule 只读区块与 Execution History。

#### Scenario: 任务编辑
- **WHEN** 打开 Task 详情页
- **THEN** 可查看引用该 Task 的 Schedules、按 task_id 查看执行历史
- **AND** Task 编辑页不再要求选择目标节点或填写执行策略

## ADDED Requirements

### Requirement: 任务定义 / 调度计划 / 执行记录命名
Web UI SHALL 使用「任务定义」「调度计划」「执行记录」作为菜单、页面标题和主要操作文案，并保持中英文文案一致。

#### Scenario: 侧边栏命名
- **WHEN** 用户查看左侧运行分组
- **THEN** 菜单显示任务定义、调度计划、执行记录
- **AND** 页面标题与描述使用同一术语

#### Scenario: 旧术语清理
- **WHEN** 用户浏览任务、调度或执行页面
- **THEN** 不再出现将 Task 称为「模板/目标」或将执行页仅称为「结果」的混淆文案

### Requirement: 执行过程与结果展示
Web UI SHALL 在执行记录详情页同时展示执行过程与执行结果。

#### Scenario: 执行过程
- **WHEN** 用户打开执行详情
- **THEN** 页面展示创建、开始、结束时间线，当前状态与 block_reason，并保留实时日志

#### Scenario: 执行结果
- **WHEN** 执行进入终态
- **THEN** 页面展示状态、耗时、退出码、stdout/stderr 摘要与截断标记
- **AND** 调度触发的执行展示来源调度计划及跳转入口

#### Scenario: 进行中的执行
- **WHEN** 执行处于 PENDING 或 RUNNING
- **THEN** 页面仍展示过程时间线与实时日志，不因「结果」命名隐藏进行中状态

#### Scenario: 从调度计划立即执行
- **WHEN** 用户从调度计划页点击「立即执行」
- **THEN** 请求只携带该 Schedule 的 ID，后端使用其 Target 与策略
- **AND** 执行记录展示来源调度，触发方式仍为手动
