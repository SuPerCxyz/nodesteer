# hub/core Specification

## Purpose
定义 Hub 在一期完整闭环中的安全、版本、一致性和执行确认行为。
## Requirements
### Requirement: Secure Agent transport

The Hub SHALL support configurable TLS for Web/API and Agent Gateway endpoints, and SHALL authenticate an Agent with the registration token only for first registration and a persisted unique credential thereafter.

#### Scenario: Credential reconnect

- **WHEN** an Agent has received and persisted a Hub credential
- **THEN** its next HELLO uses the credential without requiring the registration token
- **AND** a revoked credential is rejected

### Requirement: Transactional desired state

The Hub SHALL commit object or target-membership changes, global revision, change log, audit, and required deletion records before sending notifications.

#### Scenario: Target membership changes

- **WHEN** a group, label, or application assignment changes
- **THEN** affected Agents receive a revision notification after commit
- **AND** the desired revision is durable even if notification delivery fails

### Requirement: Complete reconciliation

The Hub SHALL provide enough desired-state information for an Agent to remove objects no longer assigned to it, including full resync.

#### Scenario: Removed target

- **WHEN** a task target changes so that an Agent no longer matches it
- **THEN** the next sync response identifies the stale task and related schedule for deletion
- **AND** the Agent can converge without a manual restart

### Requirement: Idempotent execution acknowledgement

The Hub SHALL acknowledge execution result uploads and process duplicate uploads by execution ID or scheduled slot without duplicate history.

#### Scenario: Duplicate result upload

- **WHEN** the same execution result is uploaded more than once
- **THEN** the Hub returns an acknowledgement for each accepted duplicate
- **AND** stores only one execution history record

### Requirement: Cron Schedule 触发
系统 SHALL 支持 Cron 类型的 Schedule 在 Hub（execution_owner=hub）侧按标准 cron 表达式正确触发。

#### Scenario: Cron 到点触发
- **WHEN** 存在启用的 Cron Schedule 且已到触发时刻
- **THEN** Hub 为该 Schedule 创建 Execution 并分发给目标节点

#### Scenario: Cron 表达式非法
- **WHEN** Schedule 的 Cron 表达式无法解析
- **THEN** 该 Schedule 被跳过并记录错误，不影响其他 Schedule

### Requirement: Remote Node Condition
系统 SHALL 支持远程节点条件求值：`node == ONLINE`、`node.last_execution(task) == SUCCESS`；并在远程状态解析结果为 UNKNOWN 时按 Fail Closed 处理，不得将未知值作为普通值参与比较。

#### Scenario: 查询在线状态
- **WHEN** 条件引用另一节点的在线状态
- **THEN** 返回该节点 Value/ObservedAt/TTL，未过期的真实值参与求值

#### Scenario: 状态过期
- **WHEN** Remote State 超出 TTL 或不可确认
- **THEN** 视为 UNKNOWN，条件评估结果 Fail Closed（BLOCKED），不得将 UNKNOWN 当作 TRUE

#### Scenario: 远程节点不存在
- **WHEN** Remote Condition 引用的节点不存在或属性不可解析
- **THEN** 返回不可评估标记（evaluated=false）
- **AND** 执行被 BLOCKED，不得放行

#### Scenario: 未知值参与比较
- **WHEN** 远程状态值恰为字符串 "UNKNOWN"
- **THEN** 该值不得参与 ==/!= 等比较，一律视为不可评估
- **AND** 执行结果 BLOCKED

### Requirement: Tombstone 删除同步
系统 SHALL 在删除 Script/Task/Schedule/Application 时产生 Tombstone，供 Agent 同步删除本地副本。

#### Scenario: 删除对象产生 Tombstone
- **WHEN** Hub 删除任一 Desired State 对象
- **THEN** 该对象的删除记录进入 Agent 同步响应，Agent 停止调度并删除本地副本、保留历史 Execution

### Requirement: Manual Run 幂等
系统 SHALL 支持同一 Task 在相同节点上的多次手动执行。

#### Scenario: 连续手动执行
- **WHEN** 同一 Task 对同一节点连续触发多次 Manual Run
- **THEN** 每次创建独立 Execution 并执行，不被上一次记录阻塞

### Requirement: 执行状态与取消
系统 SHALL 完整支持 Execution 状态机 PENDING/RUNNING/SUCCESS/FAILED/SKIPPED/CANCELED/TIMED_OUT。

#### Scenario: 取消执行
- **WHEN** 用户对 RUNNING 执行发起取消
- **THEN** Hub 通知 Agent 取消并记录 CANCELED 状态

