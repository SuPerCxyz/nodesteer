# hub-core Specification Delta

## MODIFIED Requirements

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
