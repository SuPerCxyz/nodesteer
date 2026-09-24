# hub/nodes Specification

## Purpose
Hub 管理 Agent Node 的注册、心跳、在线状态、Inventory、Capability、Label 与 Group，作为节点生命周期与同步调度的基础。
## Requirements
### Requirement: Node 列表与详情 API
系统 SHALL 提供 Node 列表与详情 API，至少包含 Node ID、Hostname、IP、OS、Architecture、Labels、Agent Version、Deployment Mode、Host Integration、Sync Status、Last Seen。

#### Scenario: 查询节点列表
- **WHEN** 调用 GET /api/nodes
- **THEN** 返回包含上述字段的 Node 列表

### Requirement: 节点在线状态机

系统 SHALL 维护节点在线状态的三段生命周期：纳管创建的记录初始为 `pending`（等待 Agent 接入）；仅当 Agent 的 HELLO 被接受后置为 `online`；已在线节点连接断开或心跳超时后置为 `offline`。`maintenance` 与 `disabled` 状态 SHALL 不被任何自动转换覆盖。

#### Scenario: 纳管创建显示等待

- **WHEN** 管理员生成纳管命令并持久化 pending node record
- **THEN** 该节点 status 为 `pending`，Web 列表显示「等待中」
- **AND** 在 HELLO 被接受前不得显示为 `online`

#### Scenario: HELLO 接受后上线

- **WHEN** Agent 携有效 credential 或 registration token 完成 HELLO 且被接受
- **THEN** 节点 status 置为 `online`
- **AND** `maintenance`/`disabled` 节点保持原状态不被覆盖

#### Scenario: HELLO 被拒绝不滞留在线

- **WHEN** Agent 的 HELLO 被拒绝（credential 无效、节点不存在或 registration token 无效）
- **AND** 该拒绝可定位到既有节点
- **THEN** 若该节点当前为 `online` 且没有活跃 Agent 会话，在关闭连接前置为 `offline`
- **AND** 若该节点存在活跃 Agent 会话，拒绝不改变其状态
- **AND** 从未成功会话的 `pending` 节点保持 `pending`

#### Scenario: 断开与心跳超时置离线

- **WHEN** 已在线节点连接断开或心跳超时
- **THEN** 节点 status 置为 `offline`
- **AND** `maintenance`/`disabled` 不被该转换覆盖

#### Scenario: Hub 启动复位孤儿在线

- **WHEN** Hub 启动
- **THEN** 所有没有活跃 Agent 会话的 `online` 节点被复位为 `offline`
- **AND** 持有活跃会话的节点状态不受影响

### Requirement: 节点暂停语义

节点处于 `maintenance`（暂停）时，系统 SHALL 拦截全部任务型下发：手动执行、调度触发、应用部署/操作、文件传输指令、制品预取指令；settings 下发与变更通知等配置同步 SHALL 不受影响。Agent 在暂停态 SHALL 拒收执行与部署指令作为双保险。暂停 SHALL 不终止节点上已部署的常驻服务，也不中止运行中的任务，仅停止新下发。调度因暂停跳过时 SHALL 产生 SKIPPED 执行记录。管理员 SHALL 可批量将所选节点设置为暂停或恢复。

#### Scenario: 暂停拦截任务型下发

- **WHEN** 节点处于暂停状态
- **THEN** 手动执行、调度触发、应用部署、文件传输与制品预取请求不被下发到该节点
- **AND** settings 下发与变更通知照常送达

#### Scenario: 暂停期常驻服务与运行中任务

- **WHEN** 节点处于暂停状态且其上已部署常驻服务、存在运行中任务
- **THEN** 已部署服务继续运行，运行中的任务执行至完成
- **AND** 不再向该节点派发任何新任务

#### Scenario: 调度跳过留痕

- **WHEN** 调度到点但目标节点处于暂停状态
- **THEN** 创建 SKIPPED 状态的执行记录（含目标节点与跳过原因），不再静默丢弃

#### Scenario: Agent 暂停态拒收

- **WHEN** Agent 本地处于暂停态且收到执行或部署指令
- **THEN** Agent 拒收该指令并回报，不启动本地执行

#### Scenario: 批量暂停与恢复

- **WHEN** 管理员在节点列表多选节点并执行批量暂停（或恢复）
- **THEN** 所选节点状态统一变更，逐节点产生状态操作结果反馈
