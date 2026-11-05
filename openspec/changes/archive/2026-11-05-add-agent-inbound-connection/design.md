## Context

当前拓扑只有一条路径：Agent 主动向 Hub Agent Gateway 建立 WebSocket，Hub 不拨号 Agent，Agent 不监听管理端口。该模型覆盖 Hub 公网 / Agent 内网、双方公网、双方同内网；但不覆盖 Hub 在内网、Agent 在公网的场景，因为公网 Agent 无法访问没有公网入口的 Hub。用户要求在没有 relay、没有 VPN 的前提下，让任意一方处于内网时仍可监控和操作，并接受公网 Agent 开放一个入站 TLS 端口。

本 change 增加第二条传输路径，但不改变控制面语义：Hub 仍是 Desired State 唯一权威源，Execution ID/Key、Journal、Revision、fail-closed 全部沿用。入站连接只是 Hub 与 Agent 之间的另一种会话承载方式；谁发起 TCP 连接不改变谁拥有权威状态。

**关键约束**

- 保持 `ProtocolVersion = 3`，不要求旧 Hub/旧 Agent 协同升级。
- 新能力通过 capability 声明；旧 Agent 在 HELLO 中确认不支持后，Hub 停止对该节点的入站拨号。
- `hostname` 会在重新注册路径被覆盖，不能用作显示名；本 change 的 endpoint/连接字段与 Change 2 的 `display_name` 分离。
- 公网入站面默认关闭；没有完整 CA/证书/令牌配置时不得监听。
- 大文件、Artifact、Agent 二进制不得走控制长连接（架构 §67 不变量 18）。

## Goals / Non-Goals

**Goals**

- Agent 与 Hub 任意一方在公网、另一方在内网时，都能建立一条经过认证的控制会话。
- inbound 模式下文件传输、Artifact、应用部署、自升级全部可用，且失败有明确 409/BLOCKED 原因。
- 自动选择连接方向，不提供页面手动模式；避免出站/入站双连接和反复切换。
- 入站面具备可验证的安全边界：mTLS、每节点令牌、证书指纹 pin、源 IP 限制、限速、单连接、审计。
- 旧 Agent/旧 Hub 行为不变，升级顺序不受严格限制。

**Non-Goals**

- 不做 relay/P2P/hole punching/overlay；双方隔离 NAT 仍不支持。
- 不做证书自动轮换或 ACME；bundle 重新签发即人工轮换。
- 不实现 Hub HA/多 Hub；本设计假设单 Hub 实例持有一套 CA 与客户端证书。
- 不改变 Task/Schedule/Execution 的既有职责，不把入站连接变成新的调度器。

## 1. 拓扑与连接方向

| # | Hub 位置 | Agent 位置 | 首选路径 | 说明 |
|---|---|---|---|---|
| 1 | 公网 | 内网 | Agent → Hub 出站 | 现有模式，保持首选；Agent 无需开放端口 |
| 2 | 内网 | 公网 | Hub → Agent 入站 | 本 change 的核心场景；Agent 开放 18444，Hub 出站拨号 |
| 3 | 公网 | 公网 | Agent → Hub 出站 | 出站优先；入站仅作出站持续失败时的回退 |
| 4 | 内网 | 同一内网 | Agent → Hub 出站 | 网络已互通，沿用现有模式 |
| 5 | 隔离内网 A | 隔离内网 B | 不支持 | 无任何可达路径，留待 relay |

补充规则：

- 入站 listener 只有安装了 bundle（CA、服务端证书/私钥、Hub 客户端证书指纹、节点令牌）才启用；没有 bundle 时 Agent 不监听，行为与旧版本一致。
- Hub 只有同时满足以下条件才拨号：节点存在、`endpoint` 非空、入站 bundle 已签发（`inbound_provisioned=1`）、节点不是 `disabled`、当前没有活跃会话、且不在断开后的宽限期内。首次引导不要求先收到过 HELLO：pending 节点在 bundle 签发后即可被拨号；一旦 Agent 在 HELLO 中明确未声明 `inbound_connection`，Hub 记录该节点不支持入站并停止后续拨号。
- 该引导规则解决“Hub 私网 + 全新公网 Agent”场景：Agent 无法先出站注册，Hub 必须能在 capability 未知时按 endpoint 发起首次入站握手。
- Hub 不因“节点离线”而无限扫描；拨号目标只来自节点 `endpoint`，不允许 API 传入任意地址。
- Agent 出站失败不能自动改为“不监听”；入站 listener 与出站循环相互独立，但同一时刻只保留一个已认证会话。

## 2. 自动连接与所有权状态机

### 2.1 Agent 侧

Agent 启动后并行运行：

```text
outbound loop:  IDLE -> DIALING -> HELLO_WAIT -> CONNECTED -> BACKOFF -> DIALING
inbound listener: STOPPED -> LISTENING -> TLS_HANDSHAKE -> HUB_HELLO_WAIT
                  -> HELLO_WAIT -> CONNECTED -> LISTENING
```

状态约束：

