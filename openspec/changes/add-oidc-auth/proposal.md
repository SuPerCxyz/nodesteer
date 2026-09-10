# Proposal: OIDC Authentication for Hub Web UI

## Why

Cadentra Hub 目前仅支持本地用户名/密码登录（`internal/hub/auth`，bcrypt + 内存 session）。`docs/PRODUCT.md` 将 LDAP/OIDC/SSO 列为后续规划能力，且架构承诺"不得阻碍未来加入这些能力"。用户要求实现 OIDC 认证，使 Hub Web UI 支持标准 OIDC Authorization Code Flow + PKCE 单点登录，同时保留现有 RBAC 与用户模型。

## What Changes

- **ADD**（Hub 认证）：
  - 新增 `internal/hub/auth/oidc.go`：OIDC 客户端（基于 `coreos/go-oidc/v3` + `golang.org/x/oauth2`），支持 issuer discovery、Authorization Code Flow + PKCE、ID Token 签名验证（JWKS）、claims 提取
  - 自动创建本地用户：首次 SSO 登录按 username claim 自动创建本地用户（默认 viewer 角色），后续登录直接映射；自动创建用户纳入现有 `models.User` / store / `/api/users` 管理
  - 角色映射：按 role claim（默认 `groups`）+ `role_mappings` 配置映射（如 `{admins: administrator, ops: operator}`），未命中默认 viewer
  - OIDC 启用后**禁用本地账号密码登录**（`/api/login` 返回 403）；未配置 issuer 时行为完全不变（向后兼容）
- **ADD**（API）：
  - `GET /api/oidc/login`：生成 state + PKCE verifier 并 302 跳转 IdP
  - `GET /api/oidc/callback`：校验 state，code→token→验证→建 session→302 回前端
  - `GET /api/oidc/state`：返回是否启用 OIDC（前端判断）
- **ADD**（配置）：hub.yaml + 环境变量新增 `oidc` 配置块（issuer / client_id / redirect_url / scopes / username_claim / role_claim / role_mappings）
- **ADD**（前端）：Login 页在 OIDC 启用时显示"使用 SSO 登录"按钮
- 会话复用现有 `auth.Manager` session/token 机制；不改变 RBAC 模型、users API、审计

## Capabilities

- **ADD**: hub-core（OIDC 认证、SSO 会话、角色映射、本地登录门禁）
- **ADD**: web-ui（登录页 SSO 入口）
