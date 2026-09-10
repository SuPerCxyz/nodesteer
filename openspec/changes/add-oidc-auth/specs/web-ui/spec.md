## Purpose

登录页在 Hub 启用 OIDC 时展示"使用 SSO 登录"入口并隐藏本地表单。

## ADDED Requirements

### Requirement: OIDC 登录页
系统 SHALL 在登录页根据 `/api/oidc/state` 决定展示 SSO 入口或本地登录表单。

#### Scenario: OIDC 启用时展示 SSO 入口
- **WHEN** `/api/oidc/state` 返回 `{"enabled": true}`
- **AND** 用户打开登录页
- **THEN** 页面显示"使用 SSO 登录"按钮，不显示本地用户名/密码表单
- **WHEN** 用户点击按钮
- **THEN** 浏览器跳转到 `/api/oidc/login`

#### Scenario: OIDC 未启用时维持本地登录
- **WHEN** `/api/oidc/state` 返回 `{"enabled": false}`
- **AND** 用户打开登录页
- **THEN** 页面显示本地用户名/密码表单，无 SSO 按钮