#### Scenario: 超时执行
- **WHEN** 执行超过 Task.Timeout
- **THEN** Agent 终止进程组并上报 TIMED_OUT

### Requirement: Operator 权限
系统 SHALL 限定 Operator 角色仅可查看、运行任务、查看日志、部署与操作 Managed Application，不可创建/编辑/删除定义类对象。

#### Scenario: Operator 写定义被拒
- **WHEN** Operator 尝试创建/编辑/删除 Script/Task/Schedule/Group/Artifact/Application
- **THEN** 返回 403

### Requirement: Application Upgrade 审计
系统 SHALL 区分记录应用部署动作：deploy / upgrade / start / stop / restart。

#### Scenario: 升级审计
- **WHEN** 对应用执行升级
- **THEN** 审计日志记录动作类型为 upgrade

### Requirement: Hub OIDC 认证
系统 SHALL 支持标准 OIDC Authorization Code Flow + PKCE（公开客户端），通过 IdP 完成登录。本地账号密码登录与 SSO SHALL 由 `oidc.allow_local_login` **互斥选择**（三态，nil 视为 true，默认本地模式）：`true` 时本地密码登录可用且 OIDC 完全失效；`false` 时 SSO 是唯一登录方式、密码登录一律 403（与本地账户是否存在无关）。两种方式登录成功后 SHALL 产生权限完全一致的会话。

#### Scenario: 完整 SSO 登录流程
- **WHEN** Hub 以 `oidc.allow_local_login=false` 启动且浏览器访问 `GET /api/oidc/login`
- **THEN** 302 跳转到 IdP 授权端点，query 含 state 与 code_challenge
- **WHEN** IdP 授权后回跳 `GET /api/oidc/callback?state=...&code=...`
- **THEN** state 校验通过、ID Token 经 JWKS 验证，按 username claim 自动创建本地用户
- **AND** 用户角色按 role_mappings 映射（未命中则默认 viewer）
- **AND** 返回有效 session，后续请求可用 Bearer token 通过 `/api/me`

#### Scenario: 配置 issuer 后本地登录被禁用
- **WHEN** Hub 配置了 `oidc.issuer` 且 `oidc.allow_local_login=false`
- **AND** 客户端调用 `POST /api/login` 提交本地账号密码
- **THEN** 返回 403 与错误信息 "local login disabled"
- **AND** 不创建任何会话
- **AND** 本地账户是否存在、密码是否正确均不影响该 403（绝对语义）
- **AND** 该拒绝写入审计（`Action=login`，detail 标识 local login disabled）

#### Scenario: 默认本地模式下 OIDC 失效
- **WHEN** `oidc.allow_local_login` 未显式配置或为 `true`（即使同时配置了 `oidc.issuer`）
- **THEN** 本地账号密码登录可用
- **AND** `GET /api/oidc/state` 返回 `{"enabled": false, "local_fallback": true}`
- **AND** `GET /api/oidc/login` 与 `GET /api/oidc/callback` 返回 404
- **AND** 启动期不请求 IdP discovery、不构造 OIDC 客户端，IdP 不可达不影响启动

#### Scenario: 两种登录方式权限一致
- **WHEN** 同角色用户分别经本地密码登录（本地模式）与 SSO 登录（`allow_local_login=false` + IdP）
- **THEN** 两者 `/api/me` 返回的角色一致
- **AND** 受保护端点的允许/拒绝行为逐点一致

#### Scenario: 登录失败写入审计并限速
- **WHEN** `POST /api/login` 提交错误凭证
- **THEN** 返回 401，并写入 `Action=login` 的失败审计（detail 不含密码）
- **AND** 同一 IP+用户名连续失败达到阈值后，后续尝试被递增延迟，登录成功后重置

#### Scenario: callback state 校验失败
- **WHEN** `allow_local_login=false` 模式下客户端访问 `GET /api/oidc/callback` 且 state 与存储不匹配或已过期
- **THEN** 拒绝并 302 回前端登录页带错误标记
- **AND** 不创建会话

### Requirement: OIDC 配置与状态端点
系统 SHALL 提供状态端点供前端判断登录入口：`enabled` 仅在 SSO 唯一登录模式且 OIDC 客户端就绪时为 true，`local_fallback` 表示本地密码登录是否可用（字段名保留以兼容旧前端）。SSO 唯一登录模式下未配置 `oidc.issuer` 或 discovery 失败 SHALL 拒绝启动；本地模式启动 SHALL 不依赖 IdP 可达性、永不因 OIDC 失败。

