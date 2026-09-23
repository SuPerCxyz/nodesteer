# Proposal: OIDC 本地登录兜底

## Why

Beta 部署要求「万一 OIDC 异常可以本地登录」。当前实现下这是做不到的：

1. **启动期**：配置了 `oidc.issuer` 但 IdP 不可达（discovery 失败）时 `hubserver.New`
   直接返回错误，Hub 拒绝启动，本地登录无从谈起。
2. **运行期**：`LocalLoginDisabled()` 只看「是否配置了 OIDC」，不看 IdP 健康状态；
   IdP 挂掉后 `/api/login` 恒 403，SSO 跳转也失败，**管理员被完全锁死**。
3. **前端**：OIDC 启用时登录页整体隐藏密码表单，即使后端放行也没有入口。
4. **部署接线**：`docker-compose.yml` 与 `.env.example` 未暴露任何 `CADENTRA_OIDC_*`
   变量，也没有独立部署文档说明 OIDC 配置方式与故障恢复。

## What Changes

- **MODIFY**（Hub 认证）：`OIDCConfig` 新增 `allow_local_login`（env
  `CADENTRA_OIDC_ALLOW_LOCAL_LOGIN`），默认 `false` 保持现有严格语义（OIDC 启用即禁本地）。
  开启后 OIDC 启用时本地账号密码登录仍可用（break-glass 并存模式）。
- **MODIFY**（启动降级）：`allow_local_login=true` 时，OIDC 初始化（discovery）失败
  不再中止启动，降级为「OIDC 未启用 + WARN 日志 + 本地登录可用」；`false` 时维持
  fail-fast。
- **MODIFY**（状态端点）：`GET /api/oidc/state` 响应新增 `local_fallback` 字段，
  指示 OIDC 启用且本地兜底开启（前端据此决定是否渲染本地表单）。
- **MODIFY**（前端）：`local_fallback=true` 时登录页在 SSO 按钮下方保留本地
  用户名/密码表单（含分隔文案，中英 i18n）。
- **ADD**（部署接线与文档）：compose 透传 7 个 `CADENTRA_OIDC_*` 变量；
  `.env.example` 新增 OIDC 段；新建 `docs/DEPLOYMENT.md`（env 全表、OIDC 两种
  配置方式、`role_mappings` 必须走 yaml 的警告、异常场景与恢复流程、启用后验证
  清单）；`hub.yaml.example` 补 `allow_local_login` 注释项。

## Capabilities

- **MODIFY**: hub-core（OIDC 本地登录门禁条件化、启动降级、状态端点字段）
- **MODIFY**: web-ui（登录页兜底双入口）