- `CONNECTED` 表示收到 accepted 的 `HELLO_ACK`，不是 TCP/TLS 成功。
- Agent 只维护一个对业务可见的 Hub 在线状态：任一出站或入站会话 accepted 后置为已连接，最后一条已认证会话断开后置为未连接。Local Scheduler 的 `hub_online` 判定、Condition、日志上报和 Execution 结果回传不得按传输方向分支。
- Agent 同一节点最多一个 `CONNECTED` 会话。出站握手成功时，Agent 关闭当前入站会话；入站握手成功时，如果出站正在连接则允许其完成，出站成功后再关闭入站。
- 入站会话存活期间暂停出站重试，避免 Hub 内网不可达时每 2–4 秒刷失败；入站断开后立即恢复出站尝试，并带 0–2 秒 jitter。
- 没有入站 bundle 时 listener 不启动；bundle 存在但证书/私钥/令牌读取失败时，listener 启动失败并记录明确错误，不静默降级为明文或跳过配置。
- 入站 listener 只接受 `HUB_HELLO`；收到其他首帧、旧 Hub 的 `HELLO` 或非法 JSON 时直接关闭并计数。

### 2.2 Hub 侧拨号器

每个节点一个独立状态机，节点之间并行：

```text
DISABLED  --endpoint/bundle/CA 就绪-->      IDLE
IDLE      --无会话且过宽限期-->             DIALING
DIALING   --TCP/TLS 失败-->                 BACKOFF
DIALING   --TLS 成功-->                     HUB_HELLO
HUB_HELLO --HUB_HELLO_ACK 失败-->            BACKOFF
HUB_HELLO --收到 HELLO-->                    HELLO_WAIT
HELLO_WAIT--HELLO_ACK accepted-->            CONNECTED
CONNECTED --会话消失-->                      GRACE -> IDLE
BACKOFF   --退避到期/endpoint 更新-->         DIALING
```

- 退避：2s 起，指数增长到 60s，叠加 ±20% jitter；连续失败 10 次后保持 60s，不无限增长，避免网络恢复后长时间不重连。
- 每次尝试重新解析 DNS；只对通过 CIDR 策略的解析结果拨号。DNS 解析失败、TCP 拒绝、TCP 超时、TLS 证书错误、pin 不匹配、令牌错误、协议错误分别记录为不同 `last_error`。
- 全局并发上限默认 16，单节点同时最多 1 个拨号；握手总时限 20s（TCP 5s、TLS 5s、HUB_HELLO 5s、HELLO 5s），超时即取消。
- 宽限期默认 10s：会话断开后 10s 内不拨号，给 Agent 出站重连优先机会。宽限期内 Agent 出站成功则继续使用出站；宽限期后仍无会话才拨号。
- 拨号成功只注册会话，不直接把节点置 online；`HELLO_ACK accepted` 后才置 online，继续沿用既有在线状态机。
- 拨号失败不把已有会话的节点置 offline；没有会话时节点保持原状态（pending/offline），只更新诊断字段。
- `disabled` 节点不拨号；`maintenance` 节点允许建立会话（便于观察与恢复），但任务下发仍受既有暂停语义约束。
- 会话注册采用 `origin` 与 `epoch`：出站 origin 优先；入站注册时若已有出站会话则拒绝并关闭；入站会话被新的出站会话替换时，旧会话的迟到消息按 epoch 丢弃。
- Hub 重启后内存会话丢失，拨号器与 Agent 出站同时恢复；谁先完成握手谁先上线，随后出站优先接管。该过程最多发生一次切换，不形成循环。

### 2.3 所有权矩阵

| 当前会话 | 新事件 | 结果 |
|---|---|---|
| 无 | 出站 HELLO accepted | 注册出站会话 |
| 无 | 入站 HELLO accepted | 注册入站会话 |
| 出站 | 入站握手成功 | 拒绝入站，关闭连接，记录 duplicate |
| 入站 | 出站 HELLO accepted | 注册出站，关闭入站 |
| 入站 | 新入站握手 | 拒绝新连接，保留旧连接 |
| 任意 | 心跳超时 | 关闭并置 offline（maintenance/disabled 保持原状态） |

## 3. 协议与握手

`ProtocolVersion` 保持 `3`。新增消息只在入站连接上使用，旧 Hub/旧 Agent 不会收到：

```text
HUB_HELLO      Hub -> Agent
HUB_HELLO_ACK  Agent -> Hub
HELLO          Agent -> Hub（既有）
HELLO_ACK      Hub -> Agent（既有）
```

### 3.1 消息载荷

复用 `HelloAckPayload` 的 `Accepted`、`NodeID`、`AgentID`、`DesiredGlobalRev`、`Settings`、`Message`、`NodeStatus` 字段；通过嵌入扩展必要信息，不改变既有 `HELLO_ACK` 的 JSON 兼容性：

```text
HubHelloPayload
  HelloAckPayload        # accepted=true, node_id/agent_id 目标身份, desired_global_rev, settings, message
  hub_id                 # Hub 实例标识
  hub_version
  nonce                  # 16 字节随机值，base64url
  issued_at              # UTC
  data_plane             # Hub 侧数据面能力声明

HubHelloAckPayload
  HelloAckPayload        # accepted=true, node_id, agent_id, message, node_status
  nonce                  # 回显 Hub nonce
  agent_nonce            # Agent 生成的 16 字节随机值
  token_proof            # HMAC-SHA256(node_token, "nodesteer-inbound-v1" | hub_nonce | agent_nonce | node_id)
  data_plane             # Agent 是否具备 inbound_data_plane
  session_id             # 本次入站会话标识，供数据面绑定
```

握手顺序：

