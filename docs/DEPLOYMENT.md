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

`.env`（compose 宿主侧变量 → 容器内 `NODESTEER_*`）：

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `ADMIN_PASSWORD` | **是** | 无（缺失即拒绝启动） | 初始管理员口令；空口令 Hub 拒绝启动 |
| `ADMIN_USERNAME` | 否 | `admin` | 初始管理员用户名 |
| `REGISTRATION_TOKEN` | **是** | `changeme` | Agent 首次注册 Token，**必须改掉默认值** |
| `HUB_BASE_URL` | **是** | `http://localhost:8080` | 对外可达的 Web/API 地址（Artifact 下载、纳管命令、OIDC 回跳均依赖它） |
| `HUB_WEB_PORT` | 否 | `8080` | 宿主映射的 Web 端口；单入口模式下 Gateway 同走此端口（对外仅需暴露这一个） |
| `HUB_GATEWAY_PORT` | 否 | `8443` | **仅双端口模式使用**：单入口模式无独立 Gateway 端口（compose 已注释 8443 映射） |
| `HUB_GATEWAY_BASE_URL` | 否（**反代部署必填**） | 空 | **反代部署必填**（对外地址，通常= `HUB_BASE_URL`）；直连且对外端口=容器端口时可空 |
| `MAX_FILE_TRANSFER_BYTES` | 否 | `10737418240`（10 GiB） | 单文件传输上限 |
| `OIDC_ISSUER` | 否 | 空 | `OIDC_ALLOW_LOCAL_LOGIN=false` 时**必填**；true 模式下忽略（OIDC 失效），见第 3 节 |
| `OIDC_CLIENT_ID` | 否 | 空 | SSO 模式（`OIDC_ALLOW_LOCAL_LOGIN=false`）时必填 |
| `OIDC_REDIRECT_URL` | 否 | `<HUB_BASE_URL>/api/oidc/callback` | IdP 侧需登记此回跳地址 |
| `OIDC_USERNAME_CLAIM` | 否 | `preferred_username` | 回退顺序：email、sub |
| `OIDC_ROLE_CLAIM` | 否 | `groups` | — |
| `OIDC_DEFAULT_ROLE` | 否 | `viewer` | **无 role_mappings 时所有 SSO 用户落此角色**，勿设 `administrator` |
| `OIDC_ALLOW_LOCAL_LOGIN` | 否 | `true` | 登录方式互斥选择器：`true`=本地密码登录（OIDC 失效）；`false`=SSO 唯一登录（密码登录一律 403），见第 3、4 节 |

完整列表以 `.env.example` 为准；容器内变量名统一为 `NODESTEER_` 前缀（如 `NODESTEER_OIDC_ISSUER`），由 Hub 进程读取。

> **HTTPS 口径**：`web_tls_*` / `gateway_tls_*` 两套 TLS 配置在反代部署下**恒为空**，HTTPS 一律由反向代理终结，Hub 侧全程明文；详见第 7 节。

## 3. 配置 OIDC

`OIDC_ALLOW_LOCAL_LOGIN` 是两种登录方式的**互斥选择器**（yaml：`oidc.allow_local_login`，默认 `true`）：

| 值 | 登录方式 | 启动行为 |
|---|---|---|
| `true`（默认，含未显式配置） | 本地密码登录可用；**OIDC 完全失效** | 不做 discovery、不构造 OIDC 客户端，**永不因 OIDC 失败**；`/api/oidc/login` 返回 404 |
| `false` | **SSO 唯一登录方式**，密码登录一律 **403**（与本地账户是否存在无关） | 未配 `OIDC_ISSUER` → 拒启；discovery 失败 → fail-fast 拒启 |

**启用 SSO 必须显式设 `OIDC_ALLOW_LOCAL_LOGIN=false`**：只配置 `OIDC_ISSUER` 而开关保持默认 `true` 时，SSO 不会生效。

### 方式 A：纯环境变量（`.env`）

适合无角色映射需求的简单 IdP。配置 `OIDC_ALLOW_LOCAL_LOGIN=false` + `OIDC_ISSUER` + `OIDC_CLIENT_ID` 即启用：

