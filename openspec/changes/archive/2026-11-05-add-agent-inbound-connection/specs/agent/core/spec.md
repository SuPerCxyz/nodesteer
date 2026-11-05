## MODIFIED Requirements

### Requirement: Agent 主动连接
系统 SHALL 支持 Agent 主动向 Hub Agent Gateway 建立持久连接；Agent 默认不开放远程管理端口，不依赖 Hub SSH 登录节点。当且仅当安装了入站 bundle 时，Agent SHALL 同时提供一个仅接受 Hub 拨号的 TLS listener，并与出站连接自动协调为单一已认证会话。

#### Scenario: Agent 主动连接
- **WHEN** Agent 启动且 Hub 可达
- **THEN** Agent 主动建立 WebSocket 连接并完成 AUTH/HELLO

#### Scenario: Hub 不可达重连
- **WHEN** Hub 临时不可达
- **THEN** Agent 按退避策略持续重连且不影响本地 Scheduler 继续工作

#### Scenario: 未安装入站身份
- **WHEN** Agent 没有入站证书、私钥或令牌
- **THEN** Agent 不监听任何管理端口，行为与旧版本一致

#### Scenario: 同时具备出站与入站
- **WHEN** Agent 安装了入站 bundle 且出站 Hub 可达
- **THEN** Agent 同时启动出站尝试和入站 listener，但只保留一个已认证会话
- **AND** 出站成功后关闭入站会话，入站存活期间暂停出站重试以避免抖动

## ADDED Requirements

### Requirement: 入站 TLS listener 与握手

Agent SHALL 在配置的入站地址（默认 `:18444`）提供 TLS listener，要求 mTLS 客户端证书、Hub 客户端证书指纹 pin 和每节点入站令牌。Agent SHALL 校验 `HUB_HELLO` 的目标 node_id/agent_id、nonce 和时间戳，并在 `HUB_HELLO_ACK` 中返回基于每节点令牌的 HMAC proof；Hub SHALL 校验该 proof 后继续使用既有 `HELLO`/`HELLO_ACK`。Agent SHALL 限制握手并发、来源 IP、请求速率和单节点连接数；任何校验失败 SHALL 关闭连接，不得降级为明文或跳过 pin。

#### Scenario: 有效 Hub 拨号
- **WHEN** Hub 使用有效客户端证书、匹配的指纹和正确的节点令牌连接
- **THEN** Agent 返回 `HUB_HELLO_ACK`，在同一连接上继续 HELLO 握手并接受 Hub 的会话
- **AND** 只有收到 accepted 的 `HELLO_ACK` 后才把连接标记为已连接

#### Scenario: 指纹或令牌错误
- **WHEN** 客户端证书链有效但指纹不匹配，或 `HUB_HELLO_ACK` 的令牌 proof 不匹配
- **THEN** 对应一侧关闭连接且不建立会话
- **AND** 记录不含秘密的失败原因和限速计数

#### Scenario: 重复连接
- **WHEN** 节点已有出站或入站已认证会话时收到新的入站握手
- **THEN** Agent 按所有权规则拒绝或关闭重复连接
- **AND** 不会同时向 Hub 发送两条会话的业务消息

#### Scenario: 握手超时或重放
- **WHEN** TLS、`HUB_HELLO` 或 `HELLO` 超过配置时限，或 nonce/时间戳校验失败
- **THEN** Agent 关闭连接并拒绝该次会话
- **AND** 不把半握手连接计入在线状态

#### Scenario: 入站会话视为 Hub 在线
- **WHEN** 入站连接的 `HELLO_ACK` 被接受
- **THEN** Agent 把 Hub 在线状态置为真，Local Scheduler、Condition 和结果回传与出站会话使用同一状态
- **AND** 只有最后一条已认证会话断开后才置为离线

### Requirement: 入站数据面客户端

Agent SHALL 在入站会话中提供受 mTLS、节点令牌和 `session_id` 绑定的 HTTPS 数据面，用于 Hub 拉取源文件、推送目标文件、推送 Artifact 和推送自升级二进制。所有写入 SHALL 先落临时文件并在校验大小与 SHA-256 后原子提交；目标路径 SHALL 只来自已授权的控制消息，不得由 HTTP 请求头任意指定。Agent 未实现或未声明 `inbound_data_plane` 时 SHALL 返回明确的不可用错误，不得让调用方等待到超时。

#### Scenario: Hub 拉取源文件
- **WHEN** Hub 对已授权的 transfer 调用 source 拉取接口
- **THEN** Agent 按 offset 返回文件字节并报告大小/SHA 元数据；尚未就绪时返回 409 `source_not_ready` 与重试提示
- **AND** offset 不匹配时返回 409 与当前 offset，不返回错误区间的数据

#### Scenario: Hub 推送目标文件
- **WHEN** Hub 对已授权的 transfer 推送目标文件，且大小和 SHA 校验通过
- **THEN** Agent 原子写入目标路径并上报成功
- **AND** 校验失败、磁盘不足或权限失败时删除临时文件并返回明确错误

#### Scenario: 会话失效
- **WHEN** 数据面请求携带的 `session_id` 不对应活跃入站会话
- **THEN** Agent 返回 401/403，不执行任何文件或二进制写入

#### Scenario: 数据面未实现
- **WHEN** Agent 声明了 `inbound_connection` 但未声明 `inbound_data_plane`
- **THEN** Agent 不提供数据面路由，Hub 在发起请求前以 409/BLOCKED 拒绝
- **AND** 不得以空响应或无限等待伪装成功
