## ADDED Requirements

### Requirement: Inbound 文件传输方向

当节点会话为 inbound 时，文件传输 SHALL 使用 Hub 发起的 HTTPS 数据面：源文件由 Hub 从源 Agent 拉取（pull），目标文件由 Hub 推送到目标 Agent（push）。当会话为 outbound 时 SHALL 继续使用既有 Agent → Hub HTTP 上传/下载路径。系统 SHALL 按会话方向选择唯一路径，不得在同一传输中混用两种方向。

#### Scenario: 源 Agent 上传
- **WHEN** inbound 源 Agent 收到 `FILE_UPLOAD_REQUEST` 且 `transport=inbound_pull`
- **THEN** Agent 校验源文件并等待 Hub 通过数据面拉取；尚未就绪时返回 409 `source_not_ready` 与重试提示
- **AND** Hub 有界重试并校验大小和 SHA-256 后进入既有 staging/交付流程

#### Scenario: 目标 Agent 接收
- **WHEN** inbound 目标 Agent 收到 `FILE_DELIVERY_REQUEST` 且 `transport=inbound_push`
- **THEN** Agent 预创建目标临时文件并等待 Hub 推送
- **AND** 校验通过后原子写入目标路径并返回既有 `FILE_DELIVERY_RESULT`

#### Scenario: 出站会话保持原路径
- **WHEN** 节点会话为 outbound
- **THEN** 文件上传和下载继续使用现有 Hub HTTP 端点
- **AND** 不要求 Agent 开放入站端口

#### Scenario: 方向切换
- **WHEN** 传输过程中会话方向发生变化
- **THEN** 当前尝试以明确错误失败或按 offset 重试
- **AND** 不得把两种方向的进度拼接成成功结果

### Requirement: Inbound 传输断点与失败

Inbound 文件传输 SHALL 支持按 offset 断点续传，并 SHALL 在 offset 冲突、大小超限、磁盘不足、权限失败或 SHA-256 不匹配时返回明确错误。临时文件 SHALL 可清理；失败不得留下可被误认为完整的目标文件。

#### Scenario: 断点续传
- **WHEN** 传输中断后重新开始，offset 等于 Agent 已接收/已发送的字节数
- **THEN** 从该 offset 继续，不重复写入或跳过字节
- **AND** 最终 SHA-256 与源文件一致

#### Scenario: Offset 冲突
- **WHEN** Hub 请求的 offset 与 Agent 当前进度不一致
- **THEN** Agent 返回 409 和当前有效 offset
- **AND** 不覆盖已有临时文件

#### Scenario: 校验失败
- **WHEN** 推送完成但大小或 SHA-256 不匹配
- **THEN** Agent 删除临时文件、保留原目标文件并返回失败
- **AND** Hub 把该目标标记为失败，不报告传输成功
