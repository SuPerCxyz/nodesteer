## 1. 基线与设计确认

- [x] 1.1 复核 `docs/PRODUCT.md` §7/§8/§17/§33/§34.1/§38/§44/§49 与 `docs/ARCHITECTURE.md` §15/§16/§58/§59/§62/§67，列出受影响的强制约束和需要改写的措辞。
- [x] 1.2 确认 `ProtocolVersion=3` 不变，新增 `HUB_HELLO`/`HUB_HELLO_ACK` 只在入站连接使用；为消息字段与 `HelloAckPayload` 嵌入扩展补协议表驱动测试设计。
- [x] 1.3 确认能力常量 `inbound_connection`、`inbound_data_plane` 的声明条件和 Hub/Agent 门禁矩阵。
- [x] 1.4 与 `add-node-display-name` 对齐迁移版本：本 change 预留 v27，Change 2 使用 v26；确认两迁移独立、可乱序应用。
- [x] 1.5 明确本 change 的非目标：不做 relay/P2P、不自动轮换证书、不支持双方隔离 NAT、不提供手动模式选择。

## 2. 协议与数据模型

- [x] 2.1 在 `internal/protocol` 增加 `HUB_HELLO`、`HUB_HELLO_ACK` 常量与载荷类型，复用 `HelloAckPayload` 语义并嵌入 hub/agent 版本、nonce、时间戳、令牌、数据面能力和 `session_id`。
- [x] 2.2 为 `ARTIFACT_PREFETCH`、`FILE_UPLOAD_REQUEST`、`FILE_DELIVERY_REQUEST`、`RUN_EXECUTION` 增加向后兼容的 `transport`/`artifact_transport`/`agent_binary_staged` 字段；旧 Agent 缺省值保持原语义。
- [x] 2.3 在 `internal/models` 增加 `endpoint`、`connection_mode`、`last_connection_mode`、入站证书指纹/序列号/有效期、令牌密文、最近握手/错误、endpoint 更新者/时间等字段；确保 JSON 输出不包含令牌。
- [x] 2.4 增加稳定错误码：`inbound_data_plane_unavailable`、`inbound_stage_unavailable`、`inbound_agent_unsupported`、`inbound_endpoint_invalid`、`inbound_pin_mismatch`、`inbound_certificate_expired`、`inbound_auth_failed`。
- [x] 2.5 补充协议兼容测试：旧 `HELLO`/`HEARTBEAT`/`SYNC_*` 帧不受新字段影响；未知消息仍按既有行为处理。

## 3. 数据库迁移 v27

- [x] 3.1 新增迁移 `v27_agent_inbound_connection`，只新增列并设置安全默认值；不删除、不回填、不修改既有 `hostname`。
- [x] 3.2 从 `UpsertNode` 的 `ON CONFLICT` 更新列表排除 `endpoint`、`connection_mode`、入站身份和诊断列；新增专用更新方法。
- [x] 3.3 改造 `scanNode`/`ListNodes`/`GetNode`/`GetNodeByAgentID` 读取新列，并保证旧库/旧行默认值可用。
- [x] 3.4 迁移测试：全新库、v25 旧库、v26 已应用 v27 未应用、v27 已应用后补 v26、重复启动幂等。
- [x] 3.5 注册路径测试：重新注册覆盖 hostname 但保留 endpoint/指纹/令牌密文；`PrepareEnrollment` 不清空入站配置。

## 4. Hub PKI、配置与 bundle

- [x] 4.1 在 `cmd/hub`/`internal/hubserver` 增加 CA、Hub 客户端证书/私钥、拨号开关、并发、超时、退避、允许端口和 CIDR 拒绝配置；默认无 CA 时禁用并明确日志。
- [x] 4.2 实现 CA 加载/首次自建与 0600 权限检查；CA 私钥不入库、不进日志；启动时校验证书/私钥匹配和有效期。
- [x] 4.3 实现节点证书签发（DNS/IP SAN、EKU serverAuth、SHA-256 指纹）、每节点 32 字节令牌生成与 AES-256-GCM 密文存储。
- [x] 4.4 实现 bundle 生成（`ca.pem`、服务端证书/私钥、Hub 客户端指纹、令牌、`agent-inbound.yaml`、安装脚本、README），压缩包权限和 `Cache-Control: no-store`。
- [x] 4.5 实现 bundle 下载与轮换语义：首次生成、再次需显式轮换、旧身份立即失效、审计不含秘密。
- [x] 4.6 补充配置解析/校验测试：缺项、路径不可读、证书/私钥不匹配、非法 CIDR/端口、默认关闭。

## 5. Hub 入站拨号器

