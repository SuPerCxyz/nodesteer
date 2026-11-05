## ADDED Requirements

### Requirement: 纳管与重新注册保留显示名

节点纳管流程 SHALL 将 `display_name` 与 `hostname` 分离：新节点默认空显示名；重新纳管复用既有节点时 SHALL 保留原显示名；Agent 首次或后续 HELLO 上报的 hostname SHALL NOT 清空或覆盖显示名。纳管命令和 Agent 配置 SHALL NOT 包含 `display_name`。

#### Scenario: 首次纳管

- **WHEN** 管理员为全新节点生成纳管命令并完成注册
- **THEN** 节点 `display_name` 保持空
- **AND** UI 回退显示 Agent 上报的 hostname

#### Scenario: 重新纳管

- **WHEN** 管理员对已重命名的节点撤销凭证并重新纳管
- **THEN** 节点 ID/Agent ID 按既有规则复用
- **AND** `display_name` 保留，不因重新纳管清空

#### Scenario: 重新注册

- **WHEN** Agent 重启后以既有 credential 重连并上报 hostname
- **THEN** hostname 按既有语义刷新
- **AND** 显示名保持不变

#### Scenario: 纳管命令不携带显示名

- **WHEN** 管理员生成 native/docker 纳管命令
- **THEN** 命令和 Agent 配置只包含既有身份/地址字段
- **AND** 不包含 `display_name`，避免 Agent 覆盖 Hub 侧展示配置
