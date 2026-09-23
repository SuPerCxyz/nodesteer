# Tasks

- [x] 创建 change 目录与 proposal/specs/design 文档
- [x] 新增 go-oidc + oauth2 依赖
- [x] 实现 internal/hub/auth/oidc.go（OIDC 客户端 + PKCE + 角色映射）
- [x] 扩展 auth.Manager（SetOIDC / SSOLogin / LocalLoginDisabled）
- [x] API 新增 /api/oidc/login、/api/oidc/callback、/api/oidc/state + handleLogin 拒绝
- [x] hubserver.Config + cmd/hub 配置解析接线
- [x] 前端 Login.tsx SSO 按钮 + oidc state 检测
- [x] oidc 单测（mock IdP httptest）
- [x] go test / vet / 前端 build 通过
- [x] KVM 端到端验证