```bash
OIDC_ALLOW_LOCAL_LOGIN=false   # SSO 唯一登录（密码登录一律 403）
OIDC_ISSUER=https://idp.example.com/realms/beta
OIDC_CLIENT_ID=nodesteer
OIDC_DEFAULT_ROLE=viewer
```

### 方式 B：hub.yaml（**需要 role_mappings 时必须用这个**）

> ⚠️ **角色映射 `role_mappings` 只支持 hub.yaml，环境变量无法表达。**
> 纯 env 部署下所有 SSO 用户都会落到 `OIDC_DEFAULT_ROLE`（默认 `viewer`）——
> 若 IdP 里没有能拿到 administrator 的途径，**启用 SSO 后将无人能管理 Hub**。
> 且 SSO 模式（`allow_local_login: false`）下本地密码登录不可用，没有退路。

将 `packaging/systemd/hub.yaml.example` 的 `oidc:` 块取消注释并挂载进容器：

```yaml
oidc:
  issuer: "https://idp.example.com/realms/beta"
  client_id: "nodesteer"
  role_mappings:
    admins: "administrator"
    ops: "operator"
  default_role: "viewer"
  allow_local_login: false   # SSO 唯一登录；省略或 true 则本地模式（OIDC 失效）
```

```bash
# compose.yml 的 hub.services 下增加：
#   volumes:
#     - ./hub.yaml:/etc/nodesteer/hub.yaml:ro
#   command: ["--config", "/etc/nodesteer/hub.yaml"]
```

### 启用后的验证清单

1. `GET /api/oidc/state`：SSO 模式返回 `{"enabled": true, "local_fallback": false}`；本地模式（默认）返回 `{"enabled": false, "local_fallback": true}`。
2. 登录页互斥渲染：SSO 模式仅显示「使用 SSO 登录」按钮；本地模式仅显示本地表单。
3. SSO 模式下点击 SSO 按钮 302 跳转到 IdP，回调后进入仪表盘。
4. 两种方式登录成功后权限完全一致：同角色用户经本地登录与 SSO 登录，`/api/me` 角色一致，受保护端点（如 `GET /api/scripts` 允许、`GET /api/users` 拒绝）行为逐点一致。
5. SSO 模式下 `POST /api/login` 返回 403（无论本地账户/密码是否存在），并写入审计。
6. 本地模式下 `/api/oidc/login` 返回 404，本地账号密码登录正常进入。

## 4. 登录方式切换与故障恢复

`allow_local_login`（env：`OIDC_ALLOW_LOCAL_LOGIN`）是互斥选择器，**默认 `true`**。
两种模式的故障语义完全不同，恢复动作统一是：**切回 `true` 并重启（本地密码登录立即恢复）**。

| 场景 | `allow_local_login=true`（默认） | `allow_local_login=false`（SSO 唯一登录） |
|---|---|---|
| A. 配了 issuer 但 IdP 启动时不可达 / discovery 失败 | **无影响**：本地模式不做 discovery、不构造 OIDC 客户端，正常启动，本地登录可用 | Hub **拒绝启动**（fail-fast），日志 `init oidc: ...` |
| B. Hub 正常运行后 IdP 宕机 | **无影响**：本地登录可用（SSO 本就失效） | SSO 跳转失败且 `/api/login` 恒 **403**，所有账号被锁死 |
| C. 未配置 `OIDC_ISSUER` | 正常启动，纯本地认证 | Hub **拒绝启动**，日志 `allow_local_login=false requires oidc.issuer ...` |
| D. OIDC 配置错误（client_id 缺失、role_mappings 非法） | **无影响**：本地模式不校验 OIDC 配置 | 拒绝启动（fail-fast） |

**恢复流程（false 模式下 Hub 起不来或 IdP 宕机导致锁死时）：**

```bash
# 1. 切回本地模式（true 是默认值，也可直接删除该行）
sed -i 's/^OIDC_ALLOW_LOCAL_LOGIN=.*/OIDC_ALLOW_LOCAL_LOGIN=true/' .env
# 2. 重建容器环境变量并重启
docker compose up -d hub
docker compose logs --tail 100 hub   # 确认进入 local login mode，无 init oidc 错误
# 3. 本地密码登录恢复后，排查 IdP 再择机切回 false 重新启用 SSO
```

