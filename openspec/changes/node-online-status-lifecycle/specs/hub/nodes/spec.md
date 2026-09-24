## ADDED Requirements

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
