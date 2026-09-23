# web-ui Specification Delta

## MODIFIED Requirements

### Requirement: OIDC 登录页
系统 SHALL 在登录页根据 `/api/oidc/state` 决定展示 SSO 入口、本地登录表单或两者并存；`local_fallback=true` 时本地表单 SHALL 与 SSO 按钮同时保留（break-glass 兜底）。

#### Scenario: OIDC 启用时展示 SSO 入口
- **WHEN** `/api/oidc/state` 返回 `{"enabled": true, "local_fallback": false}`（或旧响应仅含 `enabled`）
- **AND** 用户打开登录页
- **THEN** 页面显示"使用 SSO 登录"按钮，不显示本地用户名/密码表单
- **WHEN** 用户点击按钮
- **THEN** 浏览器跳转到 `/api/oidc/login`

#### Scenario: 开启本地兜底时双入口并存
- **WHEN** `/api/oidc/state` 返回 `{"enabled": true, "local_fallback": true}`
- **AND** 用户打开登录页
- **THEN** 页面同时显示 SSO 按钮与本地用户名/密码表单
- **AND** 两者之间显示本地登录分隔文案
- **AND** 本地表单提交走既有本地登录流程，SSO 按钮仍可跳转 IdP

#### Scenario: OIDC 未启用时维持本地登录
- **WHEN** `/api/oidc/state` 返回 `{"enabled": false, "local_fallback": false}`
- **AND** 用户打开登录页
- **THEN** 页面显示本地用户名/密码表单，无 SSO 按钮
