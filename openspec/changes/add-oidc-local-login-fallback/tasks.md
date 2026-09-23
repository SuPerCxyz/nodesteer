# Tasks

## 1. 后端认证与启动降级

- [x] 1.1 `internal/hub/auth/oidc.go`：`OIDCConfig` 增加 `AllowLocalLogin bool`（yaml `allow_local_login`）与 `(*OIDC).LocalFallback()`（nil 安全）
- [x] 1.2 `internal/hub/auth/auth.go`：`LocalLoginDisabled()` 改为 `Enabled && !LocalFallback`
- [x] 1.3 `internal/hubserver/hub.go`：初始化失败时若 `AllowLocal_login` 允许则 WARN 降级继续启动，否则维持 fail-fast
- [x] 1.4 `internal/hub/api/api.go`：`/api/oidc/state` 响应增加 `local_fallback`
- [x] 1.5 `cmd/hub/main.go`：`applyEnv` 解析 `CADENTRA_OIDC_ALLOW_LOCAL_LOGIN`（bool）

## 2. 后端测试

- [x] 2.1 auth 包：`LocalLoginDisabled` 开关两态（含 nil/未启用边界）
- [x] 2.2 hubserver：启动期 discovery 失败 × 开关两态（降级成功启动 / fail-fast 拒绝）
- [x] 2.3 hubserver：`allow_local_login=true` 时 `/api/login` 200 且 state 返回 `enabled=true, local_fallback=true`

## 3. 前端登录页

- [x] 3.1 `sign-in/index.tsx`：state 类型加 `local_fallback?`；`local_fallback=true` 时 SSO 按钮与本地表单同时渲染，含分隔文案
- [x] 3.2 `locales/{zh,en}.ts`：新增分隔文案 key，保持中英对齐
- [x] 3.3 测试：Playwright `P01-08`（enabled+local_fallback 双入口可见）；vitest 覆盖可见性判定

## 4. 部署接线与文档

- [x] 4.1 `docker-compose.yml`：hub 服务透传 7 个 `CADENTRA_OIDC_*` 变量
- [x] 4.2 `.env.example`：新增带注释的 OIDC 段（含 break-glass 警告与 default_role 提醒）
- [x] 4.3 `packaging/systemd/hub.yaml.example`：`oidc` 块补 `allow_local_login` 注释项与降级行为说明
- [x] 4.4 新建 `docs/DEPLOYMENT.md`：compose 步骤、env 全表、OIDC env/yaml 两种配置方式（yaml 含 `role_mappings` 必须走文件的醒目警告）、异常场景与恢复流程、启用 OIDC 后的验证清单
- [x] 4.5 `README.md`：部署段链接 DEPLOYMENT.md，文档索引补一行

## 5. 验证

- [x] 5.1 `go vet` + `go test ./...` 全过
- [x] 5.2 前端 `lint` / `prettier --check` / `build` / `vitest` 全过，`knip` 死依赖保持 0
- [x] 5.3 `openspec validate add-oidc-local-login-fallback` 通过
- [x] 5.4 compose 配置校验（`docker compose config`，无 docker 时记录未验证）
