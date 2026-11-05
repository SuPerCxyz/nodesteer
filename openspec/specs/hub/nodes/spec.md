# hub/nodes Specification

## Purpose
Hub 管理 Agent Node 的注册、心跳、在线状态、Inventory、Capability、Label 与 Group，作为节点生命周期与同步调度的基础。
## Requirements
### Requirement: Node 列表与详情 API
系统 SHALL 提供 Node 列表与详情 API，至少包含 Node ID、Hostname、IP、OS、Architecture、Labels、Agent Version、Deployment Mode、Host Integration、Sync Status、Last Seen。

#### Scenario: 查询节点列表
- **WHEN** 调用 GET /api/nodes
- **THEN** 返回包含上述字段的 Node 列表

### Requirement: 节点在线状态机

系统 SHALL 维护节点在线状态的三段生命周期：纳管创建的记录初始为 `pending`（等待 Agent 接入）；仅当 Agent 的 HELLO 被接受后置为 `online`；已在线节点连接断开或心跳超时后置为 `offline`。`maintenance` 与 `disabled` 状态 SHALL 不被任何自动转换覆盖。

#### Scenario: 纳管创建显示等待

- **WHEN** 管理员生成纳管命令并持久化 pending node record
- **THEN** 该节点 status 为 `pending`，Web 列表显示「等待中」
- **AND** 在 HELLO 被接受前不得显示为 `online`

#### Scenario: HELLO 接受后上线

- **WHEN** Agent 携有效 credential 或 registration token 完成 HELLO 且被接受
- **THEN** 节点 status 置为 `online`
- **AND** `maintenance`/`disabled` 节点保持原状态不被覆盖

#### Scenario: HELLO 被拒绝不滞留在线

- **WHEN** Agent 的 HELLO 被拒绝（credential 无效、节点不存在或 registration token 无效）
- **AND** 该拒绝可定位到既有节点
- **THEN** 若该节点当前为 `online` 且没有活跃 Agent 会话，在关闭连接前置为 `offline`
- **AND** 若该节点存在活跃 Agent 会话，拒绝不改变其状态
- **AND** 从未成功会话的 `pending` 节点保持 `pending`

#### Scenario: 断开与心跳超时置离线

- **WHEN** 已在线节点连接断开或心跳超时
- **THEN** 节点 status 置为 `offline`
- **AND** `maintenance`/`disabled` 不被该转换覆盖

#### Scenario: Hub 启动复位孤儿在线

- **WHEN** Hub 启动
- **THEN** 所有没有活跃 Agent 会话的 `online` 节点被复位为 `offline`
- **AND** 持有活跃会话的节点状态不受影响

### Requirement: 节点暂停语义

节点处于 `maintenance`（暂停）时，系统 SHALL 拦截全部任务型下发：手动执行、调度触发、应用部署/操作、文件传输指令、制品预取指令；settings 下发与变更通知等配置同步 SHALL 不受影响。Agent 在暂停态 SHALL 拒收执行与部署指令作为双保险。暂停 SHALL 不终止节点上已部署的常驻服务，也不中止运行中的任务，仅停止新下发。调度因暂停跳过时 SHALL 产生 SKIPPED 执行记录。管理员 SHALL 可批量将所选节点设置为暂停或恢复。

#### Scenario: 暂停拦截任务型下发

- **WHEN** 节点处于暂停状态
- **THEN** 手动执行、调度触发、应用部署、文件传输与制品预取请求不被下发到该节点
- **AND** settings 下发与变更通知照常送达

#### Scenario: 暂停期常驻服务与运行中任务

- **WHEN** 节点处于暂停状态且其上已部署常驻服务、存在运行中任务
- **THEN** 已部署服务继续运行，运行中的任务执行至完成
- **AND** 不再向该节点派发任何新任务

#### Scenario: 调度跳过留痕

- **WHEN** 调度到点但目标节点处于暂停状态
- **THEN** 创建 SKIPPED 状态的执行记录（含目标节点与跳过原因），不再静默丢弃

#### Scenario: Agent 暂停态拒收

- **WHEN** Agent 本地处于暂停态且收到执行或部署指令
- **THEN** Agent 拒收该指令并回报，不启动本地执行

#### Scenario: 批量暂停与恢复

- **WHEN** 管理员在节点列表多选节点并执行批量暂停（或恢复）
- **THEN** 所选节点状态统一变更，逐节点产生状态操作结果反馈

### Requirement: 节点 endpoint 与自动连接模式

Hub SHALL 在创建节点时要求提供 `endpoint`（域名或 IP，可带端口，默认 `18444`），并 SHALL 在重新注册、HELLO 元数据刷新和普通 Upsert 时保留该值。`endpoint` 的修改 SHALL 仅允许 Administrator。系统 SHALL 自动选择出站或入站连接，不提供用户可写的 `connection_mode`；API/UI 暴露的 `connection_mode` 是当前会话的只读观测值。

#### Scenario: 添加节点必须填写 endpoint

- **WHEN** Administrator 调用节点纳管 API 或提交添加节点表单但没有提供有效 `endpoint`
- **THEN** 请求返回 400 并指出 endpoint 必填或格式非法
- **AND** 不会创建缺少 endpoint 的节点记录

