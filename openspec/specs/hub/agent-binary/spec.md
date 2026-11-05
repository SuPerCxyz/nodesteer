# hub/agent-binary Specification

## Purpose

以公开只读端点向目标主机提供与 Hub 构建匹配的 Agent 二进制（按架构选择、固定来源、附完整性元数据），使纳管与自升级无需外部下载源。
## Requirements
### Requirement: Serve configured Agent binaries

The Hub SHALL expose a read-only public endpoint for `amd64` and `arm64` Agent binaries. Standard Hub builds SHALL select the payload embedded in the Hub executable; unbundled local development builds MAY use fixed configured fallback paths. The endpoint SHALL reject unsupported or unavailable architectures with a clear error, and SHALL return the binary SHA256 in response metadata.

#### Scenario: Download amd64 binary

- **WHEN** a client requests the configured `amd64` Agent binary
- **THEN** the Hub returns the binary as an octet stream with its size and SHA256 metadata
- **AND** the request does not require a user session or Registration Token

#### Scenario: Download arm64 binary

- **WHEN** a client requests the configured `arm64` Agent binary
- **THEN** the Hub returns the configured arm64 binary with its size and SHA256 metadata
- **AND** the request does not require a user session or Registration Token

#### Scenario: Missing architecture binary

- **WHEN** a client requests an architecture whose configured binary is absent
- **THEN** the Hub returns a non-success response explaining that the architecture is unavailable
- **AND** the Hub does not read an arbitrary path supplied by the client

### Requirement: Package Agent payloads for Hub builds

The Hub deployment artifacts SHALL make the selected Agent binary available for the supported architecture. Local builds MAY provide only the host/x86 embedded payload; CI-produced Hub binaries and images SHALL embed both amd64 and arm64 Agent payloads and publish Hub images for `linux/amd64` and `linux/arm64`.

#### Scenario: CI multi-architecture image

- **WHEN** the release workflow publishes Hub images
- **THEN** the image manifest contains `linux/amd64` and `linux/arm64`
- **AND** each image can serve both configured Agent architectures

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