1. TCP 连接建立，TLS 1.2+（优先 1.3）完成 mTLS。
2. Hub 校验证书链、EKU、节点证书指纹 pin；Agent 校验证书链、EKU、Hub 客户端证书指纹 pin。
3. Hub 发送 `HUB_HELLO`，携带目标 node_id/agent_id、nonce、时间戳和能力；不发送节点令牌。
4. Agent 校验目标 node_id/agent_id 与本地身份一致、时间偏差（±60s）和 nonce 未重放；用 bundle 中的节点令牌计算 HMAC proof，返回 `HUB_HELLO_ACK`，回显 nonce、给出 `session_id` 和 `token_proof`。Hub 解密自己保存的节点令牌并常量时间校验 proof，失败即关闭。令牌本身不在连接上传输。
5. Agent 在**同一连接**上发送既有 `HELLO`，携带 `ProtocolVersion=3`、AgentID、注册令牌或 credential、能力集。
6. Hub 复用既有 `handleHello` 逻辑完成协议版本、credential/registration token、节点身份校验，注册会话并返回 `HELLO_ACK`。
7. `HELLO_ACK accepted` 后连接才可用于控制消息和数据面。

为什么保留两段握手：`HUB_HELLO` 先证明“对面是持有该节点 bundle 的 Hub”，避免 Agent 对任意扫描者暴露注册流程；既有 `HELLO` 继续承担协议版本、Agent 注册、credential 轮换和节点元数据刷新，避免复制一套身份系统，也保证旧逻辑、旧测试和旧 Agent 行为不变。

### 3.2 能力常量

```text
inbound_connection   Agent 具备入站 listener 与 HUB_HELLO 握手
inbound_data_plane   Agent 具备 inbound HTTPS push/pull 数据面
```

- 只有两个能力都声明时，Hub 才把节点视为“完整 inbound 可用”。
- 只有 `inbound_connection` 时，控制面可用，但文件传输/Artifact/部署/升级按第 6 节返回 409/BLOCKED。
- 旧 Agent 不声明任一能力；首次拨号后若 HELLO 明确不支持，Hub 停止后续拨号，UI 显示“不支持入站”。

### 3.3 非目标协议变更

- 不升 `ProtocolVersion`，不改变既有 `HELLO`、`HEARTBEAT`、`SYNC_*`、`RUN_EXECUTION`、`EXECUTION_*`、`LOG_CHUNK` 的字段语义。
- 不为入站新增可靠队列；控制消息仍按既有语义处理，断线期间的状态由 Desired State/Revision/Execution Journal 恢复。

## 4. TLS、PKI 与认证

### 4.1 组件

| 组件 | 签发/来源 | 用途 | 存储 |
|---|---|---|---|
| Hub CA 证书/私钥 | Hub 首次启用入站时自建，或配置外部文件 | 签发 Agent 服务端证书与 Hub 客户端证书 | CA 私钥仅在 Hub 本地文件，0600；不入库、不进日志 |
| Agent 服务端证书/私钥 | Hub CA 签发，按节点生成 | Agent listener 的 TLS 服务端身份 | bundle 交付给 Agent；Hub 只存证书指纹/有效期/序列号 |
| Hub 客户端证书/私钥 | Hub CA 签发，Hub 级单份 | Hub 拨号时的 mTLS 客户端身份 | Hub 本地文件，0600 |
| 每节点入站令牌 | Hub 随机生成 32 字节 | `HUB_HELLO_ACK` 的 HMAC proof 二次认证 | Hub 存 AES-256-GCM 密文（密钥由 CA 私钥派生）；bundle 存明文 |
| 证书指纹 pin | 证书 DER 的 SHA-256 | 双向防冒充 | Hub 存 Agent 指纹；Agent bundle 存 Hub 客户端证书指纹 |

### 4.2 校验规则

- Hub → Agent：`tls.Config{ClientAuth: RequireAndVerifyClientCert, ClientCAs: HubCA}`；Agent 校验证书链、`ExtKeyUsageClientAuth`、Hub 客户端证书指纹 pin；不匹配立即断开，不发送 `HUB_HELLO_ACK`。
- Agent → Hub：Hub 校验证书链、`ExtKeyUsageServerAuth`、证书 SAN 与 endpoint 主机名/IP 一致、节点证书指纹 pin；SAN 与 pin 都必须匹配，pin 不匹配即使链有效也拒绝。
- 令牌 proof 使用常量时间比较；Hub 只保存 AES-256-GCM 密文，API/日志/审计不返回明文。令牌本身不在 HUB_HELLO 或 HUB_HELLO_ACK 中传输。
- 时间戳偏差超过 60s 拒绝；nonce 在 Agent 侧保留 10 分钟（有界 LRU，最多 1024 条），重复拒绝。
- TLS 最低 1.2；禁止 `InsecureSkipVerify`、明文 HTTP、自签未知证书和“先连接后升级 TLS”。
- 证书 SAN 按 endpoint 生成：域名用 DNS SAN，IP 用 IP SAN；endpoint 变更必须重新签发 bundle，不能复用旧 SAN。
- 证书有效期建议：CA 10 年、Agent/Hub 证书 1 年；到期前 UI 显示告警，但不自动轮换。到期后握手失败，节点离线并显示 `certificate_expired`。

### 4.3 Bundle

`inbound bundle` 是管理员从 Hub 下载、手工复制到 Agent 的 tar.gz，包含：