- [x] 5.1 实现 `InboundDialer` 与每节点状态机（DISABLED/IDLE/DIALING/BACKOFF/HUB_HELLO/HELLO_WAIT/CONNECTED/GRACE），启动/停止与 Hub 生命周期绑定。
- [x] 5.2 实现触发条件：Hub 启动、节点创建/endpoint 更新、bundle 签发/轮换、能力刷新、会话断开宽限期、退避到期、手动测试；`disabled` 不拨号；pending 节点允许首次引导拨号。
- [x] 5.3 实现 DNS 重新解析、CIDR/端口策略、TCP/TLS/HUB_HELLO/HELLO 分阶段超时、并发上限和 2s→60s 退避 + jitter。
- [x] 5.4 实现 TLS 客户端：强制校验服务端证书链、SAN、EKU 和节点证书指纹 pin；禁止 `InsecureSkipVerify` 和明文回退。
- [x] 5.5 实现 `HUB_HELLO` 发送与 `HUB_HELLO_ACK`/`HELLO` 处理，复用既有 `handleHello` 注册、credential/token 校验、Revision/Settings 下发。
- [x] 5.6 扩展 `SessionManager` 支持 origin/epoch 注册策略：出站优先、入站仅在无出站会话时注册、重复连接拒绝、迟到消息按 epoch 丢弃。
- [x] 5.7 实现拨号诊断字段与 metrics；拨号失败不误置 offline，HELLO 接受后才置 online。
- [x] 5.8 单元测试状态机、退避、并发、DNS 重解析、pin 失败、重复连接、Hub 重启、Agent 重启，以及 pending 新节点在 capability 未知时的首次入站引导。

## 6. Agent 入站 listener

- [x] 6.1 在 `internal/agent` 增加入站配置（listen addr、证书/私钥、CA、Hub 客户端指纹、令牌、来源 CIDR、限速），无 bundle 时不监听。
- [x] 6.2 实现 TLS listener：最低 TLS 1.2、RequireAndVerifyClientCert、EKU clientAuth、Hub 客户端证书指纹 pin、握手超时。
- [x] 6.3 实现 `HUB_HELLO` 校验（时间偏差、nonce 重放、令牌 HMAC proof 常量时间校验、节点身份）与 `HUB_HELLO_ACK`。
- [x] 6.4 在同一连接上复用既有 `HELLO`/`HELLO_ACK` 发送与接收逻辑；改造连接管理器支持“拨号客户端”和“被拨号服务端”两种传输封装，并统一 `HubOnline/HandshakeComplete` 状态供 Local Scheduler/Condition 使用。
- [x] 6.5 实现单连接与所有权：出站成功关闭入站、入站存活暂停出站重试、重复握手拒绝、断线恢复。
- [x] 6.6 实现握手/数据面限速（每 IP、全局、并发）、请求大小限制、来源 CIDR 拒绝和结构化失败日志。
- [x] 6.7 测试：mTLS、pin、令牌、nonce/时钟、超时、重复连接、来源限制、无 bundle 不监听、listener 配置错误 fail fast。

## 7. Inbound 数据面

- [x] 7.1 在 Agent TLS listener 上增加 `/agent-data/v1/*` 路由和统一认证中间件（mTLS + Bearer 令牌 + session_id + node_id）。
- [x] 7.2 实现 `GET /transfers/{id}/source`：offset、大小、SHA 元数据、范围校验和流式读取。
- [x] 7.3 实现 `PUT /transfers/{id}/target`：仅接受已授权 destination，临时文件、offset、大小/SHA 校验、原子 rename、mode 设置。
- [x] 7.4 实现 `PUT /artifacts/{id}`：SHA 缓存、幂等、内容冲突拒绝、临时文件清理、缓存 GC 兼容。
- [x] 7.5 实现 `PUT /agent-binary`：架构/大小/SHA 校验、固定 staging 路径、可执行权限、失败清理。
- [x] 7.6 实现 409 offset、413 超限、422 校验失败、429 限速、507 磁盘不足等错误码和 JSON 响应。
- [x] 7.7 实现会话失效、方向切换、数据面未实现时的明确失败，不静默超时。
- [x] 7.8 测试数据面认证、路径策略、offset、SHA、磁盘不足、超大 body、会话绑定和并发写入。

## 8. Hub 数据面编排与功能接入

- [x] 8.1 Hub 文件传输管理器按会话 origin 选择 outbound 既有路径或 inbound pull/push；传输状态机、epoch、停滞超时和取消语义保持一致。
- [x] 8.2 源上传：发送 `transport=inbound_pull` 请求后由 Hub 拉取、写入 staging、校验 SHA，并驱动单一终态。
- [x] 8.3 目标投递：发送 `transport=inbound_push` 请求后由 Hub 推送、按 offset 重试、校验结果并更新 transfer target。
- [x] 8.4 Artifact：部署/预取前推送，失败时 409/BLOCKED；控制通道只传元数据；更新 `Prefetch` 和同步载荷的 transport 字段。
- [x] 8.5 应用部署：inbound 请求置空 `artifact_url`、设置 `artifact_transport`，Artifact 缺失时 Agent 返回 `BLOCKED artifact_not_staged`。
- [x] 8.6 自升级：inbound 先推送二进制并校验，再发送带 staged 标志的 `RUN_EXECUTION`；失败不替换二进制，重启后按 Journal 对账。
- [x] 8.7 所有入口在创建/下发前执行能力与阶段门禁，返回稳定 409 code；执行记录写 `BLOCKED` + `block_reason`。
- [x] 8.8 集成测试：文件上传/投递/续传/取消/失败、Artifact 推送/冲突、部署成功/回滚/缺失、自升级成功/失败/Docker 拒绝、方向切换。