#### Scenario: endpoint 默认端口

- **WHEN** 管理员提交 `endpoint=agent.example.com`
- **THEN** 系统按 `agent.example.com:18444` 保存和展示
- **AND** 提交 `[2001:db8::10]:18444` 时按 IPv6 地址加端口处理

#### Scenario: 重新注册不覆盖 endpoint

- **WHEN** Agent 以已有身份重新注册并上报了新的 hostname
- **THEN** 节点的 `hostname` 按既有语义更新
- **AND** `endpoint`、入站证书指纹和入站令牌密文保持不变

#### Scenario: 不允许手动选择连接模式

- **WHEN** 调用方在创建或更新节点时提交 `connection_mode`
- **THEN** 系统不把它作为策略保存，也不因此强制入站或出站
- **AND** 实际连接方向仍由会话与自动选择规则决定

#### Scenario: 修改 endpoint 需要管理员

- **WHEN** Operator 或 Viewer 提交新的 `endpoint`
- **THEN** 请求返回 403 且 endpoint 不变
- **AND** Administrator 提交合法 endpoint 时更新成功并记录审计

#### Scenario: endpoint 变更后证书失效

- **WHEN** Administrator 修改了 endpoint 但没有重新签发 bundle
- **THEN** 响应标记入站 bundle 已过期，Hub 暂停对该节点的入站拨号，UI 提示重新下载/轮换
- **AND** 新 endpoint 的 TLS SAN 不匹配时不会静默使用旧证书连接新地址

### Requirement: 首次入站引导

对于已签发入站 bundle 且 endpoint 已知、但从未完成 HELLO 的 pending 节点，Hub SHALL 允许按 endpoint 发起首次入站拨号，不得要求先收到一次出站 HELLO 或已存储的 `inbound_connection` 能力。首次握手成功后，Hub SHALL 按正常注册路径保存能力与在线状态；若 HELLO 明确不支持入站，Hub SHALL 停止后续拨号。

#### Scenario: 全新公网 Agent 首次接入

- **WHEN** 管理员创建节点、填写 endpoint 并下载 bundle，但 Agent 因 Hub 在内网而无法出站注册
- **THEN** Hub 在 bundle 签发后按 endpoint 拨号并完成首次 HUB_HELLO/HELLO 握手
- **AND** 节点在 HELLO 被接受后置为 online，而不是一直停留在 pending

#### Scenario: 首次握手确认不支持

- **WHEN** 被拨号的旧 Agent 完成 HELLO 但没有声明 `inbound_connection`
- **THEN** Hub 记录该节点不支持入站并停止后续拨号
- **AND** 不影响该 Agent 已有的或后续的出站连接

### Requirement: 入站连接状态与诊断

Hub SHALL 在节点详情中暴露只读的入站配置与诊断信息，包括 `endpoint`、当前/上次连接方式、Agent 是否声明 `inbound_connection`、是否具备 `inbound_data_plane`、证书指纹、证书有效期、最近握手时间和最近错误。Hub SHALL NOT 通过 API 返回入站令牌、私钥或证书私钥材料。

#### Scenario: 查看入站状态

- **WHEN** 已授权用户打开节点详情或调用节点详情 API
- **THEN** 返回 endpoint、当前连接方式、上次连接方式、入站能力和证书诊断
- **AND** 响应中不包含令牌明文或任何私钥

#### Scenario: 旧 Agent 不支持入站

- **WHEN** 节点 Agent 在 HELLO 中明确未声明 `inbound_connection`
- **THEN** 详情显示“不支持入站”，Hub 停止后续拨号
- **AND** 若出站会话存在，节点仍可正常监控和操作

#### Scenario: 测试连接

- **WHEN** Administrator 对无活跃会话的节点发起连接测试
- **THEN** Hub 执行 DNS、TCP、TLS、pin、HUB_HELLO 和 HELLO 分阶段检查并返回每阶段结果
- **AND** 测试不注册持久会话、不改变节点在线状态，Agent 也不把测试连接视为业务在线会话，并按节点限速

#### Scenario: 已有会话时测试

- **WHEN** 节点已有活跃出站或入站会话时发起连接测试
- **THEN** Hub 返回当前已连接及连接方向，不再发起第二次拨号

#### Scenario: 未签发 bundle 时测试

- **WHEN** 节点没有入站证书/令牌或 Hub 入站功能未启用时发起连接测试
- **THEN** 返回 409 `inbound_not_provisioned` 或 `inbound_disabled`
- **AND** 不尝试无认证的 TCP/TLS 探测

### Requirement: 入站身份签发与轮换

Hub SHALL 为节点签发入站 bundle，包含 Hub CA、节点服务端证书/私钥、Hub 客户端证书指纹、每节点入站令牌和安装说明。bundle 下载与轮换 SHALL 仅允许 Administrator，并 SHALL 记录审计。重新签发 SHALL 使旧证书和旧令牌立即失效；Hub SHALL 只保存证书指纹/有效期和令牌密文，不保存私钥或令牌明文。

#### Scenario: 首次下载 bundle