```text
ca.pem                       # Hub CA
agent-server.pem             # 节点服务端证书
agent-server-key.pem         # 节点私钥，0600
hub-client-fingerprint.txt   # Hub 客户端证书 SHA-256
inbound-token                # 每节点令牌，0600
agent-inbound.yaml           # hub_url、node_id、预分配 agent_id、registration_token、listener/证书路径
install-inbound.sh           # 校验权限、写入目录、重启服务
README.txt                   # 端口/防火墙/回滚说明
```

- 下载接口仅 Administrator；`Cache-Control: no-store`；审计 `node.inbound_bundle_issue`。
- 首次下载生成证书和令牌；再次下载默认拒绝，必须显式调用 rotate 接口并确认“旧证书/令牌立即失效”。rotate 同时关闭该节点的当前入站会话，避免旧令牌继续用于数据面；出站会话不受影响。rotate 审计 `node.inbound_bundle_rotate`。
- bundle 不包含 Agent 二进制；私网 Hub 场景由管理员另行拷贝已安装的 Agent 或二进制包。安装脚本只写配置/证书并重启，不下载任何东西。
- 首次入站注册沿用现有全局 `registration_token` 和预分配 `agent_id`；bundle 必须按秘密管理。后续可引入每节点一次性注册令牌，本次不改变既有注册契约。
- Agent 私钥只存在 Agent 磁盘；Hub 数据库不保存私钥，避免 Hub 备份泄露即可冒充所有 Agent。

## 5. Hub 拨号器实现要点

- 新增 `InboundDialer`，由 `hubserver` 启动/停止，持有 `SessionManager`、`NodeManager`、TLS 配置、CIDR/端口策略和每节点状态。
- 触发拨号的信号：Hub 启动、节点创建/endpoint 更新、bundle 签发/轮换、Agent HELLO 刷新能力、会话断开宽限期结束、手动测试（不注册会话）、退避到期。
- 会话建立后调用与出站相同的 `handleHello` 注册路径，但连接包装为 `dialedConn`（Hub 作为 WebSocket 客户端）并标记 `origin=inbound`、`epoch`。
- `SessionManager` 增加带 origin 的注册策略；`Get` 仍按 nodeID 返回唯一会话。所有发送方无需感知方向。
- 心跳、离线检测沿用现有 `LastSeen`/`HeartbeatTimeout`；拨号器不另建在线判定。
- 诊断字段：`last_attempt_at`、`last_success_at`、`last_error`、`last_error_stage`、`cert_not_after`；只写节点扩展字段，不写敏感信息。
- 观测：`inbound_dial_attempts_total`、`inbound_dial_failures_total{stage}`、`inbound_sessions`、`inbound_handshake_seconds`、`inbound_rejected_total{reason}`、`inbound_data_plane_bytes{direction}`。
- 审计只记录管理员动作（endpoint 修改、bundle 签发/轮换、测试连接），不对每次拨号写 audit，避免噪声；拨号结果进结构化日志和 metrics。

## 6. Inbound 数据面

### 6.1 传输选择

- 会话 origin 为 `outbound`：沿用现有 Agent → Hub HTTP 路径（`/agent/transfers/*`、`/api/artifacts/*/download`、`/api/agent/binary`）。不重复实现反向路径。
- 会话 origin 为 `inbound`：Hub 使用同一 TLS 端口上的 Agent HTTP API 进行 push/pull。数据面选择只看会话 origin，不在失败时静默切换方向；方向切换必须等新会话建立并重新开始传输。
- 没有活跃会话、能力不足或阶段未实现：在创建/下发前拒绝，返回 `409 inbound_data_plane_unavailable`；已进入队列的执行置 `BLOCKED` 并带 `block_reason`，不得等待到超时。

### 6.2 Agent 数据面 API

同一 `18444` TLS listener、HTTP/1.1（ALPN 仅 `http/1.1`，避免 WebSocket 与 h2 混用；WebSocket 与普通 HTTP 共用端口，按路径分流）。单连接约束只针对控制会话；数据面允许受控并发的独立 HTTPS 连接，每个连接仍须 mTLS、令牌和 `session_id`，并受每节点并发/限速约束：

```text
GET  /agent-data/v1/health
GET  /agent-data/v1/transfers/{transfer_id}/source?offset=N
PUT  /agent-data/v1/transfers/{transfer_id}/target?offset=N
PUT  /agent-data/v1/artifacts/{artifact_id}?sha256=...&size=...
PUT  /agent-data/v1/agent-binary?sha256=...&arch=...
```

统一要求：

- mTLS 客户端证书 + `Authorization: Bearer <inbound-token>` + `X-NodeSteer-Session: <session_id>` + `X-NodeSteer-Node: <node_id>`；任一不匹配 401/403。
- `session_id` 必须对应活跃入站会话；会话断开后所有数据面请求失败，传输由上层重试。
- 请求体上限沿用文件传输/Artifact 配置；未知路径 404；不跟随重定向；拒绝 chunked 之外的异常编码。
- 返回 JSON 错误 `{code, message}`；409 用于 offset/状态冲突，413 用于超限，429 用于限速，507 用于磁盘不足。
- 所有写入先落 `.part`，校验 SHA-256 和大小后原子 rename；校验失败删除临时文件并返回 422，不得安装/覆盖。
- 目标路径只能来自已授权的控制消息（transfer ID 对应的 destination），HTTP 请求头不得直接指定任意路径。

### 6.3 文件传输