## 9. API、权限与审计

- [x] 9.1 扩展 `GET /api/nodes`、`GET /api/nodes/{id}` 返回 endpoint、当前/上次连接方式、入站能力和证书诊断；确保不返回令牌/私钥。
- [x] 9.2 扩展 `POST /api/nodes/enrollment`：endpoint 必填、规范化、默认端口、非法输入 400；响应提供 bundle 可用性。
- [x] 9.3 扩展 `POST/PUT /api/nodes/{id}`：endpoint 仅 Administrator；`connection_mode` 不可写；endpoint 变更将 `inbound_provisioned` 置 0、标记 `inbound_bundle_stale` 并暂停拨号，提示重新签发；校验失败不产生部分更新。
- [x] 9.4 实现 `POST /api/nodes/{id}/connection-test`：分阶段结果、10s 级超时、每节点 1 次/10s、已有会话时不重复拨号、不注册会话。
- [x] 9.5 实现 `GET /api/nodes/{id}/inbound-bundle` 与 `POST /api/nodes/{id}/inbound-bundle/rotate`，仅 Administrator、no-store、审计。
- [x] 9.6 实现审计动作 `node.endpoint_update`、`node.inbound_bundle_issue`、`node.inbound_bundle_rotate`、`node.connection_test`；确认审计/日志/API 无秘密。
- [x] 9.7 RBAC 测试：Administrator/Operator/Viewer 的 endpoint、bundle、test、查看权限矩阵；匿名请求 401。

## 10. Web UI

- [x] 10.1 添加节点对话框增加必填 endpoint、默认端口提示、格式校验和错误展示；提交体包含 endpoint。
- [x] 10.2 增加“入站安装包”入口与适用拓扑说明；区分出站命令与 bundle 手动安装，不误导私网 Hub 场景。
- [x] 10.3 节点列表增加连接方式/endpoint 展示（截断/提示），不提供模式下拉框。
- [x] 10.4 节点详情增加 endpoint、当前/上次连接方式、入站能力、证书指纹/有效期、最近握手/错误；Administrator 显示测试/下载/轮换操作。
- [x] 10.5 文件传输、Artifact、部署、升级入口在入站数据面不可用时显示 banner、禁用提交并映射 409/BLOCKED 原因。
- [x] 10.6 出站正常时入站状态显示“待命/未使用”，不误报故障；证书过期/pin 错误显示明确警告。
- [x] 10.7 前端测试：表单必填、默认端口、列表/详情、权限可见性、测试结果、bundle 轮换确认、数据面错误提示。
- [x] 10.8 Web E2E：完成添加节点、查看状态、测试连接、下载 bundle（模拟）、触发 409/BLOCKED 的完整路径并截图。

## 11. 安全与故障验证

- [x] 11.1 SSRF/扫描测试：metadata、loopback、link-local、multicast、非允许端口、DNS 重绑定、混合解析结果。
- [x] 11.2 认证测试：错误/缺失/过期证书、pin 不匹配、令牌错误、nonce 重放、时间偏差、暴力握手限速。
- [x] 11.3 数据面安全：任意路径写入、超大 body、会话失效、错误 offset、SHA 冲突、磁盘不足、部分文件残留。
- [x] 11.4 故障注入：Hub/Agent 重启、网络中断、半开连接、证书过期、数据库迁移失败回滚、端口未开放。
- [x] 11.5 兼容矩阵实测：旧 Hub + 新 Agent、新 Hub + 旧 Agent、新 Hub + 新 Agent 的四种拓扑；记录未验证项。
- [x] 11.6 确认所有失败路径都有明确 409/BLOCKED/FAILED 原因，不存在无限等待或静默成功。

## 12. 文档与收尾

- [x] 12.1 更新 `docs/ARCHITECTURE.md` §15/§16/§58/§59/§62/§67，明确双向连接、数据面方向和不变量 2 的新措辞。
- [x] 12.2 更新 `docs/PRODUCT.md` §7/§8/§17/§33/§34.1/§38/§44/§49，说明自动模式、endpoint 必填、非目标和拓扑限制。
- [x] 12.3 更新 `docs/DEPLOYMENT.md`：CA 生成/备份、bundle 安装、18444 防火墙、Docker 端口映射、私网 Hub 手动安装、回滚。
- [x] 12.4 更新 `README.md`、`.env.example`、`docker-compose.yml` 的入站配置和端口示例。
- [x] 12.5 增加 `docs/INBOUND_CONNECTION.md` runbook（可选）：证书轮换、故障排查、日志字段、回滚。
- [x] 12.6 最终回归：后端 `make test`/`make lint`、相关包 `-race`、前端 `npm test`/`npm run lint`/`npm run build`；记录命令、退出码和未执行项。
- [x] 12.7 复核 tasks/spec/design 与实现一致；未实现或未验证项保持未勾，不归档。
