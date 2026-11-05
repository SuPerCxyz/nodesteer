# hub/artifacts Specification

## Purpose
TBD - created by archiving change fix-real-test-findings. Update Purpose after archive.
## Requirements
### Requirement: Idempotent artifact deletion

The Hub SHALL treat deletion of an already absent Artifact as an idempotent success while continuing to report storage failures for existing objects.

#### Scenario: Retry deletion

- **WHEN** a client repeats DELETE for an Artifact that was already deleted
- **THEN** the Hub returns a successful response
- **AND** no Artifact record or storage file is recreated

### Requirement: Inbound Artifact 推送

当目标节点会话为 inbound 时，Hub SHALL 通过 Agent 数据面把 Artifact 推送到 Agent，而不是要求 Agent 访问 Hub 下载端点。推送 SHALL 携带 Artifact ID、大小和 SHA-256；Agent SHALL 校验后写入本地缓存。控制长连接 SHALL 只传 Prefetch 元数据和部署指令，不得传输 Artifact 二进制。

#### Scenario: 部署前推送
- **WHEN** Hub 需要向 inbound 节点预取或部署 Artifact
- **THEN** Hub 在发送部署请求前调用 Agent 数据面推送 Artifact
- **AND** 推送成功后 Agent 可从本地缓存读取该 SHA 的文件

#### Scenario: 重复推送幂等
- **WHEN** 同一 Artifact（相同 SHA）被重复推送
- **THEN** Agent 复用已有缓存，不重复写入或覆盖为不同内容
- **AND** 返回成功，不产生重复缓存项

#### Scenario: 推送失败
- **WHEN** 推送超时、SHA 不匹配或 Agent 无 `inbound_data_plane`
- **THEN** Hub 不发送依赖该 Artifact 的部署请求，并返回 409/BLOCKED 及原因
- **AND** 不得让部署在缺少 Artifact 的情况下继续并静默失败

#### Scenario: 控制通道边界
- **WHEN** Hub 向 Agent 发送 Prefetch 或部署指令
- **THEN** WebSocket 消息只包含 Artifact 元数据、SHA 和传输方式
- **AND** 不包含 Artifact 文件内容