> 不要在生产长期用 `default_role=administrator` + 关闭 role_mappings 来"图省事"；
> 那等于任何能通过 IdP 的人都拿到管理员。正确姿势是走方式 B 配 role_mappings。

## 5. 升级与备份

### 5.1 常规备份与升级

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

### 5.2 Hub/Agent 协同升级（Task/Schedule 模型与协议版本变更）

包含 Task/Schedule 模型重构与条件归属拆分的版本会把 Hub-Agent 协议版本由 1 提升到 2、再提升到 3：Task 不再承载目标与执行策略，Schedule 成为任务、目标与策略的唯一绑定入口；Task 只保留本地条件，Schedule 承载远程条件；`executions` 增加 `schedule_id`/`schedule_revision`。新 Hub 拒绝旧协议版本 Agent，旧 Hub 也不识别新 Agent，因此 Hub 与 Agent 必须协同升级。

升级顺序固定为：

```text
备份 Hub DB 与所有 Agent 数据
  ↓
停止 Hub 与所有 Agent
  ↓
升级并启动 Hub（自动执行 schema 与数据迁移）
  ↓
确认 Hub 健康检查与迁移结果
  ↓
升级并启动所有 Agent
  ↓
验证同步、调度与执行记录
```

Docker Compose 示例（卷名以 `docker volume ls` 实际结果为准；单机 Native Agent 备份 `/var/lib/nodesteer`）：

```bash
# 1. 停止并备份
docker compose stop hub agent
docker run --rm -v nodesteer_hub-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/hub-data-$(date +%F-%H%M).tar.gz -C /data .
docker run --rm -v nodesteer_agent-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/agent-data-$(date +%F-%H%M).tar.gz -C /data .

# 2. 构建新版本
docker compose build

# 3. 先升级 Hub，确认迁移成功后再升级 Agent
docker compose up -d hub
docker compose logs --tail 200 hub
curl -fsS http://localhost:8080/healthz
curl -fsS http://localhost:8080/readyz

docker compose up -d agent
```

Native Agent：

```bash
systemctl stop nodesteer-agent
tar czf /backup/nodesteer-agent-$(date +%F-%H%M).tar.gz -C /var/lib nodesteer
install bin/nodesteer-agent /usr/local/bin/nodesteer-agent
systemctl restart nodesteer-agent
systemctl status nodesteer-agent --no-pager
```

迁移内容：

- `executions` 新增可空 `schedule_id`/`schedule_revision` 及必要索引；`created_at` 沿用既有列并回填空值，同时在 API 与协议中对外暴露。历史记录 `schedule_id` 为空、`schedule_revision` 为 0。
- 旧 Task 的 `target`、`timeout`、`retry`、`run_user`、`offline_policy` 复制到其全部 Schedule。
- Task 定义 JSON 移除目标与策略字段；Schedule 定义 JSON 补齐 `name`、`target`、`timeout`、`retry`、`run_user`。
- 有目标但没有 Schedule 的旧 Task 生成一条禁用的 `manual` 迁移调度，名称为 `迁移：<task name>`，保留原目标与策略。
- 旧 Task.Condition 按本地/远程拆分：本地保留在 Task，远程复制到全部 Schedule；混合 `and` 递归拆分。无调度的远程条件写入迁移 manual 调度；无法绑定时告警并保留本地部分。
- 迁移完成后提升 Global Revision，触发 Agent 全量对账。
- 数据迁移在单个事务内完成；失败时 Hub 启动报错，不继续以半迁移状态运行。

验证点：

1. Hub `/healthz`、`/readyz` 返回 200，启动日志无迁移错误。
2. 任务定义数量与升级前一致，页面不再显示 Task 的目标与策略字段。
3. 调度计划数量正确；抽查原 Task 的目标与策略已复制到其全部 Schedule。
4. 原「有目标但无调度」的任务出现一条禁用的 `manual` 迁移调度，目标与策略保留。
5. 历史执行记录数量不变，来源调度为空或显示「历史记录/未知来源」。
6. 所有 Agent 升级后重新连接并完成同步；旧协议 Agent 被拒绝时日志给出明确版本错误。
7. agent-owned 调度在 Agent 离线/恢复场景仍可触发，执行记录带来源调度。

