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
