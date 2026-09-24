# node-enrollment Specification

## Purpose
Give administrators a single Nodes-page workflow for enrolling Native and Docker Agents into an existing Hub using the real registration protocol and copyable commands.
## Requirements
### Requirement: Show enrollment methods

The Nodes page SHALL provide an administrator-only Add Node workflow with required node name and node address fields accepting an IPv4/IPv6 address or DNS hostname, an editable Hub address defaulted to the current web page origin, and Native, `docker run`, and standalone `docker compose` methods. A successful generation SHALL persist one pending offline node record before returning the enrollment metadata. The Web UI SHALL place “Generate install command” on the left and the Copy action on the right of the same action row after commands are generated, and Copy SHALL copy the complete exact text for the selected method on secure and ordinary HTTP origins without placing credentials in persistent browser storage.

#### Scenario: Open Add Node

- **WHEN** an administrator opens Add Node
- **THEN** the page shows required node name and node address inputs
- **AND** the Hub address is prefilled from the current page origin
- **AND** the three enrollment methods are available

#### Scenario: Generate runtime-architecture enrollment commands

- **WHEN** the administrator submits a valid node name, node address, and Hub address
- **THEN** the Hub creates and persists one pending offline node record
- **AND** returns commands containing the node identity, preassigned Agent ID, registration token, supplied address unchanged, and an Agent Gateway URL derived from the supplied Hub address and configured Gateway port
- **AND** the Native command detects the target Linux architecture, downloads and SHA256-verifies the matching Agent binary from the Hub
- **AND** the Docker and Compose content uses the multi-architecture Agent image
- **AND** the Web UI refreshes the node list so the pending record is visible

#### Scenario: Reject invalid node identity

- **WHEN** the administrator submits an empty/invalid node name or an invalid node address
- **THEN** the Hub rejects the request without generating commands or creating a node record

#### Scenario: Generate failure

- **WHEN** node persistence fails while generating enrollment metadata
- **THEN** the Hub returns an error and the Web UI does not report generation as successful

#### Scenario: Copy enrollment command

- **WHEN** the administrator selects Native, `docker run`, or `docker compose` and clicks Copy
- **THEN** the complete command or Compose document returned for that method is copied without truncation

#### Scenario: Clipboard API unavailable

- **WHEN** the browser does not expose or rejects `navigator.clipboard.writeText`
- **THEN** the UI uses a synchronous browser-native fallback and reports an error only if both copy paths fail

### Requirement: Use the existing registration flow

Enrollment instructions SHALL configure the existing Agent binary/container with the Hub URL, registration token, preassigned Agent ID, node name, and node address, preserve persistent Agent state, and bind the first registration to the pending node record. Native instructions SHALL remain executable on a clean Linux host after the Agent binary and existing service unit are obtained from the generated content.

#### Scenario: Native enrollment detects host architecture

- **WHEN** an administrator runs the Native instructions on a clean Linux host
- **THEN** the command detects `x86_64`/`amd64` or `aarch64`/`arm64` and downloads the matching binary from Hub without requiring a download token
- **AND** verifies the binary before installation
- **AND** starts the Agent with the generated configuration so the pending node becomes the connected real node

#### Scenario: Docker enrollment uses the multi-architecture image

- **WHEN** an administrator runs Docker or Compose enrollment on a supported host
- **THEN** the generated content uses the multi-architecture Agent image
- **AND** preserves the `/var/lib/nodesteer` persistent volume

#### Scenario: Agent reports configured identity

- **WHEN** an Agent starts with generated enrollment configuration
- **THEN** its first HELLO reports the configured node name and node address, falling back to local discovery only when those values are absent

#### Scenario: First Agent connection

- **WHEN** an Agent starts with generated enrollment configuration and sends its first HELLO
- **THEN** the Hub updates the matching pending node record with the real Agent metadata and marks it online
- **AND** no duplicate node record is created

#### Scenario: Native enrollment

- **WHEN** an administrator runs the Native instructions on a Linux host
- **THEN** the Agent starts with the generated configuration, sends HELLO, and the pending node becomes the connected real node

#### Scenario: Docker enrollment

- **WHEN** an administrator runs the Docker or Compose instructions against an existing Hub
- **THEN** the container uses a persistent `/var/lib/nodesteer` volume, connects to the configured Gateway, and binds to the pending node record

#### Scenario: Docker enrollment image

- **WHEN** the administrator selects Docker or Compose enrollment
- **THEN** the generated content uses `ghcr.io/supercxyz/nodesteer-agent:latest`
- **AND** preserves the `/var/lib/nodesteer` persistent volume

### Requirement: Protect enrollment metadata

The enrollment metadata endpoint SHALL require administrator authorization and SHALL return a clear error when the Hub Gateway URL is not configured or derivable.

#### Scenario: Unauthorized enrollment metadata

- **WHEN** a Viewer, unauthenticated caller, or invalid session requests enrollment metadata
- **THEN** the Hub returns an authorization error and does not expose the registration token

### Requirement: Agent 自动检测并上报运行参数

Agent SHALL 在启动时自动生成运行参数而非依赖配置硬编码：`agent_version` 来自编译期注入的真实构建版本；`deployment_mode` 在未显式配置时自动探测（容器环境为 docker，否则 native，显式配置优先）；`host_integration` 由部署模式推导。纳管命令与生成的配置模板 SHALL NOT 硬编码这些参数（既有配置文件保持向后兼容）。Agent SHALL 通过 HELLO 上报真实值；Hub SHALL 对上报的 `deployment_mode` 做枚举白名单校验，非法值拒绝或归一化。`data_dir` 为本地路径，SHALL NOT 上报。

#### Scenario: 版本来自编译注入

- **WHEN** CI 或本地以 ldflags 构建 Agent 二进制并启动
- **THEN** `agent_version` 为编译注入的真实版本值，HELLO 上报该值
- **AND** 未注入时回退到明确的未知/开发默认值而非伪造版本

#### Scenario: 部署模式自动探测

- **WHEN** Agent 启动且未显式配置 `deployment_mode`
- **THEN** 运行于容器环境时探测为 docker，裸机运行时为 native
- **AND** 显式配置或环境变量始终优先于探测结果

#### Scenario: 纳管模板去硬编码

- **WHEN** 管理员生成纳管命令
- **THEN** 生成的配置不再写入 `deployment_mode`/`host_integration`/`agent_version` 硬编码键，由 Agent 启动时自动生成
- **AND** 既有旧配置文件仍可正常解析

#### Scenario: Hub 校验上报模式

- **WHEN** HELLO 上报的 `deployment_mode` 不在合法枚举内
- **THEN** Hub 拒绝或归一化该值，不将非法值写入节点记录
