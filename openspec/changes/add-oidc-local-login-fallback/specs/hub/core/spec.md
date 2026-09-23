# hub-core Specification Delta

## MODIFIED Requirements

### Requirement: Hub OIDC 认证
系统 SHALL 支持标准 OIDC Authorization Code Flow + PKCE（公开客户端），通过 IdP 完成登录。本地账号密码登录的可用性 SHALL 由 `oidc.allow_local_login` 显式控制：默认禁用，显式开启后作为 break-glass 与 SSO 并存。

#### Scenario: 完整 SSO 登录流程
- **WHEN** 浏览器访问 `GET /api/oidc/login`
- **THEN** 302 跳转到 IdP 授权端点，query 含 state 与 code_challenge
- **WHEN** IdP 授权后回跳 `GET /api/oidc/callback?state=...&code=...`
- **THEN** state 校验通过、ID Token 经 JWKS 验证，按 username claim 自动创建本地用户
- **AND** 用户角色按 role_mappings 映射（未命中则默认 viewer）
- **AND** 返回有效 session，后续请求可用 Bearer token 通过 `/api/me`

#### Scenario: 配置 issuer 后本地登录被禁用
- **WHEN** Hub 配置了 `oidc.issuer` 且 `oidc.allow_local_login=false`（默认）
- **AND** 客户端调用 `POST /api/login` 提交本地账号密码
- **THEN** 返回 403 与错误信息 "local login disabled"
- **AND** 不创建任何会话

#### Scenario: 开启兜底后 OIDC 启用时本地登录仍可用
- **WHEN** Hub 配置了 `oidc.issuer` 且 `oidc.allow_local_login=true`
- **AND** 客户端调用 `POST /api/login` 提交有效本地账号密码
- **THEN** 按本地凭证正常认证并返回 session（IdP 不可用时仍可登录）

#### Scenario: callback state 校验失败
- **WHEN** 客户端访问 `GET /api/oidc/callback` 且 state 与存储不匹配或已过期
- **THEN** 拒绝并 302 回前端登录页带错误标记
- **AND** 不创建会话

### Requirement: OIDC 配置与状态端点
系统 SHALL 在未配置 `oidc.issuer` 时完全禁用 OIDC，且提供状态端点供前端判断。状态端点 SHALL 报告本地登录兜底是否生效；开启兜底时 OIDC 初始化失败 SHALL 降级启动而不是拒绝启动。

#### Scenario: 未配置 issuer 时 OIDC 禁用
- **WHEN** Hub 未配置 `oidc.issuer`
- **THEN** `GET /api/oidc/state` 返回 `{"enabled": false, "local_fallback": false}`
- **AND** 本地账号密码登录行为与未引入 OIDC 时一致

#### Scenario: 配置 issuer 时启用
- **WHEN** Hub 配置了 `oidc.issuer` 且 discovery 成功
- **THEN** `GET /api/oidc/state` 返回 `{"enabled": true}`
- **AND** `local_fallback` 与 `oidc.allow_local_login` 一致
- **AND** `GET /api/oidc/login` 生成 state+PKCE 并 302 跳转 IdP

#### Scenario: 未开启兜底时初始化失败拒绝启动
- **WHEN** 配置了 `oidc.issuer` 且 `oidc.allow_local_login=false`
- **AND** 启动期无法获取 IdP discovery 文档
- **THEN** Hub 拒绝启动并输出明确错误（fail-fast）

#### Scenario: 开启兜底时初始化失败降级启动
- **WHEN** 配置了 `oidc.issuer` 且 `oidc.allow_local_login=true`
- **AND** 启动期无法获取 IdP discovery 文档
- **THEN** Hub 以 WARN 日志继续启动
- **AND** `GET /api/oidc/state` 返回 `{"enabled": false, "local_fallback": false}`
- **AND** 本地账号密码登录可用