#### Scenario: 未配置 issuer 时 OIDC 禁用
- **WHEN** Hub 未配置 `oidc.issuer` 且 `oidc.allow_local_login` 为默认 `true`
- **THEN** `GET /api/oidc/state` 返回 `{"enabled": false, "local_fallback": true}`
- **AND** 本地账号密码登录行为与未引入 OIDC 时一致

#### Scenario: 配置 issuer 时启用
- **WHEN** Hub 配置了 `oidc.allow_local_login=false` 与 `oidc.issuer` 且 discovery 成功
- **THEN** `GET /api/oidc/state` 返回 `{"enabled": true, "local_fallback": false}`
- **AND** `GET /api/oidc/login` 生成 state+PKCE 并 302 跳转 IdP

#### Scenario: SSO 模式缺少 issuer 拒绝启动
- **WHEN** `oidc.allow_local_login=false` 且未配置 `oidc.issuer`
- **THEN** Hub 拒绝启动并输出明确错误（allow_local_login=false requires oidc.issuer）

#### Scenario: SSO 模式初始化失败拒绝启动
- **WHEN** 配置了 `oidc.issuer` 且 `oidc.allow_local_login=false`
- **AND** 启动期无法获取 IdP discovery 文档
- **THEN** Hub 拒绝启动并输出明确错误（fail-fast）
- **AND** 恢复方式为切回 `allow_local_login=true` 后重启

### Requirement: Run User 接线
系统 SHALL 支持以指定用户运行任务。

#### Scenario: 指定运行用户
- **WHEN** Task 或 Script 配置了 run_user 且 Agent 以 root 运行
- **THEN** 执行进程以该用户身份启动
- **AND** 未配置时保持现状（当前用户）

### Requirement: Agent-owned 调度的应用部署
系统 SHALL 支持 execution_owner=agent 的 Application Deploy/Upgrade Task。

#### Scenario: Agent 本地触发部署
- **WHEN** Agent Local Scheduler 触发 application_deploy Task
- **THEN** Agent 从本地应用定义解析 binary_path/unit/health 并执行部署
- **AND** 结果经 DeployResult 上报 Hub

### Requirement: Prefetch 覆盖调度部署
系统 SHALL 在 Scheduled/Offline Application Deployment 前向 Agent 下发 PREFETCH。

#### Scenario: 调度部署前预取
- **WHEN** Agent-owned Schedule 触发 Application Deploy/Upgrade
- **THEN** Agent 先下载并校验 Artifact 到缓存
- **AND** 部署时直接使用缓存

### Requirement: Settings 运行期生效
系统 SHALL 使 heartbeat_interval_sec / changelog_window 等设置运行期生效。

#### Scenario: 修改设置生效
- **WHEN** 管理员通过 /api/settings 修改 changelog_window
- **THEN** Hub 运行期读取新值，无需重启

### Requirement: Gateway 单端口模式
系统 SHALL 支持 Agent Gateway 与 Web API 共用同一监听地址：当配置的 Gateway 地址与 Web 地址相同时，Hub SHALL 只启动一个 listener，并按请求特征分流 —— 携带 `Upgrade: websocket` 头的请求与 `/agent/transfers/*` 路径 SHALL 转入 Gateway 处理，其余请求 SHALL 照常进入 Web 路由。配置不同时（默认 `:8443`）SHALL 保持既有双独立 listener 行为不变。

#### Scenario: 单端口按协议特征分流
- **WHEN** Hub 配置 `gateway_addr` 与 `web_addr` 相同（如均为 `:8080`）
- **THEN** Hub 仅启动一个 listener
- **AND** 同一端口上：Agent WebSocket 握手（子协议 `nodesteer`）成功建立并完成注册与心跳
- **AND** `/agent/transfers/*` 上传/下载经同一端口完成
- **AND** REST API、静态前端与 `/healthz` 在同一端口正常响应
- **AND** 若此时误配 Gateway TLS 证书，Hub 输出 WARN 且不影响启动（反代部署下 TLS 恒空）

#### Scenario: 纳管与传输地址跟随单端口派生
- **WHEN** 未显式配置 `gateway_base_url` 且 Gateway 地址与 Web 同端口
- **THEN** 生成的纳管命令 WebSocket 地址与文件传输地址使用该共同端口（如 `ws://host:8080`）
- **AND** 地址派生不再依赖硬编码的 8443 回退值

#### Scenario: 默认双端口行为回归
- **WHEN** `gateway_addr` 保持默认 `:8443` 且与 `web_addr` 不同
- **THEN** Web 与 Gateway 维持两个独立 listener，既有连接、传输与纳管行为不发生任何变化

