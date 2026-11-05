## MODIFIED Requirements

### Requirement: Agent Gateway
系统 SHALL 提供独立的 Agent Gateway（如 :8443）承载持久控制通道，负责 Agent Auth、Heartbeat、Realtime Notification、Task Dispatch、Execution Status、Realtime Logs 与 Sync Protocol。控制通道 SHALL 支持两种建立方向：Agent 主动连接 Hub，或 Hub 按节点 endpoint 主动拨号 Agent；两种方向 SHALL 使用同一套协议、会话注册和状态机。

#### Scenario: Agent 建立长连接
- **WHEN** Agent 通过 Gateway 建立 WebSocket 连接并完成认证
- **THEN** 建立持久双向通道并注册 Session

#### Scenario: Hub 主动拨号
- **WHEN** 节点已签发入站 bundle、endpoint 已知且当前没有会话
- **THEN** Hub 按 endpoint 建立 TLS 连接、完成入站握手与 HELLO 认证，并注册同一 SessionManager 中的会话
- **AND** 首次引导不要求节点预先声明 `inbound_connection`，节点只有在 HELLO 被接受后才置为 online

#### Scenario: 会话状态仅内存维护
- **WHEN** Hub 重启
- **THEN** 内存 Session 丢失但不影响 Desired State 与 Execution 持久化数据，Agent 重连或 Hub 重新拨号后恢复

## ADDED Requirements

### Requirement: 入站握手与协议版本兼容

Hub SHALL 在入站连接上先发送 `HUB_HELLO`，收到 `HUB_HELLO_ACK` 后继续使用既有 `HELLO`/`HELLO_ACK` 完成注册与鉴权。`HUB_HELLO` 与 `HUB_HELLO_ACK` SHALL 复用 `HelloAckPayload` 的 accepted、node/agent、revision、settings 和 message 语义。`ProtocolVersion` SHALL 保持 `3`；旧 Hub SHALL NOT 向旧 Agent 发送这些消息，旧 Agent 的 HELLO/HEARTBEAT/SYNC 行为 SHALL 保持不变。Node SHALL 通过 `inbound_connection` 和 `inbound_data_plane` 能力声明入站支持。

#### Scenario: 入站握手成功
- **WHEN** Hub 与 Agent 完成 mTLS、`HUB_HELLO`/`HUB_HELLO_ACK`、`HELLO`/`HELLO_ACK`
- **THEN** Hub 注册会话并返回与出站模式一致的 Desired Global Revision、Settings 和 Node Status

#### Scenario: 首次入站引导
- **WHEN** pending 节点已签发 bundle、endpoint 已知但尚无任何 HELLO
- **THEN** Hub 允许发起首次入站握手，不要求预先存储 `inbound_connection` 能力
- **AND** 只有 HELLO 被接受后才注册会话并置 online

#### Scenario: 旧 Agent 被拨号
- **WHEN** 已签发 bundle 的节点首次被拨号，且 Agent 在 HELLO 中未声明 `inbound_connection`
- **THEN** Hub 完成本次握手后记录不支持入站，并停止后续拨号
- **AND** 不向旧 Agent 重复发送 `HUB_HELLO`，出站连接保持可用

#### Scenario: 旧 Hub 连接新 Agent
- **WHEN** 新 Agent 连接未实现入站拨号的旧 Hub
- **THEN** Agent 不使用 `HUB_HELLO` 发起连接，继续既有 HELLO 流程
- **AND** `ProtocolVersion` 仍为 3，旧 Hub 不因未知消息失败

#### Scenario: 协议版本不匹配
- **WHEN** 入站连接中的 `HELLO` 携带不被支持的 `ProtocolVersion`
- **THEN** Hub 返回拒绝的 `HELLO_ACK` 并关闭连接，不注册会话

### Requirement: 入站数据面错误契约

Hub SHALL 在无法使用入站数据面时返回稳定、可机器识别的错误，而不是等待超时。文件传输、Artifact、应用部署和自升级 SHALL 在创建/下发前检查当前会话方向、`inbound_data_plane` 能力和阶段可用性；不满足时 API SHALL 返回 `409` 与稳定 `code`，已进入执行链路的操作 SHALL 置为 `BLOCKED` 并带 `block_reason`。

#### Scenario: Agent 缺少数据面能力
- **WHEN** inbound 会话存在但 Agent 未声明 `inbound_data_plane`
- **THEN** 创建文件传输、Artifact 预取、应用部署或自升级的请求返回 409 `inbound_data_plane_unavailable`
- **AND** 不会发出无法完成的控制指令，也不会静默超时

#### Scenario: 阶段未实现
- **WHEN** inbound 模式某个数据面阶段尚未实现
- **THEN** 返回 409 `inbound_stage_unavailable` 并指出阶段
- **AND** 执行记录为 `BLOCKED`，原因可被 UI 展示

#### Scenario: 会话方向变化
- **WHEN** 传输或部署进行中会话从出站切换为入站或反向切换
- **THEN** 当前操作按明确错误失败或可恢复重试，不混用两种数据面
- **AND** 不会把半完成状态报告为成功