源端（上传）：

1. Hub 创建 transfer 后向源 Agent 发送 `FILE_UPLOAD_REQUEST`，inbound 模式携带 `transport=inbound_pull`、`upload_url` 为空、transfer ID、大小上限。
2. Agent 校验本地源文件、路径策略、大小和可读性，进入“可拉取”状态；Hub 通过 `GET .../source?offset=N` 拉取。Agent 尚未校验完成时返回 409 `source_not_ready` + `Retry-After`，Hub 有界重试（最多 30s），不得无限等待。
3. Hub 写入现有 staging 文件并计算 SHA-256；源 Agent 在全部字节写出后上报既有 `FILE_UPLOAD_RESULT`，Hub 继续忽略 `OK=true`，以自己完成 staging 为成功唯一依据；失败回执按既有 `Attempt` 世代匹配，只终结当前尝试。
4. 断线/超时：Hub 以 offset 重试；Agent 的 offset 必须等于已发送字节数，否则 409 并返回当前 offset；超过停滞阈值按现有 transfer 规则失败。

目标端（投递）：

1. Hub 向目标 Agent 发送 `FILE_DELIVERY_REQUEST`，inbound 模式携带 `transport=inbound_push`、`download_url` 为空、destination、mode、size、sha256。
2. Agent 预创建目标目录和 `.part`，校验 destination 策略；Hub 通过 `PUT .../target?offset=N` 推送。
3. Agent 校验大小/SHA、原子 rename、设置 mode，返回既有 `FILE_DELIVERY_RESULT`。
4. offset 不匹配、磁盘不足、权限失败、SHA 失败均明确失败；不得保留半文件或静默成功。

### 6.4 Artifact

- 现有 `ARTIFACT_PREFETCH` 增加 `transport=inbound_push`，`url` 为空，携带 artifact ID、SHA、size。
- Hub 在发送部署/执行前调用 `PUT /agent-data/v1/artifacts/{id}` 推送；Agent 按 SHA 缓存，重复推送幂等，内容冲突拒绝。
- Agent 的应用部署只从本地 Artifact 缓存取文件；`artifact_url` 为空且缓存缺失时返回 `BLOCKED artifact_not_staged`，不得尝试访问不可达的 Hub URL。
- 控制通道仍只传元数据与 Prefetch 指令；二进制不进入 WebSocket（保持 §67 不变量 18）。

### 6.5 应用部署

- `DEPLOY_REQUEST` / `RUN_EXECUTION` 的控制字段不变；inbound 模式仅把 `artifact_url` 置空并增加 `artifact_transport=inbound_push`。
- Hub 在下发前完成 Artifact 推送；推送失败则不发送部署请求，Execution 置 `BLOCKED` 并记录原因。
- Agent 侧部署流程、systemd 操作、健康检查、Journal、回滚全部复用现有实现；不因传输方向改变部署语义。
- 如果部署定义没有 Artifact（纯脚本/配置），控制消息本身即可完成，不依赖数据面。

### 6.6 自升级

- inbound 模式下 Hub 不依赖 `GET /api/agent/binary`；先调用 `PUT /agent-data/v1/agent-binary` 推送当前架构二进制。
- Agent 校验 SHA-256、大小和可执行权限，落到固定 staging 路径；`RUN_EXECUTION` 携带 `agent_binary_staged=true` 与 SHA，Agent 只接受该 staging 文件，不接受 Hub 传入的任意路径。
- 推送失败、校验失败或 staging 缺失：Execution `BLOCKED`/`FAILED`，不替换当前二进制。
- 替换、备份、重启、Journal 与回滚复用现有 `upgradeRunner`；重启后 Hub 重新拨号或 Agent 出站重连，按既有 Execution 对账收敛。

### 6.7 降级与 409/BLOCKED 契约

| 场景 | Hub API | Execution |
|---|---|---|
| Agent 无 `inbound_connection` | 节点显示不支持入站；出站可用则继续 | 正常 |
| Agent 有 `inbound_connection` 无 `inbound_data_plane` | 文件传输/Artifact/部署/升级返回 409 `inbound_data_plane_unavailable` | `BLOCKED`，`block_reason` 明确 |
| inbound 会话已断开 | 409 `agent_offline`（既有离线语义优先） | `BLOCKED`/离线策略按既有规则 |
| 某阶段尚未实现（如升级推送） | 409 `inbound_stage_unavailable`，附 stage | `BLOCKED`，不得超时 |
| 推送/拉取中途失败 | 传输 target `FAILED`，Artifact/部署 `BLOCKED`/`FAILED` | 明确原因，可重试 |
| 证书过期/pin 不匹配 | 节点离线并显示 `certificate_expired`/`pin_mismatch` | 不下发 |

所有错误必须使用稳定 `code`，UI 显示可读中文；不得只返回泛化 500 或让请求挂起。

## 7. 数据库迁移

迁移只新增列，默认值保证旧行可用；不删除、不回填、不修改既有 `hostname` 语义。

**版本规划**

- `v26`：Change 2 `add-node-display-name` 的 `nodes.display_name`。
- `v27`：本 change 的入站连接列。

两个 change 的迁移相互独立、可乱序应用（迁移表按未应用版本执行）；实现时仍建议先合入 Change 2，再合入本 change，保持版本单调。若实际实现顺序相反，本 change 使用当时下一个空闲版本，Change 2 使用再下一个；两者不得复用同一版本，也不得把 `display_name` 与入站列合并成互相依赖的迁移。

