## Why

NodeSteer 目前只有 Agent 主动出站连接 Hub 一种拓扑：Hub 在内网、Agent 在公网时，Agent 无法穿过内网 NAT 访问 Hub，除非额外做端口映射、反向代理或 VPN。用户要求 Hub 与 Agent 任意一方在内网、另一方在公网都能监控和操作，因此需要在不破坏现有出站模式的前提下，增加由 Hub 按节点 `endpoint` 主动拨号的入站 TLS 连接，并让文件传输、Artifact、应用部署和自升级在该模式下仍然完整可用。

## What Changes

- Agent 增加自动连接模式：配置了入站身份时，同时运行现有出站连接和入站 TLS listener（默认 `18444/tcp`）；未配置时不监听，旧行为不变。Hub 优先使用已有出站会话，无会话且节点 `endpoint` 已知时按 endpoint 拨号，避免双连接和抖动。
- **BREAKING**：添加节点 API/表单要求 `endpoint` 必填（域名或 IP，可带端口，默认 `18444`）；旧调用方缺少该字段会收到 400。`endpoint` 可在节点详情修改，仅 Administrator 可写。`connection_mode` 由系统自动判定，API/UI 均不提供手动选择。
- 新增 Hub 自建 CA、Agent 服务端证书、Hub 客户端证书、每节点入站令牌、证书 SHA-256 指纹 pin、源 IP 限制、握手限速和单连接约束。
- 协议新增 `HUB_HELLO` / `HUB_HELLO_ACK`，复用 `HelloAckPayload` 的 accepted/node/agent/revision/settings 语义，并在其后继续使用既有 `HELLO` / `HELLO_ACK` 完成注册与鉴权。`ProtocolVersion` 保持 `3`；新增能力 `inbound_connection` 和 `inbound_data_plane`。
- Hub 新增入站拨号器：按节点维护状态机、指数退避、每次尝试重新解析 DNS、全局/单节点并发上限、会话注册与所有权判定、离线判定和结构化诊断。
- 完整数据面：inbound 模式下由 Hub 通过同一 TLS 端口向 Agent 发起 HTTPS push/pull，覆盖文件传输、Artifact、应用部署和自升级。Agent 未声明 `inbound_data_plane` 或某个阶段未实现时，相关 API 返回明确 `409`，执行返回 `BLOCKED` 并带原因，不得静默超时或假装成功。
- 数据库迁移新增 `endpoint`、连接模式观测值、入站证书指纹/有效期、令牌密文、最近握手状态等列；不覆盖既有行，不影响 `hostname` 重新注册路径。
- API/UI 增加 endpoint、连接状态、测试连接、入站安装包（bundle）、数据面不可用提示、权限和审计。
- 文档更新 ARCHITECTURE、PRODUCT、DEPLOYMENT、README 和 `.env.example`，并明确四种拓扑、失败模式、SSRF/扫描防护、证书轮换边界和兼容策略。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `hub/nodes`：节点 endpoint、自动连接模式、入站状态、测试连接、bundle 与离线判定。
- `hub/api`：Agent Gateway 支持 Hub 主动拨号，新增入站握手、会话所有权与 409 错误契约。
- `hub/configuration`：Hub CA、客户端证书、入站拨号器、端口/CIDR/限速配置及默认关闭行为。
- `agent/core`：Agent 同时支持出站与入站、TLS listener、HUB_HELLO 握手、数据面客户端和 fail-closed 行为。
- `agent-file-relay`：inbound 模式下由 Hub pull 源文件、push 目标文件，支持断点与校验。
- `hub/artifacts`：inbound 模式下 Artifact 由 Hub push 到 Agent，仍禁止走控制长连接。
- `hub/applications`：inbound 模式下应用部署使用已暂存 Artifact，缺失时 BLOCKED。
- `hub/executions`：inbound 数据面不可用时返回 409/BLOCKED，不静默超时；自升级改用推送二进制。
- `hub/agent-binary`：inbound 模式下 Agent 二进制由 Hub 推送并校验，不能依赖 Agent 下载。
- `web-ui`：添加节点 endpoint 必填、连接状态/测试/bundle、数据面不可用提示，不提供手动模式选择。
- `node-enrollment`：纳管必须绑定 endpoint，区分出站命令与入站 bundle，并说明 Hub 不可达时的手动安装路径。
- `hub/access-control`：endpoint、bundle、测试连接的权限边界和审计动作。

## Impact

- 后端：`internal/protocol`、`internal/models`、`internal/hub`（gateway/session/node/artifact/application/execution/file_transfer/agentbundle）、`internal/hubserver`、`internal/hub/api`、`internal/store`（schema 迁移）、`internal/agent`（connection/listener/data plane/upgrade/file transfer/artifact）、`cmd/hub`、`cmd/agent`。
- 前端：`web/src/features/catalog`、`web/src/features/transfers`、`web/src/features/executions`、`web/src/features/overview`、`web/src/features/monitoring`、`web/src/lib/api.ts`、`web/src/locales`。
- 数据：Hub 节点表新增入站相关列；迁移只新增列，不回填、不改写既有 `hostname`。Agent 新增入站证书/令牌/配置目录。
- 部署：Agent 需开放 `18444/tcp`（仅建议对 Hub 出口放行）；Hub 需能出站访问节点 endpoint。Hub 自建 CA 私钥和客户端证书需要备份。
- 兼容性：`ProtocolVersion` 保持 `3`；旧 Agent 不声明新能力，Hub 不拨号；旧 Hub 不发送 `HUB_HELLO`，新 Agent 继续出站。新 Hub + 旧 Agent、旧 Hub + 新 Agent 均保持可用。
- 安全：新增公网入站面，必须默认关闭、强制 TLS/mTLS、每节点令牌、指纹 pin、源 IP 限制、限速和审计；CA/证书自动轮换不在本次范围。

## Non-Goals

- 不做 relay、P2P、hole punching、overlay network 或 Agent mesh。
- 不支持双方都在隔离 NAT、且没有任何可达路径的拓扑；该场景留待后续 relay。
- 不实现证书自动轮换、ACME 或外部 PKI 集成；bundle 重新签发即手动轮换。
- 不提供 `connection_mode` 手动选择，也不允许强制 inbound 覆盖已有出站会话。
- 不改变 Hub 是 Desired State 唯一权威源、Execution 幂等、Journal、fail-closed 等既有不变量。
