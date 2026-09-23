# NodeSteer 部署指南（Docker Compose）

面向 beta / 生产的 Hub 部署说明。Native 部署见 README「CI 构建产物」与 `packaging/systemd/`。

## 1. 快速开始

```bash
cp .env.example .env
# 编辑 .env：至少设置 ADMIN_PASSWORD 与 REGISTRATION_TOKEN，并按部署网络修改 HUB_BASE_URL
docker compose up -d --build
curl -fsS http://localhost:8080/healthz   # 期望 200
```

Compose 默认同时提供 Hub 和一个 Docker Agent。数据保存在 `hub-data`（Hub 数据与发布包）与 `agent-data`（Agent 持久化状态）卷中，删除容器不丢数据；**删除卷才会丢数据**，升级前按第 5 节备份。

## 2. 环境变量全表

`.env`（compose 宿主侧变量 → 容器内 `CADENTRA_*`）：

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `ADMIN_PASSWORD` | **是** | 无（缺失即拒绝启动） | 初始管理员口令；空口令 Hub 拒绝启动 |
| `ADMIN_USERNAME` | 否 | `admin` | 初始管理员用户名 |
| `REGISTRATION_TOKEN` | **是** | `changeme` | Agent 首次注册 Token，**必须改掉默认值** |
| `HUB_BASE_URL` | **是** | `http://localhost:8080` | 对外可达的 Web/API 地址（Artifact 下载、纳管命令、OIDC 回跳均依赖它） |
| `HUB_WEB_PORT` | 否 | `8080` | 宿主映射的 Web 端口 |
| `HUB_GATEWAY_PORT` | 否 | `8443` | 宿主映射的 Agent Gateway 端口 |
| `HUB_GATEWAY_BASE_URL` | 否 | 空 | 跨网络纳管时设为 Agent 可达的 `wss://` 地址 |
| `MAX_FILE_TRANSFER_BYTES` | 否 | `10737418240`（10 GiB） | 单文件传输上限 |
| `OIDC_ISSUER` | 否 | 空 | 留空 = 不启用 OIDC（纯本地认证）；见第 3 节 |
| `OIDC_CLIENT_ID` | 否 | 空 | 启用 OIDC 时必填 |
| `OIDC_REDIRECT_URL` | 否 | `<HUB_BASE_URL>/api/oidc/callback` | IdP 侧需登记此回跳地址 |
| `OIDC_USERNAME_CLAIM` | 否 | `preferred_username` | 回退顺序：email、sub |
| `OIDC_ROLE_CLAIM` | 否 | `groups` | — |
| `OIDC_DEFAULT_ROLE` | 否 | `viewer` | **无 role_mappings 时所有 SSO 用户落此角色**，勿设 `administrator` |
| `OIDC_ALLOW_LOCAL_LOGIN` | 否 | `false` | break-glass 兜底，见第 3、4 节 |

完整列表以 `.env.example` 为准；容器内变量名统一为 `CADENTRA_` 前缀（如 `CADENTRA_OIDC_ISSUER`），由 Hub 进程读取。

## 3. 配置 OIDC

### 方式 A：纯环境变量（`.env`）

适合无角色映射需求的简单 IdP。配置 `OIDC_ISSUER` + `OIDC_CLIENT_ID` 即启用：

```bash
OIDC_ISSUER=https://idp.example.com/realms/beta
OIDC_CLIENT_ID=cadentra
OIDC_DEFAULT_ROLE=viewer
OIDC_ALLOW_LOCAL_LOGIN=true      # beta 建议开启，见第 4 节
```

### 方式 B：hub.yaml（**需要 role_mappings 时必须用这个**）

> ⚠️ **角色映射 `role_mappings` 只支持 hub.yaml，环境变量无法表达。**
> 纯 env 部署下所有 SSO 用户都会落到 `OIDC_DEFAULT_ROLE`（默认 `viewer`）——
> 若 IdP 里没有能拿到 administrator 的途径，**启用 OIDC 后将无人能管理 Hub**。

