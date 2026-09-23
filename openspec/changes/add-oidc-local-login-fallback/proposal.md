# Proposal: 登录方式互斥选择（allow_local_login）

## Why

既有的「OIDC + 本地兜底并存」语义带来不可控的安全面与启动耦合，需要改为明确的互斥选择器：

1. **语义混叠**：旧语义下 `allow_local_login` 只是 OIDC 启用时的 break-glass 兜底，
   「并存」使登录面同时暴露两套凭证入口，且 Go 零值 `false` 与产品期望的「默认本地优先」矛盾。
2. **启动耦合**：默认模式下配置了 `oidc.issuer` 就会做 discovery，IdP 不可达会牵连启动，
   与「本地模式永不因 OIDC 失败」的目标冲突；而旧降级分支又让 `false` 的 fail-fast 语义不纯粹。
3. **门禁不绝对**：旧 `LocalLoginDisabled()` 只看「是否配置了 OIDC」，在开关为 `false`（SSO 唯一）
   时若 OIDC 客户端缺失，密码登录会被错误放行。
4. **可观测性缺失**：失败登录与被 403 拒绝的登录没有审计记录，也没有任何失败限速。

## What Changes

- **MODIFY**（认证开关）：`allow_local_login` 改为**两种登录方式的互斥选择器**（`*bool` 三态，nil=true，默认本地模式）：
  - `true`（默认，含未显式配置）：本地密码登录可用；**OIDC 完全失效** —— 启动不做 discovery、
    不构造 OIDC 客户端（启动永不因 OIDC 失败）、`/api/oidc/login` 与 `/api/oidc/callback` 返回 404、
    `/api/oidc/state` 返回 `{"enabled": false, "local_fallback": true}`。
  - `false`：**密码登录一律 403（绝对语义，本地账户密码存在与否不影响）**；SSO 是唯一登录方式，
    `/api/oidc/state` 返回 `{"enabled": true, "local_fallback": false}`；
    未配 `oidc.issuer` → 启动拒绝并报明确错误；discovery 失败 → fail-fast 拒启（恢复=切回 true 重启）。
- **MODIFY**（启动逻辑）：本地模式跳过整个 `auth.NewOIDC`（连 client_id 校验都不做）；
  SSO-only 模式删除旧「降级继续」分支，改为无条件 fail-fast。
- **MODIFY**（状态端点/互斥门禁）：`enabled` 与 `local_fallback` 按互斥语义输出；
  `local_fallback` 字段名**保留**（旧前端渲染逻辑 `showLocalForm = !enabled || localFallback` 在两种新模式下恰好正确）；
  OIDC 路由统一经「SSO-only 模式且客户端就绪」判定，本地模式恒 404。
- **ADD**（登录审计与限速）：失败登录、403 拒绝均写审计（`Action=login`，成功/失败可区分，
  detail 不含密码）；同 IP+用户名连续失败达到阈值后递增延迟，成功后重置（无第三方依赖的最小实现）。
- **MODIFY**（配置接线）：`docker-compose.yml` 默认 `${OIDC_ALLOW_LOCAL_LOGIN:-true}`、
  `.env.example` 默认 `=true`、`hub.yaml.example` 注释改互斥语义 —— 三处默认值一致为 `true`，
  避免 compose 部署默认语义反转。
- **MODIFY**（env 解析）：`NODESTEER_OIDC_ALLOW_LOCAL_LOGIN` 三态解析 —— 未设置不覆盖 yaml/默认；
  设置则必须为合法 bool，非法值拒绝启动（安全开关不得静默忽略）。
- **MODIFY**（文档）：`docs/DEPLOYMENT.md`（env 表、配置节、验证清单、场景表、故障表、恢复流程
  统一改互斥语义，恢复动作=切回 `true` 重启）、`docs/TEST_PLAN.md` 登录用例同步。

## Capabilities

- **MODIFY**: hub-core（登录方式互斥门禁、状态端点、启动 fail-fast、登录审计与失败限速）
- **MODIFY**: web-ui（登录页互斥渲染：enabled→仅 SSO、local_fallback→仅本地表单，不再双入口并存）