回滚步骤与限制：

1. 停止新 Hub 与所有新 Agent。
2. 恢复升级前的 Hub 数据卷/目录备份，并恢复旧 Hub 二进制或镜像。
3. 恢复 Agent 数据目录/卷，或让旧 Agent 重新全量同步。
4. 旧 Hub 不支持直接读取新库：不得只回退二进制而保留已迁移的 Hub DB。
5. 回滚会丢失备份之后产生的执行记录与定义变更；回滚前先导出需要保留的执行记录。
6. 旧 Hub 与新 Agent、以及新 Hub 与旧 Agent 均不兼容，回滚必须同时恢复两侧版本。

## 6. 常见启动失败

| 日志特征 | 原因 | 处理 |
|---|---|---|
| `ADMIN_PASSWORD must be set`（compose 拒绝解析） | `.env` 未设 `ADMIN_PASSWORD` | 设置后重新 `up` |
| `admin password is required`（exit 1） | 口令为空 | 同上 |
| `init oidc: ...`（exit 1） | SSO-only 模式（`allow_local_login=false`）discovery 失败 | 见第 4 节恢复流程（切回 `true` 重启） |
| `allow_local_login=false requires oidc.issuer ...`（exit 1） | SSO-only 模式未配置 `OIDC_ISSUER` | 配置 issuer，或切回 `true` 重启 |
| `load bundled agent payloads failed`（WARN） | 非打包构建缺内置 Agent | 用 `make build` 或官方镜像 |

## 7. 网络与反向代理（单入口）

**单入口模式**：`NODESTEER_GATEWAY_ADDR` 与 `NODESTEER_WEB_ADDR` 相同（compose 默认 `:8080`）即触发。
Web/API、纳管下载、Agent WebSocket 与文件传输全部走 8080 一个端口，对外只需暴露 8080。

**HTTPS 一律由反向代理终结，Hub 侧全明文：**

- `web_tls_*` / `gateway_tls_*` 两套 TLS 配置在反代部署下**恒为空**，Hub 只以明文 HTTP/WS 提供服务。
- Agent 经反代以 `wss://` 连接；使用公签证书时**无需**为 Agent 配置 `tls_ca_file`。
- 仅私有 CA 场景需要给 Agent 配置 `tls_ca_file`，且信任的是反代的证书链。

**双端口模式（可选回退）**：默认双端口 `:8080/:8443` 保留为可选模式，8443 仍可选，见 `docker-compose.yml` 注释（放开 8443 端口映射，并把 `NODESTEER_GATEWAY_ADDR` 改回 `:8443`）。

> ⚠️ **行为提示**：已生成的旧纳管命令若基于 8443，切换单口后需在节点页**重新生成**，否则命令仍指向 8443。

nginx 示例（单 `location /` + WebSocket 透传 + 明文上游）：

```nginx
server {
    listen 443 ssl;
    server_name hub.example.com;

    ssl_certificate     /etc/nginx/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/privkey.pem;

    client_max_body_size 10g;   # 与单文件传输上限匹配
    proxy_buffering off;        # 实时日志/流式响应直通

    location / {                # 单入口：Web/API、纳管下载、Agent WS、文件传输共用
        proxy_pass http://127.0.0.1:8080;   # 明文上游（Hub 侧不做 TLS）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;      # WebSocket 握手透传
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;   # 长连接与大文件传输
        proxy_send_timeout 3600s;
    }
}
```

要点：nginx 只保留**单 `location /`**；`Upgrade`/`Connection` 头原样透传给 Agent WS；上游必须是**明文** `http://127.0.0.1:8080`；保留 `proxy_read/send_timeout 3600s`、`client_max_body_size 10g` 并关闭 `proxy_buffering`。不再需要 8443 独立 vhost 或双 location。