`v27` 建议列：

```sql
ALTER TABLE nodes ADD COLUMN endpoint TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN connection_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_provisioned INTEGER NOT NULL DEFAULT 0;
ALTER TABLE nodes ADD COLUMN inbound_cert_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_cert_serial TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_cert_not_after TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_token_enc TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_last_handshake TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN inbound_last_error TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN endpoint_updated_at TEXT NOT NULL DEFAULT '';
ALTER TABLE nodes ADD COLUMN endpoint_updated_by TEXT NOT NULL DEFAULT '';
```

- `connection_mode` 只存最近一次成功会话 origin（`outbound`/`inbound`，空表示从未成功），不是策略、不允许 API 写入；API 的 `connection_mode` 由当前会话实时计算，离线时返回 `offline`，同时返回 `last_connection_mode`。
- `endpoint` 存规范化后的 `host:port`（无 scheme/path）；缺省端口补 `18444`。重新注册、HELLO 元数据刷新、`UpsertNode` 均不得覆盖该列。
- `inbound_token_enc` 只存 AES-256-GCM 密文（nonce||ciphertext 的 base64），解密密钥由 Hub CA 私钥派生；`inbound_cert_fingerprint` 存证书 DER SHA-256。
- 所有新列必须从 `UpsertNode` 的 `ON CONFLICT` 更新列表中排除，避免 Agent 注册把管理员配置擦掉；新增专用 `UpdateNodeEndpoint`、`UpdateNodeInboundIdentity`、`UpdateNodeInboundDiagnostics`。
- 迁移测试覆盖：全新库、v25 旧库升级、v26 已存在但 v27 未应用、重复启动幂等、旧 Agent 注册不写新列。

## 8. API、权限与审计

### 8.1 节点 API 扩展

`GET /api/nodes` 与 `GET /api/nodes/{id}` 增加：

```json
{
  "endpoint": "agent.example.com:18444",
  "connection_mode": "inbound",
  "last_connection_mode": "inbound",
  "inbound": {
    "configured": true,
    "capable": true,
    "data_plane": true,
    "cert_fingerprint": "sha256:...",
    "cert_not_after": "2027-11-04T00:00:00Z",
    "last_handshake_at": "...",
    "last_error": ""
  }
}
```

`POST /api/nodes/enrollment`：

- 新增必填 `endpoint`；缺失/非法返回 400。支持 `host`、`host:port`、`[ipv6]`、`[ipv6]:port`；默认端口 18444。
- `endpoint` 与 `node_ip` 分离：`node_ip` 仍是节点地址/身份元数据，`endpoint` 是 Hub 拨号地址；UI 不自动隐藏 endpoint。
- 响应增加 `inbound_bundle_available` 与 bundle 下载提示；生成命令只覆盖出站模式，私网 Hub 场景明确提示使用 bundle 手动安装。

`POST/PUT /api/nodes/{id}`：

- `endpoint` 存在时要求 Administrator；非 Administrator 即使 `canWrite` 也返回 403。更新前校验格式、端口策略，更新后触发拨号器重新评估；审计 `node.endpoint_update`（含 old/new，不含秘密）。endpoint 变更不会自动复用旧证书：Hub 将 `inbound_provisioned` 置 0、响应标记 `inbound_bundle_stale=true`，在重新签发 bundle 前不拨号新 endpoint；UI 提示重新下载/轮换 bundle。
- `status`/`labels` 语义不变。
- `connection_mode` 不接受写入；出现该字段返回 400 或忽略并记录警告（实现取其一，但不得静默改变策略）。

新增：

```text
POST /api/nodes/{id}/connection-test     Administrator；不注册会话；1 次/10s/节点
GET  /api/nodes/{id}/inbound-bundle      Administrator；生成/下载 bundle；审计
POST /api/nodes/{id}/inbound-bundle/rotate Administrator；吊销旧证书/令牌并生成新 bundle
```

测试连接返回分阶段结果：`dns`、`tcp`、`tls`、`pin`、`hub_hello`、`hello`，每阶段 `ok/error`；已有活跃会话时返回 `connected=true` 和当前 origin，不再拨号。`HUB_HELLO` 携带 `test=true`，Agent 不注册业务会话、不改变 Hub 在线状态，测试完成后立即关闭。

### 8.2 权限

| 操作 | Administrator | Operator | Viewer |
|---|---|---|---|
| 查看 endpoint/连接状态 | 是 | 是 | 是 |
| 修改 endpoint | 是 | 否 | 否 |
| 下载/轮换 bundle | 是 | 否 | 否 |
| 测试连接 | 是 | 否 | 否 |
| 查看指纹/有效期 | 是 | 是 | 是 |
| 查看令牌明文 | 否（任何角色均不可） | 否 | 否 |

### 8.3 审计

新增动作：

- `node.endpoint_update`
- `node.inbound_bundle_issue`
- `node.inbound_bundle_rotate`
- `node.connection_test`

不得记录令牌、私钥、证书 PEM；指纹可记录。拨号成功/失败只进结构化日志与 metrics，不写 audit。

## 9. UI