将 `packaging/systemd/hub.yaml.example` 的 `oidc:` 块取消注释并挂载进容器：

```yaml
oidc:
  issuer: "https://idp.example.com/realms/beta"
  client_id: "cadentra"
  role_mappings:
    admins: "administrator"
    ops: "operator"
  default_role: "viewer"
  allow_local_login: true
```

```bash
# compose.yml 的 hub.services 下增加：
#   volumes:
#     - ./hub.yaml:/etc/cadentra/hub.yaml:ro
#   command: ["--config", "/etc/cadentra/hub.yaml"]
```

### 启用后的验证清单

1. `GET /api/oidc/state` 返回 `{"enabled": true, ...}`（开启兜底时 `local_fallback: true`）。
2. 登录页显示「使用 SSO 登录」按钮（开启兜底时本地表单同时可见）。
3. 点击 SSO 按钮 302 跳转到 IdP，回调后进入仪表盘。
4. SSO 用户的 `/api/me` 角色符合预期（role_mappings 生效验证）。
5. OIDC 启用且 `allow_local_login=false` 时，`POST /api/login` 返回 403。
6. （开启兜底时）本地账号密码登录仍可成功进入。

## 4. OIDC 异常场景与恢复（break-glass）

`OIDC_ALLOW_LOCAL_LOGIN=true`（yaml: `oidc.allow_local_login: true`）是 beta 期的
兜底开关，默认 `false` 保持「启用 OIDC 即禁本地」的严格语义。

| 场景 | `allow_local_login=false`（默认） | `allow_local_login=true` |
|---|---|---|
| A. 配了 issuer 但 IdP 启动时不可达 / discovery 失败 | Hub **拒绝启动**（fail-fast），日志 `init oidc: ...` | Hub 以 WARN 降级启动，日志 `oidc init failed, continuing with local login fallback`；`/api/oidc/state` 为 `enabled=false`，本地登录可用 |
| B. Hub 正常运行后 IdP 宕机 | SSO 跳转失败且 `/api/login` 恒 **403**，所有账号被锁死 | SSO 跳转失败，但**本地表单保留**（`local_fallback=true`），用本地管理员继续登录 |
| C. OIDC 配置错误（client_id 缺失、role_mappings 非法） | 拒绝启动 | 同 A：降级启动 + 本地登录可用 |

**恢复流程（场景 A/C 默认模式下 Hub 起不来时）：**

```bash
# 1. 在 .env 中关闭 OIDC（或修好 IdP 可达性）
sed -i 's/^OIDC_ISSUER=.*/# OIDC_ISSUER=/' .env
# 2. 重建容器环境变量并重启
docker compose up -d hub
docker compose logs --tail 100 hub   # 确认无 init oidc 错误
# 3. 本地密码登录恢复后，排查 IdP 再择机重新启用
```

> 不要在生产长期用 `default_role=administrator` + 关闭 role_mappings 来"图省事"；
> 那等于任何能通过 IdP 的人都拿到管理员。正确姿势是走方式 B 配 role_mappings。

## 5. 升级与备份

```bash
# 备份（Hub 全部状态在卷里：SQLite + artifacts）
docker compose stop hub
docker run --rm -v nodesteer_hub-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/hub-data-$(date +%F).tar.gz -C /data .
docker compose start hub

# 升级
git pull && docker compose up -d --build
```

数据库 schema 由 Hub 启动时自动迁移（`internal/store/migrate.go`），无需手工执行 SQL。

## 6. 常见启动失败

| 日志特征 | 原因 | 处理 |
|---|---|---|
| `ADMIN_PASSWORD must be set`（compose 拒绝解析） | `.env` 未设 `ADMIN_PASSWORD` | 设置后重新 `up` |
| `admin password is required`（exit 1） | 口令为空 | 同上 |
| `init oidc: ...`（exit 1） | OIDC discovery 失败且未开兜底 | 见第 4 节恢复流程 |
| `load bundled agent payloads failed`（WARN） | 非打包构建缺内置 Agent | 用 `make build` 或官方镜像 |
