## Purpose

Hub 支持 OIDC 单点登录：Authorization Code Flow + PKCE，自动创建本地用户并按 role claim 映射角色；启用 OIDC 后禁用本地账号密码登录。

## ADDED Requirements

### Requirement: Hub OIDC 认证
系统 SHALL 支持标准 OIDC Authorization Code Flow + PKCE（公开客户端），通过 IdP 完成登录。

#### Scenario: 完整 SSO 登录流程
- **WHEN** 浏览器访问 `GET /api/oidc/login`
- **THEN** 302 跳转到 IdP 授权端点，query 含 state 与 code_challenge
- **WHEN** IdP 授权后回跳 `GET /api/oidc/callback?state=...&code=...`
- **THEN** state 校验通过、ID Token 经 JWKS 验证，按 username claim 自动创建本地用户
- **AND** 用户角色按 role_mappings 映射（未命中则默认 viewer）
- **AND** 返回有效 session，后续请求可用 Bearer token 通过 `/api/me`

#### Scenario: 配置 issuer 后本地登录被禁用
- **WHEN** Hub 配置了 `oidc.issuer`
- **AND** 客户端调用 `POST /api/login` 提交本地账号密码
- **THEN** 返回 403 与错误信息 "local login disabled"
- **AND** 不创建任何会话

#### Scenario: callback state 校验失败
- **WHEN** 客户端访问 `GET /api/oidc/callback` 且 state 与存储不匹配或已过期
- **THEN** 拒绝并 302 回前端登录页带错误标记
- **AND** 不创建会话

### Requirement: OIDC 配置与状态端点
系统 SHALL 在未配置 `oidc.issuer` 时完全禁用 OIDC，且提供状态端点供前端判断。

#### Scenario: 未配置 issuer 时 OIDC 禁用
- **WHEN** Hub 未配置 `oidc.issuer`
- **THEN** `GET /api/oidc/state` 返回 `{"enabled": false}`
- **AND** 本地账号密码登录行为与未引入 OIDC 时一致

#### Scenario: 配置 issuer 时启用
- **WHEN** Hub 配置了 `oidc.issuer`
- **THEN** `GET /api/oidc/state` 返回 `{"enabled": true}`
- **AND** `GET /api/oidc/login` 生成 state+PKCE 并 302 跳转 IdP