- 添加节点对话框新增必填 `endpoint`，helper 文案“域名或 IP，端口默认 18444”；表单校验失败阻止提交并给出字段错误。
- 添加节点结果增加“入站安装包”入口：说明适用“Hub 内网 + Agent 公网”，下载后手工复制到 Agent；不把出站安装命令伪装成可在该拓扑直接执行。
- 节点列表增加连接方式列（出站/入站/离线，显示“自动”策略），endpoint 可截断展示；不提供模式下拉框。
- 节点详情身份区显示 endpoint、当前/上次连接方式、入站能力、证书指纹（截断 + 复制）、有效期、最近握手、最近错误；Administrator 可见“测试连接”“下载/轮换 bundle”。
- 出站连接正常时，入站状态显示“待命/未使用”，不误导为故障。
- 文件传输、Artifact、部署、升级页面在选中 inbound 节点且数据面不可用时显示 banner，并禁用提交/触发；错误信息使用 409 的稳定 code 映射。
- 节点显示名在 Change 2 合并后使用 `nodeDisplayName`；本 change 先用 `hostname` 回退，不产生硬依赖。

## 10. 兼容与升级

| 组合 | 行为 |
|---|---|
| 旧 Hub + 旧 Agent | 完全不变 |
| 旧 Hub + 新 Agent | 新 Agent 不主动发 `HUB_HELLO`；旧 Hub 不拨号；出站正常。入站 listener 仅在有 bundle 时启动，旧 Hub 不会使用 |
| 新 Hub + 旧 Agent | 旧 Agent 无 `inbound_connection`；Hub 不拨号；出站正常。endpoint 可保存但显示“不支持入站” |
| 新 Hub + 新 Agent，Hub 公网/Agent 内网 | 出站优先；入站 listener 可能不可达，不影响 |
| 新 Hub + 新 Agent，Hub 内网/Agent 公网 | 出站失败后 Hub 拨号；数据面走 inbound push/pull |
| 新 Hub + 新 Agent，双方公网/同内网 | 出站优先；出站持续失败才回退入站 |

升级顺序：

1. 先升级 Hub（新增列、CA 配置、拨号器默认不启用）。
2. 再为需要入站的节点下载 bundle 并安装到 Agent（滚动）。
3. 最后升级其余 Agent；旧 Agent 不受影响。
4. 回滚 Hub：停止拨号器即可；Agent 的 listener 可保留但无人拨号，出站继续。回滚 Agent：卸载 bundle 并停止 listener，Hub 回到等待出站。

配置默认值：

- 没有显式启用 `inbound_enabled=true` 时，bundle 生成与拨号器均禁用；Hub 正常启动并记录“inbound disabled”。
- 启用后若未提供 CA/客户端证书路径，Hub 在数据目录自动生成 CA 与客户端证书（0600）并记录指纹，要求备份；显式配置但不可读或不匹配时启动失败。Agent 只有证书/私钥/令牌齐全时才监听，配置不全时 fail fast。
- `ProtocolVersion` 不变，不需要协同升级窗口。

## 11. 失败模式

| 失败 | 检测 | 处理 |
|---|---|---|
| DNS 解析失败/超时 | 拨号阶段 | 退避；`last_error=dns_*`；不置 online |
| TCP 拒绝/超时 | 拨号阶段 | 退避；`tcp_refused`/`tcp_timeout` |
| TLS 证书链错误 | TLS 阶段 | 不重试到应用层；`tls_verify_failed` |
| 证书指纹不匹配 | pin 阶段 | 拒绝；`pin_mismatch`；告警；不降级 |
| 证书过期 | TLS 阶段 | `certificate_expired`；UI 告警；需 rotate |
| 令牌 proof 错误 | HUB_HELLO_ACK | 401/关闭；`invalid_node_token`；限速 |
| nonce 重放/时间偏差 | HUB_HELLO | 拒绝；`replay_or_clock_skew` |
| 协议版本不匹配 | HELLO | 既有拒绝逻辑；`protocol_mismatch` |
| 双连接竞争 | 会话注册 | 出站优先；入站关闭；不抖动 |
| Hub 重启 | 会话丢失 | Agent 出站重连 + 拨号器重拨；谁先到谁上线，出站最终接管 |
| Agent 重启 | 连接断开 | Hub 置 offline（按既有超时）；恢复后重连 |
| 数据面 401/403 | HTTP | 传输失败；重新握手；不重试到超时 |
| 数据面 409 offset | HTTP | 用返回 offset 重试；超过次数失败 |
| 磁盘不足 | Agent 写入 | 507；传输 FAILED；部署 BLOCKED |
| SHA 不匹配 | Agent 校验 | 422；删除 part；不得安装 |
| 传输中断 | 读写错误/超时 | 保留 part + offset；按既有停滞阈值重试或失败 |
| Hub 私网且 Agent 也私网隔离 | 拨号/出站均失败 | 节点离线；明确提示不支持该拓扑，不无限扫描 |
| 反向代理/端口未开放 | TCP 超时 | 文档提示检查 18444 与防火墙 |

## 12. 安全：SSRF、扫描与滥用

