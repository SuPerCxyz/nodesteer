## ADDED Requirements

### Requirement: Inbound Agent 二进制交付

当自升级目标节点会话为 inbound 时，Hub SHALL 通过 Agent 数据面推送与 Hub 构建匹配的 Agent 二进制，而不是要求 Agent 访问 `GET /api/agent/binary`。推送 SHALL 携带架构、大小和 SHA-256；Agent SHALL 校验后写入固定 staging 路径。Hub SHALL NOT 让 Agent 从请求中接受任意文件路径。

#### Scenario: 推送并升级
- **WHEN** inbound native Agent 触发自升级
- **THEN** Hub 推送对应架构的二进制，Agent 校验 SHA-256 和大小后写入固定 staging
- **AND** `RUN_EXECUTION` 只携带 staged 标志和 SHA，Agent 使用该 staging 文件完成既有升级链

#### Scenario: 校验失败
- **WHEN** 推送内容大小或 SHA-256 不匹配
- **THEN** Agent 删除 staging 文件并返回失败
- **AND** Hub 把执行置为 `BLOCKED`/`FAILED`，不替换当前二进制

#### Scenario: 不支持的部署形态
- **WHEN** 目标节点不是 native 形态
- **THEN** 沿用既有限制，自升级返回 SKIPPED/BLOCKED
- **AND** 不发起二进制推送

#### Scenario: 公网 Hub 的既有下载
- **WHEN** 节点会话为 outbound 且 Hub 二进制端点可达
- **THEN** 继续使用既有下载端点和 SHA 校验
- **AND** 不要求 inbound 数据面
