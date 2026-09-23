# web-ui Specification Delta

## MODIFIED Requirements

### Requirement: OIDC 登录页
系统 SHALL 根据 `/api/oidc/state` **互斥**展示 SSO 入口或本地登录表单：`enabled=true` 时仅展示 SSO 入口；`local_fallback=true`（本地模式，含默认）时仅展示本地表单；两者不得并存。

#### Scenario: OIDC 启用时展示 SSO 入口
- **WHEN** `/api/oidc/state` 返回 `{"enabled": true, "local_fallback": false}`
- **AND** 用户打开登录页
- **THEN** 页面显示"使用 SSO 登录"按钮，不显示本地用户名/密码表单
- **WHEN** 用户点击按钮
- **THEN** 浏览器跳转到 `/api/oidc/login`

#### Scenario: OIDC 未启用时维持本地登录
- **WHEN** `/api/oidc/state` 返回 `{"enabled": false, "local_fallback": true}`（或旧响应仅含 `enabled: false`）
- **AND** 用户打开登录页
- **THEN** 页面显示本地用户名/密码表单，无 SSO 按钮
