# Tasks

> 本轮范围：后端 + 配置 + 文档 + spec delta。
> 前端互斥渲染由 S2 负责（`web/**`），`.e2e` 记录由主 Agent 负责，主 spec `openspec/specs/**` 不在本轮改写。

## 1. 后端认证开关与启动逻辑

- [x] 1.1 `internal/hub/auth/oidc.go`：`AllowLocalLogin` 改 `*bool`（nil=true 默认本地模式，yaml tag 不变）；新增 `OIDCConfig.LocalLoginAllowed()`；`DefaultOIDCConfig` 同步注释；`LocalFallback()` 按新语义重定义（nil 安全，本地模式为 true）
- [x] 1.2 `internal/hub/auth/auth.go`：`Manager` 增加 `localAllowed` 开关（`SetLocalLoginAllowed`/`LocalLoginAllowed`，`New` 默认 true）；`LocalLoginDisabled()` 改为只取决于开关（不再依赖 `Enabled()`，false 模式绝对禁用）
- [x] 1.3 `internal/hubserver/hub.go`：true 模式跳过整个 `auth.NewOIDC`（不做 discovery、不构造客户端）；false 模式缺 `oidc.issuer` 拒启（明确错误）、discovery 失败 fail-fast（删除降级分支）；开关注入 Manager
- [x] 1.4 `internal/hub/api/api.go`：新增 `oidcActive()`（SSO-only 模式且客户端就绪）；`/api/oidc/state` 输出互斥语义（`enabled`=SSO 生效、`local_fallback`=本地可用，字段保留）；`/api/oidc/login`、`/api/oidc/callback` 本地模式 404；403 条件走开关

## 2. 登录审计与失败限速（建议补全 B，最小版）

- [x] 2.1 `internal/hub/api/api.go`：失败登录写审计（`Action=login`、成功/失败可区分、detail 不含密码）；403 分支也记审计；成功登录 detail=local
- [x] 2.2 `internal/hub/api/api.go`：同 IP+用户名连续失败达阈值后递增延迟（无第三方依赖），登录成功后重置

## 3. 配置接线（验收必查：三处默认值一致为 true）

- [x] 3.1 `docker-compose.yml`：`OIDC_ALLOW_LOCAL_LOGIN` 默认 `${OIDC_ALLOW_LOCAL_LOGIN:-true}`，注释改互斥语义
- [x] 3.2 `.env.example`：`OIDC_ALLOW_LOCAL_LOGIN=true` + 互斥语义与恢复说明
- [x] 3.3 `packaging/systemd/hub.yaml.example`：`allow_local_login` 注释与示例值改互斥语义（默认 true、false 模式约束与恢复）
- [x] 3.4 `cmd/hub/main.go`：`applyEnv` 三态解析（未设置不覆盖；设置则必须合法 bool，非法值拒绝启动）

## 4. 文档

- [x] 4.1 `docs/DEPLOYMENT.md`：env 表、第 3 节（互斥矩阵 + 验证清单，含权限一致验收）、第 4 节场景表与恢复流程（切回 true 重启）、第 6 节故障表全部改互斥语义
- [x] 4.2 `docs/TEST_PLAN.md`：登录用例改互斥语义断言

## 5. Go 测试

- [x] 5.1 `internal/hub/auth/local_login_fallback_test.go`：三态 + `LocalLoginDisabled` 新语义（false 模式绝对禁用、不依赖 OIDC 注入）
- [x] 5.2 `internal/hubserver/oidc_fallback_test.go`：默认 true 跳过 discovery（坏 issuer 也启动）且登录 200、state/login 404；显式 false + 坏 issuer fail-fast；显式 false 未配 issuer 拒启
- [x] 5.3 `internal/hubserver/oidc_api_test.go`：`TestSSOOnlyModeLocalLoginForbidden`（显式 false 保留 403 断言）；默认模式 `local_fallback=true`；登录成功/失败审计断言；403 审计断言；失败限速延迟断言
- [x] 5.4 `internal/hubserver/oidc_parity_test.go`：权限等价用例（本地登录 vs mock IdP SSO 登录，同角色 → `/api/me` 角色一致 + 受保护端点允许/拒绝逐点一致）
- [x] 5.5 `cmd/hub/main_test.go`：env 三态 + yaml 保留 + 非法值报错

## 6. Spec 与验证

- [x] 6.1 重写 `proposal.md`、`specs/hub/core/spec.md`（MODIFIED 保留既有 scenario 名并改条件，新增互斥/权限一致/审计限速/缺 issuer 拒启 scenario）、`specs/web-ui/spec.md`（互斥渲染，删除并存/双入口 scenario）
- [x] 6.2 `openspec validate add-oidc-local-login-fallback --type change` 通过
- [x] 6.3 `go vet ./...` 与 `go test ./...` 全绿（本轮改动范围内；见最终报告的并行改动说明）
- [x] 6.4 `openspec validate --all` 全绿
- [x] 6.5 `docker compose config` 通过（ADMIN_PASSWORD=test）；grep 确认 compose/yaml/env 三处默认值一致为 true
