## Purpose

Hub 管理 Script、Task、Schedule 定义及其 Revision、Assignment 与删除同步，作为 Agent Desired State 的内容来源。

## ADDED Requirements

### Requirement: Script Library
系统 SHALL 支持创建/编辑/克隆/删除 Script，支持 Shell、Bash、Python 解释器，字段包含 Name、Description、Interpreter、Content、Parameters、Environment、Working Directory、Timeout、Enabled。

#### Scenario: 创建 Script
- **WHEN** 管理员创建 Script 并保存
- **THEN** 系统生成 Script Revision 1，计算 SHA256，并在 Revision History 中可查

#### Scenario: 编辑 Script 产生新 Revision
- **WHEN** 管理员编辑 Script 内容
- **THEN** 系统创建新 Revision 且旧 Revision 历史保留

#### Scenario: 禁用 Script
- **WHEN** 管理员将 Script 置为 Disabled
- **THEN** 引用该 Script 的 Task 不得在满足前执行

### Requirement: Task 定义
系统 SHALL 支持创建/编辑/启用/禁用 Task，一期类型包含 Command、Script、Application Deploy、Application Operation（Start/Stop/Restart/Upgrade），字段包含 Name、Type、Target、Parameters、Condition、Schedule、Timeout、Retry、Offline Policy、Enabled。

#### Scenario: 创建 Script Task
- **WHEN** 管理员创建引用已存在 Script 的 Task
- **THEN** 系统校验 Script 存在并生成 Task Revision

#### Scenario: Task 引用不存在的 Script
- **WHEN** 管理员创建引用不存在 Script 的 Task
- **THEN** 系统拒绝创建并返回错误

#### Scenario: 任务目标为 Node Group
- **WHEN** Task Target 指定 Node Group 或 Label
- **THEN** Sync Manager 据此计算每个 Agent 应同步的 Desired State

### Requirement: Task Revision 与 Definition JSON
系统 SHALL 为每个 Task 维护当前 Revision 与 Revision History，Definition 使用索引字段加 JSON 存储。

#### Scenario: 查看 Task Revision 历史
- **WHEN** 查看 Task 详情
- **THEN** 展示当前 Revision、历史 Revision 及每次修改时间

### Requirement: Schedule 定义
系统 SHALL 支持 Cron、Interval、One-Time 三种 Schedule，保存 IANA Timezone，字段包含 type、expression、timezone、execution_owner（HUB/AGENT）、offline_policy、misfire_policy、enabled。

#### Scenario: 创建 Allow Offline 的 Cron Schedule
- **WHEN** 管理员创建 execution_owner=AGENT 的 Cron Schedule
- **THEN** 系统持久化 Schedule 并随 Desired State 同步到目标 Agent

#### Scenario: 创建 Hub Online Required Schedule
- **WHEN** 管理员创建 execution_owner=HUB 的 Schedule
- **THEN** Hub 在到期时创建 Execution 并调度下发

### Requirement: Tombstone 删除同步
系统 SHALL 在删除 Script/Task/Schedule/Application 时产生 Tombstone 删除记录，Agent 收到后停止未来调度、删除本地 Desired Definition、保留历史 Execution。

#### Scenario: 删除 Task 产生 Tombstone
- **WHEN** 管理员删除已同步到 Agent 的 Task
- **THEN** Hub 在 Change Log 记录 DELETE 操作，Agent 同步后移除本地定义并停止其调度

### Requirement: 对象 Revision 单调递增
系统 SHALL 为 Script、Task、Schedule、Application 维护全局单调递增的 Object Revision，并维护 Global State Revision。

#### Scenario: 修改对象递增 Revision
- **WHEN** 任何 Desired State 对象被修改
- **THEN** 其 Object Revision 与 Global Revision 均递增

## ADDED Requirements

### Requirement: Desired State 计算
系统 SHALL 根据 Task Target、Group、Label、Application Assignment 计算每个 Agent 应同步的对象集合，Agent 无需下载整个 Hub 数据库。

#### Scenario: 仅同步相关对象
- **WHEN** Agent 请求同步
- **THEN** Hub 仅返回该 Agent 相关（Target 命中其 Group/Label 或 Assignment 指定）的对象
