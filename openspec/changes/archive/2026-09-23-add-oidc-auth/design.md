# Design: OIDC Authentication

## 概述

在 Hub 的现有认证层（`internal/hub/auth`）旁新增 OIDC 支持。复用现有 `auth.Manager` 的 session/token 机制，新增一个 OIDC 客户端负责协议握手与 claims 提取，最终仍通过 `auth.Manager` 创建会话，保证 RBAC、审计、前端 `useAuth` 完全复用。

## 组件

### 1. `internal/hub/auth/oidc.go` — OIDC 客户端

```go
type OIDCConfig struct {
    Issuer        string
    ClientID      string
    RedirectURL   string
    Scopes        []string
    UsernameClaim string   // 默认 preferred_username
    RoleClaim     string   // 默认 groups
    RoleMappings  map[string]string // {组名: NodeSteer角色}
    DefaultRole   string   // 默认 viewer
}

type OIDC struct {
    cfg      OIDCConfig
    provider *oidc.Provider   // go-oidc
    verifier *oidc.IDTokenVerifier
    oauthCfg oauth2.Config
    mu       sync.Mutex
    states   map[string]*oidcState // state -> {verifier, expires}
}

type oidcState struct {
    verifier string
    expires  time.Time
}
```

- `NewOIDC(cfg)`：`oidc.NewProvider(ctx, issuer)` + `provider.Verifier(...)`，构建 oauth2.Config。
- `Enabled() bool`：issuer 非空。
- `AuthCodeURL() (string, error)`：生成随机 state + PKCE verifier（S256），存 states（TTL 10min），返回跳转 URL。
- `Exchange(ctx, state, code) (username, role string, err error)`：校验 state 并删除，oauth2 code→token，`verifier.Verify` ID Token，提取 username/role。

### 2. `internal/hub/auth/auth.go` — Manager 扩展

- 新增字段 `oidc *OIDC`、`localDisabled bool`。
- `SetOIDC(o *OIDC)`：设置 oidc；localDisabled = o.Enabled()。
- `SSOLogin(ctx, username, role)`：GetUserByUsername，不存在则 CreateUser（PasswordHash 为空），创建 Session。
- `LocalLoginDisabled() bool`。
- `Login()` 保持不变（由 API 层检查 localDisabled）。

### 3. `internal/hub/api/api.go` — 新端点

- `GET /api/oidc/login`：`s.oidc.AuthCodeURL()` → 302 Location。
- `GET /api/oidc/callback`：query code+state → `Exchange` → `authMgr.SSOLogin` → 302 `{base}/#/oidc-success`，失败 302 `{base}/#/login?error=oidc_failed`。
- `GET /api/oidc/state`：`{"enabled": bool}`。
- `handleLogin`：若 `LocalLoginDisabled()` → 403 "local login disabled"。
- Server 增加 `oidc *auth.OIDC` 字段与 `SetOIDC`。

### 4. `internal/hubserver/hub.go` + `cmd/hub/main.go` — 接线

- hubserver.Config 增加 `OIDC auth.OIDCConfig`（零值 = 禁用）。
- New() 中若 cfg.OIDC.Issuer 非空则 `authMgr.SetOIDC(auth.NewOIDC(...))`，并把 OIDC 注入 apiServer。
- main.go Config 增加 oidc 嵌套结构 + yaml/env 解析（`CADENTRA_OIDC_ISSUER` 等）。

### 5. 前端 `web/src/pages/Login.tsx`

- useEffect 请求 `/api/oidc/state`；`enabled` 时显示 SSO 按钮、隐藏本地表单。
- SSO 按钮 `window.location.href = '/api/oidc/login'`。
- callback 成功后跳 `/#/oidc-success`：登录页检测该 query 显示成功提示（或直接跳转首页，因为 token 已在 cookie/localStorage）。

### 6. 会话传递

前端 SPA 用 `Authorization: Bearer`。OIDC 成功后 Hub 需把 token 交回前端。方案：callback 成功后响应 HTML/302 时在 URL fragment 带 `#/login?token=...`？不安全（日志泄露）。改用：callback 302 → `/{base}/#/oidc-callback`，前端在该路由读取。更稳妥方案：callback 直接把 session token 写入 cookie（HttpOnly 不行，前端需要读）。

**决策**：复用现有前端 token 机制，callback 302 回 `{base}/#/oidc?token=<sessionToken>&user=...`（fragment 不发送到服务器，避免日志泄露；SPA 读取后 `setToken` 存入 localStorage）。这符合现有前端 `setToken` 用法，改动最小。

## 测试策略

- `internal/hub/auth/oidc_test.go`：httptest 模拟 IdP（discovery 文档、JWKS、token 端点），用 go-oidc 测试辅助生成签名的 ID Token。覆盖：完整 Exchange 成功、PKCE 校验失败、role 映射（命中/未命中）、username fallback。
- `internal/hub/api`：/api/oidc/state 的 enabled 分支；handleLogin 在 localDisabled 时 403。
- KVM：部署 mock IdP（同一 VM 简单 HTTP 服务）+ hub 配置 OIDC → 浏览器 SSO 流程验证。

## 风险

- 依赖新增（go-oidc、oauth2）需 go get，network 可用。
- go-oidc 需要 IdP 支持 PKCE 与 discovery；测试 IdP 需模拟。
- callback 是公开端点，但 state+PKCE 提供 CSRF 防护。