- endpoint 只来自 Administrator 的节点配置；API 不接受任意 URL、scheme、path、userinfo 或重定向。
- Hub 拨号前解析 DNS，过滤 `0.0.0.0/8`、`127.0.0.0/8`、`169.254.0.0/16`（含云 metadata）、`224.0.0.0/4`、`240.0.0.0/4`、`::1`、`fe80::/10`、`ff00::/8`；默认允许 RFC1918（Hub 内网场景需要），可用 `inbound_dial_denied_cidrs` 进一步限制。
- 默认只允许端口 `18444`；其他端口必须加入 `inbound_allowed_ports`，避免把 Hub 变成任意端口扫描器。
- 每次尝试重新解析并只拨允许的 IP；DNS rebinding 由证书 pin 兜底。解析结果混合允许/禁止 IP 时只拨允许项，全部禁止则失败。
- Agent listener 默认 `:18444`，可用 `inbound_allowed_source_cidrs` 限制来源；建议只放行 Hub 出口 IP。无 CIDR 限制时依赖 mTLS + 令牌，但文档要求生产配置。
- 限速：Agent 每 IP 10 次/分钟握手、全局 100 次/分钟、并发握手 8；Hub 拨号全局并发 16、单节点 1；数据面每节点 100 请求/分钟（大文件流不受请求数影响）。
- 单连接：每节点最多一个已认证会话；重复连接关闭旧连接或拒绝新连接，按第 2.3 节。
- 令牌/证书/私钥不进入日志、API、审计、错误消息；错误只返回阶段和稳定 code。
- 失败关闭：任何校验失败都关闭连接，不降级到 HTTP、不跳过 pin、不回退到旧协议。

## 13. 测试矩阵

**单元**

- `HUB_HELLO`/`HUB_HELLO_ACK` 编解码、`HelloAckPayload` 嵌入兼容、nonce/时间戳校验、能力常量。
- endpoint 解析/规范化/校验（域名、IPv4、IPv6、默认端口、非法 scheme/path/端口）。
- 拨号器状态机、退避、DNS 重解析、CIDR/端口策略、并发上限、会话所有权。
- Agent listener：mTLS、pin、令牌、源 CIDR、限速、握手超时、重复连接、无 bundle 不监听。
- 数据面：offset 冲突、SHA 校验、大小限制、磁盘不足、路径策略、会话绑定。

**集成**

- 四种拓扑（用 loopback/可配置地址模拟）：公网 Hub/私网 Agent、私网 Hub/公网 Agent、双公网、同内网。
- Hub 重启、Agent 重启、双方同时重连、出站/入站竞争、宽限期、会话 epoch 迟到消息。
- 文件传输：源 pull 上传、目标 push 投递、断点续传、取消、重试、SHA 失败、磁盘满。
- Artifact：推送/缓存/重复推送/内容冲突/部署前缺失 BLOCKED。
- 应用部署：inbound 正常部署、健康检查、回滚、推送失败。
- 自升级：inbound 推送/校验/备份/替换/重启/对账/回滚；Docker 形态拒绝。
- 兼容：旧 Hub + 新 Agent、新 Hub + 旧 Agent、旧 DB 升级、未启用 inbound 时默认禁用、启用后自动生成 CA。

**安全**

- 错误/缺失/过期证书、pin 不匹配、令牌错误、重放、时钟偏差、暴力握手限速。
- 云 metadata 地址、loopback、link-local、非允许端口、DNS 重绑定、任意路径写入、超大 body。
- 审计与日志不含秘密；viewer/operator 越权 endpoint/bundle/test 被拒。

**Web E2E**

- 添加节点 endpoint 必填/默认端口/非法输入。
- 节点列表/详情显示连接方式、endpoint、指纹、错误。
- 测试连接分阶段结果；bundle 下载/轮换确认；无权限隐藏/禁用。
- inbound 数据面不可用时传输/部署/升级的 banner 与 409/BLOCKED 提示。
- 私网 Hub 场景的 bundle 安装说明可读。

## 14. 文档更新

- `docs/ARCHITECTURE.md`：§15/§16 连接与会话、§58 网络架构、§59 TLS、§62 故障恢复、§67 不变量 2 的措辞（从“Agent 主动连接 Hub”改为“Agent 必须建立或接受 Hub 发起的已认证连接；Hub 仍是唯一权威源”）。
- `docs/PRODUCT.md`：§7 网络模型、§8 Gateway、§17 节点管理、§33/§34.1 Artifact/文件中继、§38 部署、§44 权限、§49 非目标。
- `docs/DEPLOYMENT.md`：新增 inbound 章节（CA 生成/备份、bundle 安装、18444 防火墙、Docker 端口映射、私网 Hub 手动安装、回滚）。
- `README.md`：拓扑选择与最小配置指引。
- `.env.example`、`docker-compose.yml`：Hub CA/客户端证书/拨号器开关、Agent listener 配置与端口示例。
- 新增 `docs/INBOUND_CONNECTION.md`（可选）：运维 runbook、证书轮换、故障排查。

## 15. 未决/实现注意

- `HelloAckPayload` 的嵌入扩展需保持 JSON 字段不冲突；实现时补协议表驱动测试，避免旧客户端解析失败（旧客户端不会收到这些消息，但测试仍要覆盖）。
- 自升级 staging 路径必须由 Agent 固定生成，Hub 只传 SHA/标志；不得接受任意路径。
- 私网 Hub 的 `HUB_BASE_URL` 对 Agent 不可达时，出站循环仍会失败；文档需说明这是预期，不影响入站，但会产生日志。可在后续增加“出站禁用”配置，本次不做。
- 证书自动轮换、HA 多 Hub、每节点注册令牌不在本次范围；bundle rotate 是唯一轮换手段。
- Change 2 未合并时，UI 使用 `hostname`；合并后统一走 `nodeDisplayName`，本 change 不复制该 helper。
