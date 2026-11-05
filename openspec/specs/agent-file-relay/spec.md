# agent-file-relay Specification

## Purpose
Provide a durable, authenticated Hub-mediated way to move a regular file from one NodeSteer Agent to selected target Agents without requiring direct Agent-to-Agent connectivity.
## Requirements
### Requirement: Create a file transfer

The system SHALL allow an authorized administrator to create a transfer specifying one source Agent, one absolute source path, and one or more target Agent/destination-path pairs.

#### Scenario: Valid transfer creation

- **WHEN** an administrator submits a valid source Agent, source path, and target list
- **THEN** the Hub creates a durable transfer with independently pending target records and begins source upload when the source Agent is available

#### Scenario: Invalid target list

- **WHEN** a request has no targets, duplicate target entries, a missing destination path, or an unknown Agent
- **THEN** the Hub rejects it without creating a transfer

### Requirement: Upload through Hub

The source Agent SHALL upload file bytes to an authenticated Hub data endpoint, and the Hub SHALL bind the upload to the transfer's configured source Agent.

#### Scenario: Authorized source upload

- **WHEN** the configured source Agent uploads the requested file
- **THEN** the Hub accepts only the expected transfer bytes, persists progress, and reports the durable offset or staged result

#### Scenario: Wrong Agent upload

- **WHEN** another Agent or invalid credential attempts the upload
- **THEN** the Hub rejects the request and leaves the transfer unchanged

### Requirement: Verify and stage the source file

The Hub SHALL verify the final byte count and SHA256 before making a file available for delivery.

#### Scenario: Verification succeeds

- **WHEN** the uploaded file reaches the declared size and its SHA256 is computed successfully
- **THEN** the Hub atomically promotes the temporary blob to staged state and starts target delivery

#### Scenario: Verification fails

- **WHEN** the upload is truncated, oversized, or otherwise fails verification
- **THEN** the Hub marks the transfer failed, removes the incomplete staged blob, and sends no delivery request

### Requirement: Deliver to selected target Agents

The Hub SHALL deliver the verified staged file only to the selected target Agents, and each target SHALL verify the file before atomically replacing its destination path.

#### Scenario: Multi-target delivery

- **WHEN** two or more target Agents are selected and the staged file is valid
- **THEN** each target receives an independent delivery request and successful targets do not wait for or mask another target's failure

#### Scenario: Target is offline

- **WHEN** a target Agent is offline when the staged file is ready
- **THEN** its target record remains pending and delivery is retried after that Agent reconnects

#### Scenario: Destination verification fails

- **WHEN** a target download has a size or SHA256 mismatch
- **THEN** the target leaves the existing destination unchanged, reports failure, and removes its temporary file

### Requirement: Transfer lifecycle controls

The system SHALL expose transfer status and allow an administrator to retry failed work or cancel an active transfer.

#### Scenario: Retry failed target

- **WHEN** an administrator retries a failed target
- **THEN** only that target returns to pending and a new delivery attempt is made when the target is available

#### Scenario: Cancel active transfer

- **WHEN** an administrator cancels an active transfer
- **THEN** the Hub marks pending work canceled, asks active Agents to stop, and no later reconnect resumes the canceled work

### Requirement: Recover transfer state

The system SHALL preserve transfer and target state across Hub restart and Agent reconnect.

#### Scenario: Hub restart during upload or delivery

- **WHEN** the Hub restarts with a partial upload or pending target
- **THEN** the transfer resumes from durable state or remains visibly failed/pending without exposing a partial destination file

### Requirement: Enforce file-transfer security

The system SHALL authenticate source and target Agents, enforce configured source and destination path policy, reject non-regular files, cap transfer size, and record audit events for create, retry, cancel, success, and failure.

#### Scenario: Path or size policy violation

- **WHEN** a source or destination path is outside policy, is not a regular file, or exceeds the configured limit
- **THEN** the operation is rejected before bytes are committed

### Requirement: Host 文件映射必须对应真实宿主
Host Integration 文件操作 SHALL 在创建目录或写入前证明映射对应请求的真实宿主路径；缺失、错误、只读或无法确认的映射 SHALL 明确失败，保留 allowlist、symlink 与越界限制。

#### Scenario: 错误与正确 bind
- **WHEN** Relay 的目标挂载缺失或绑定错误目录
- **THEN** 传输失败且真实宿主、错误宿主目录和容器覆盖层均无本次写入；正确映射时真实宿主文件 SHA 与结果一致

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