- **WHEN** Administrator 首次请求节点的入站 bundle
- **THEN** Hub 生成新的节点证书和令牌并返回 `Cache-Control: no-store` 的压缩包
- **AND** 记录 `node.inbound_bundle_issue` 审计，响应不包含明文令牌以外的其他节点秘密

#### Scenario: 轮换 bundle

- **WHEN** Administrator 确认轮换节点的入站身份
- **THEN** Hub 生成新证书和新令牌，旧指纹和旧令牌不再能完成新握手
- **AND** 记录 `node.inbound_bundle_rotate` 审计；当前入站会话被关闭，重连必须使用新身份，出站会话不受影响

#### Scenario: 非管理员下载

- **WHEN** Operator 或 Viewer 请求 bundle 或轮换接口
- **THEN** 返回 403 且不生成任何新证书或令牌

### Requirement: 会话所有权与离线判定

Hub SHALL 保证每个节点最多一个已认证会话，并 SHALL 优先保留出站会话。入站会话仅在无出站会话时注册；出站握手成功后 SHALL 替换入站会话。节点 SHALL 仅在 HELLO 被接受后置为 online；TCP/TLS 成功但未完成 HELLO 不得置为 online。拨号失败 SHALL NOT 把已有会话的节点置为 offline。

#### Scenario: 出站优先

- **WHEN** 节点已有出站会话时收到新的入站握手
- **THEN** Hub 拒绝并关闭入站连接，保留出站会话
- **AND** 记录重复连接诊断，不把节点置为 offline

#### Scenario: 入站回退

- **WHEN** 节点没有会话且出站持续失败，但 endpoint 可达且入站握手成功
- **THEN** Hub 注册入站会话，并在 HELLO 被接受后把节点置为 online
- **AND** 后续出站重连成功时由出站会话接管

#### Scenario: 拨号失败不误判离线

- **WHEN** Hub 对一个已有活跃会话的节点发生拨号失败
- **THEN** 节点保持 online，现有会话不受影响
- **AND** 仅更新入站诊断字段

#### Scenario: 心跳超时

- **WHEN** 入站或出站会话超过心跳超时
- **THEN** Hub 关闭会话并按既有在线状态机置为 offline
- **AND** `maintenance`/`disabled` 状态不被覆盖

### Requirement: 节点显示名

Hub SHALL 为每个节点提供独立的 `display_name` 字段，默认空字符串，用于用户可见名称。`display_name` SHALL NOT 参与节点身份、Agent ID、hostname 匹配、权限、执行目标或路由。创建、重新注册、HELLO 元数据刷新、重新纳管和普通 Upsert SHALL NOT 覆盖已设置的 `display_name`。

#### Scenario: 新节点默认空

- **WHEN** 创建或纳管一个新节点
- **THEN** `display_name` 为空字符串
- **AND** API/UI 在显示时回退到 `hostname`

#### Scenario: 重新注册保留显示名

- **WHEN** Agent 以已有身份重新注册并上报新的 hostname
- **THEN** `hostname` 按既有语义更新
- **AND** `display_name` 保持不变

#### Scenario: 重新纳管保留显示名

- **WHEN** 管理员对既有节点重新生成纳管命令
- **THEN** 节点记录被复用，`display_name` 不被清空
- **AND** 只有显式重命名请求可以修改或清除它

#### Scenario: 显示名不参与身份

- **WHEN** 两个节点具有相同 `display_name`
- **THEN** 系统仍按 node ID / Agent ID 区分它们
- **AND** 不因重名拒绝创建、执行或查询

### Requirement: 显示名校验与更新 API

Hub SHALL 在节点 API 中返回 `display_name`（空值返回空字符串），并 SHALL 支持通过单节点更新接口 `POST/PUT /api/nodes/{id}` 设置或清除该字段；批量 `POST /api/nodes` SHALL NOT 接受 `display_name`。更新 SHALL 在去首尾空白后按 Unicode 码点限制为最多 64 个字符，拒绝控制字符；空字符串 SHALL 表示清除。更新不存在的节点 SHALL 返回 404，非法输入 SHALL 返回 400 且不得产生部分修改。

#### Scenario: 设置显示名

- **WHEN** Operator 提交合法的 `display_name`
- **THEN** 节点详情和列表 API 返回新值
- **AND** `hostname` 保持 Agent 上报值不变

#### Scenario: 清除显示名

- **WHEN** 用户提交空字符串或仅空白
- **THEN** `display_name` 变为空
- **AND** UI 回退显示 hostname

#### Scenario: 超长或控制字符

- **WHEN** 提交超过 64 个码点或包含控制字符的值
- **THEN** 返回 400，节点原显示名不变
- **AND** 不写入部分字段或审计成功记录

#### Scenario: 节点不存在

- **WHEN** 对不存在的 node ID 提交 `display_name`
- **THEN** 返回 404
- **AND** 不创建节点或审计成功记录

#### Scenario: 相同值重复提交

- **WHEN** 提交与当前值相同的 `display_name`
- **THEN** 请求成功但不产生重复的重命名审计
- **AND** 不触发无关的节点更新副作用
