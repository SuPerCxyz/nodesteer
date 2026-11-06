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
| `ADMIN_PASSWORD` | **是** | 无（缺失即拒绝启动） | 初始管理员口令；生产必须设置。空口令默认拒绝；仅隔离测试可配合 `NODESTEER_ALLOW_EMPTY_PASSWORD=true`，见 2.1 |
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
| `MONITOR_ALLOWED_CIDRS` | 否 | 空 | 外部探测允许的 CIDR，逗号分隔；空表示不允许向任何地址发起探测 |
| `MONITOR_WORKERS` | 否 | `8` | 外部目标采集并发数，1–128 |
| `MONITOR_METRICS_ENABLED` | 否 | `true` | Agent 原生节点指标采集开关；映射到 `NODESTEER_METRICS_ENABLED` |
| `MONITOR_METRICS_INTERVAL` | 否 | `30s` | 原生指标采集周期，Go duration；运行时限制 5s–300s。生产推荐 30s，5s 会显著放大写入与聚合成本 |
| `NODESTEER_SQLITE_BUSY_TIMEOUT` | 否 | `5s` | Hub SQLite 锁等待上限；Gateway BUSY 重试总预算 8s，小于 Agent ping/ACK 10s |
| `NODESTEER_NATIVE_METRICS_INTERVAL` | 否 | `5m` | 原生指标聚合/清理周期 |
| `NODESTEER_NATIVE_METRICS_LATE_WINDOW` | 否 | `15m` | 迟到样本重算窗口；更早缺口需显式 backfill |
| `NODESTEER_NATIVE_METRICS_AGGREGATE_BUDGET` | 否 | `10s` | 每轮聚合时间预算；超出后按 tier 游标下轮继续 |
| `NODESTEER_NATIVE_RAW_RETENTION` | 否 | `168h`（7d） | raw 保留期；预览可设 `24h` |
| `NODESTEER_NATIVE_1M_RETENTION` | 否 | `2160h`（90d） | 1m rollup 保留期；预览可设 `168h` |
| `NODESTEER_NATIVE_5M_RETENTION` | 否 | `8760h`（365d） | 5m rollup 保留期；预览可设 `720h` |
| `NODESTEER_NATIVE_1H_RETENTION` | 否 | `17520h`（730d） | 1h rollup 保留期；预览可设 `8760h` |
| `NODESTEER_NATIVE_METRICS_VACUUM_PAGES` | 否 | `5000` | 每轮 prune 最多回收的 freelist 页数（约 20MB）；受 5s prune 预算约束 |

完整列表以 `.env.example` 为准；容器内变量名统一为 `NODESTEER_` 前缀（如 `NODESTEER_OIDC_ISSUER`），由 Hub 进程读取。

原生指标保留期代码默认为 raw 7d、1m 90d、5m 1y、1h 2y，可通过 YAML（`native_raw_retention` 等）或环境变量覆盖；要求 raw >= 1h 且 raw <= 1m <= 5m <= 1h。`NODE_METRICS` ingest、周期聚合/清理和查询 API 已接入。

### 2.1 测试环境空密码（仅隔离）

- `NODESTEER_ALLOW_EMPTY_PASSWORD=true` 允许本地管理员以空密码登录；默认 `false`，生产环境必须保持关闭。
- `/api/auth-mode` 返回 `allow_empty_password`，登录表单仅在该值为 `true` 时放开空密码校验。
- 仅用于隔离测试。开启后任何能访问 Hub 的人都可取得管理员会话；验证后关闭并重启 Hub。
- Docker Compose 的 `NODESTEER_ADMIN_PASSWORD: "${ADMIN_PASSWORD:?...}"` 仍要求变量非空；空密码测试应直接启动 Hub 或使用隔离配置，不要为生产放宽该必填校验。

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
- 原生监控升级：schema v23 新增 `node_metric_samples` / `node_metric_rollups`，schema v24 新增告警/通知表；Nocturne 后端、路由、配置和绑定已移除，遗留 `monitoring_node_bindings` 表保留但不参与新链路；旧 Nocturne 历史不迁移、不回填、不展示。

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
7. 回滚恢复升级前 Hub DB 备份；旧 Nocturne 数据保持原状，不随 NodeSteer 回退迁移。

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

### 7.1 入站连接（Hub 主动拨号）

默认关闭。Hub 侧配置：

```yaml
inbound_enabled: true
inbound_ca_file: /var/lib/nodesteer-hub/inbound/ca.pem
inbound_ca_key_file: /var/lib/nodesteer-hub/inbound/ca-key.pem
inbound_client_cert_file: /var/lib/nodesteer-hub/inbound/client.pem
inbound_client_key_file: /var/lib/nodesteer-hub/inbound/client-key.pem
inbound_allowed_ports: [18444]
inbound_allow_loopback: false
```

对应环境变量为 `NODESTEER_INBOUND_ENABLED`、`NODESTEER_INBOUND_CA_FILE`、`NODESTEER_INBOUND_CA_KEY_FILE`、`NODESTEER_INBOUND_CLIENT_CERT_FILE`、`NODESTEER_INBOUND_CLIENT_KEY_FILE`、`NODESTEER_INBOUND_ALLOWED_PORTS`；拨号并发/超时/退避使用 `NODESTEER_INBOUND_DIAL_CONCURRENCY`、`NODESTEER_INBOUND_DIAL_TIMEOUT`、`NODESTEER_INBOUND_BACKOFF_INITIAL`、`NODESTEER_INBOUND_BACKOFF_MAX`。

- 未显式提供 CA/客户端证书路径时，Hub 在 `<data_dir>/inbound` 自动生成，私钥 `0600`；请备份 CA 私钥。
- Hub 需要能**出站**访问节点 `endpoint`（默认 18444）；不需要把该端口映射到 Hub。
- 节点 endpoint 在添加节点时必填，可填写域名、IP 或 `host:port`，未写端口时规范化为 `18444`。endpoint 变更会清空已签发的入站身份并标记 `inbound_bundle_stale`，必须重新下载或轮换 bundle。
- 防火墙只放行 Hub 出站地址到 Agent 的 TCP/18444，例如 `ufw allow from <Hub 出口 IP> to any port 18444 proto tcp`；不要对公网开放该端口。Docker Agent 需要 `-p 18444:18444`，并保持 bundle 中的 `listen_addr: 0.0.0.0:18444`。
- Agent 侧仅在安装完整 bundle 后监听；配置位于 `inbound.*`（`listen_addr`、`cert_file`、`key_file`、`ca_file`、`hub_client_fingerprint_file`、`token_file`、`allowed_source_cidrs`、`max_connections`、`handshake_timeout`）。私钥和令牌文件权限必须为 `0600`。
- 默认拒绝 loopback、link-local、云 metadata、multicast 和非白名单端口；RFC1918 默认允许。仅测试环境可显式启用 `inbound_allow_loopback`；`inbound_denied_cidrs` 只能进一步收紧，不能放行默认拒绝的地址。
- 入站数据面复用同一 TLS 端口：文件源 pull、目标 push、Artifact/Agent 二进制 push。数据面请求需同时携带 mTLS 客户端证书、指纹 pin、`Authorization: Bearer <base64url(node token)>`、`X-NodeSteer-Session` 和 `X-NodeSteer-Node`；transfer 授权 5 分钟有效，默认每会话 100 请求/分钟。
- 所有写入先落 `.part`，校验大小与 SHA-256 后原子替换；拒绝路径穿越和符号链接。文件默认上限 10 GiB，Agent 二进制上限 256 MiB。
- 只有 Agent 未声明 `inbound_data_plane`、会话方向切换或具体阶段不可用时，才返回 `409 inbound_data_plane_unavailable` / `inbound_stage_unavailable` 或落 `BLOCKED`。

#### 7.1.1 签发与安装 bundle

1. 在 Web「节点 → 添加节点」填写节点域名或 IP；创建后打开节点详情。
2. Administrator 点击「下载入站安装包」，得到 `nodesteer-inbound-<node>.tar.gz`。接口为 `GET /api/nodes/{id}/inbound-bundle`，响应 `Cache-Control: no-store`，敏感文件权限为 `0600`。
3. 将压缩包复制到 Agent 主机，解压后执行：

```bash
tar xzf nodesteer-inbound-<node>.tar.gz
sudo ./install-inbound.sh
```

安装脚本要求 Agent 已安装，写入 `/etc/nodesteer/inbound/` 和 `/etc/nodesteer/agent.yaml`，并重启 `nodesteer-agent`。随后在 Hub 节点详情点击「测试连接」，确认 DNS、TCP、TLS、证书指纹和 `HUB_HELLO` 阶段通过。

bundle 是一次性凭据：首次下载后再次 GET 返回 `409 inbound_bundle_already_issued`；轮换必须显式调用 `POST /api/nodes/{id}/inbound-bundle/rotate` 或点击「轮换入站身份」。轮换会立即签发新证书/令牌、关闭当前入站会话并返回新的压缩包，旧 bundle 不能继续使用。Hub 不会自动轮换证书，请在证书到期前手动轮换。

#### 7.1.2 CA 备份、回滚与排查

- 首次启用时备份 `<data_dir>/inbound/ca.pem`、`ca-key.pem`、`client.pem`、`client-key.pem`。CA 私钥只保存在 Hub 数据目录，权限 `0600`，不要放入版本库或日志。
- 回滚 Agent：停止 `nodesteer-agent`，删除 `/etc/nodesteer/inbound/`，移除 `/etc/nodesteer/agent.yaml` 的 `inbound` 段，再启动 Agent。Hub 侧随后轮换或禁用该节点入站身份。
- `inbound_bundle_stale=true` 表示 endpoint 已变更但未重新签发；重新下载/轮换后恢复。`last_error` 会记录 `dns_failed`、`tcp_failed`、`tls_verify_failed`、`inbound_pin_mismatch`、`inbound_auth_failed` 等稳定阶段码。
- 签发、轮换和连接测试分别写入审计 `node.inbound_bundle_issue`、`node.inbound_bundle_rotate`、`node.connection_test`，审计详情不包含私钥或令牌。
- 双方的隔离 NAT、relay/P2P、自动证书轮换不在当前范围；Hub 无法出站到 Agent endpoint 的拓扑暂不支持。

## 监控配置

- `NODESTEER_MONITOR_ALLOWED_CIDRS` 是以逗号分隔的允许探测 CIDR；也可在 Hub YAML 中使用 `monitoring_allowed_cidrs` 数组。默认空列表，不允许向任何地址发起监控请求。每次拨号按 DNS 解析后的实际地址校验，不使用环境代理，也不自动跟随重定向。
- 外部 HTTP 目标可引用 `NODESTEER_MONITOR_SECRET_` 开头的环境变量作为 Bearer 凭据或敏感查询参数；配置保存环境变量名，不保存其值。使用凭据时目标必须为 HTTPS。普通查询参数通过页面参数列表配置；不要把密钥放入 URL 路径或普通参数。
- 外部 TCP 和 ICMP 目标分别使用 `tcp://host:port`、`icmp://host`，可选择 Hub 或 Agent 发起探测并遵循 CIDR 限制。ICMP 使用探测端环境中的 `/usr/bin/ping`，缺少该程序或权限时报告不可用，不伪造成功。
- 选择 Agent 探测位置时，Agent 也必须独立配置 `NODESTEER_MONITOR_ALLOWED_CIDRS`（或 YAML `monitoring_allowed_cidrs`）；实际允许网段为 Hub 和 Agent 配置的交集。Agent 默认仅接受使用可信 TLS 的 `wss://` Hub 探测请求。仅隔离开发环境可显式设置 `NODESTEER_MONITOR_ALLOW_INSECURE_HUB=true`。
- Agent 本地探测凭据须配置 `monitoring_secret_destinations`（或 `NODESTEER_MONITOR_SECRET_DESTINATIONS` JSON 对象），将每个 `NODESTEER_MONITOR_SECRET_*` 变量名映射到允许使用它的 HTTPS origin 数组；未授权的目的地和凭据不会被使用。探测请求对时间偏差和重放有检查，Agent 与 Hub 需保持时钟同步。旧 Agent 不支持时探测失败并保留连接，不提供独立不可用状态。
- 原生节点指标由同一个 NodeSteer Agent 采集，不需要额外监控 Agent。Agent 配置 `metrics_enabled`（默认 `true`）、`metrics_interval`（默认 `30s`，限制 5s–300s）和 `metrics_buffer_batches`（默认 `20`，最大 `60`，另有 4MiB 内存硬上限），对应环境变量 `NODESTEER_METRICS_ENABLED`、`NODESTEER_METRICS_INTERVAL`、`NODESTEER_METRICS_BUFFER_BATCHES`；Compose 示例使用 `MONITOR_METRICS_ENABLED`、`MONITOR_METRICS_INTERVAL` 映射。补报只保存在内存，进程退出即丢失。
- 指标包含 `cpu.usage`、`memory.usage`、`load.1/5/15`、`disk.usage`、`network.rx_bps/tx_bps`。Agent 声明 `node_metrics` 能力；Native 和 Docker Host Integration 支持，Docker 基础模式只能看到容器自身 `/proc`，不声明该能力。旧 Agent 不支持时页面显示不可用，不以 0 代替。
- 指标扩展包括 CPU 组成（user/nice/system/idle/iowait/irq/softirq/steal）、内存 used/cached/buffers/available、Swap、Inode、磁盘 read/write bps/IOPS、网络 errors/drops、温度、风扇和容器 CPU/内存/网络。容器指标自动探测 Docker/Podman socket；无 socket 或不可读时没有容器样本，不显示 0。
- Hub 原生时序存储默认保留期为 raw 7d、1m 90d、5m 1y、1h 2y，可通过上述 env/YAML 覆盖；预览建议 raw 1d、1m 7d、5m 30d、1h 1y。schema v23 已创建 raw/rollup 表，schema v29 增加 `node_metric_series` 目录；gateway 已接入 `NODE_METRICS`，后台默认每 5 分钟执行聚合/清理（15m 迟到窗口、10s 预算、tier 游标），`/api/monitoring/nodes/*` 查询 API 已可用。Hub SQLite 使用独立写池（2 连接）与只读池（8 连接，`query_only`），API 读请求不再与聚合写事务争抢同一连接池。`overview` 的告警数仍为占位 0，规则告警汇总未接入。
- 前端时间范围支持 1h/6h/24h/7d 和自定义范围（最大 30 天），默认 1h；历史查询仍受后端保留期和点数预算限制。
- v29 迁移会从现有 raw/rollup 一次性回填 series 目录；4.4GB / 7M raw 快照实测约 29s。迁移只建表和目录，不改 raw/rollup；升级前按部署规范备份 Hub DB。
- 缺失 rollup 用一次性、分块、限速命令恢复，不删除 raw：
  ```bash
  nodesteer-hub --config /etc/nodesteer/hub.yaml \
    --backfill-native-from 2026-11-06T14:00:00Z \
    --backfill-native-to 2026-11-06T14:40:00Z \
    --backfill-native-chunk 15m --backfill-native-pause 10ms
  ```
  命令按 1m → 5m → 1h 顺序执行，重复运行幂等；运行完退出，不启动 Web/Gateway。建议在低峰期执行并避免与高写入叠加。
- 清理过期数据：
  ```bash
  nodesteer-hub --config /etc/nodesteer/hub.yaml --prune-native-dry-run
  nodesteer-hub --config /etc/nodesteer/hub.yaml --prune-native
  ```
  Prune 默认每批 10,000 行、5s 预算、15ms 间隔；rollup 独立清理，raw 仅在上次聚合成功且不超过 30 分钟内删除。可用 `--prune-native-batch`、`--prune-native-budget` 覆盖。`auto_vacuum=INCREMENTAL` 时每轮默认回收最多 5,000 页（约 20MB），按 500 页小批执行并受 5s 预算约束，可用 `native_metrics_vacuum_pages` / `NODESTEER_NATIVE_METRICS_VACUUM_PAGES` 覆盖。
- 已有库启用增量空间回收（必须停 Hub，约需 2x 磁盘空间；普通 migration 不执行 VACUUM）：
  ```bash
  systemctl stop nodesteer-hub
  nodesteer-hub --config /etc/nodesteer/hub.yaml --enable-incremental-vacuum
  systemctl start nodesteer-hub
  ```
  命令输出 VACUUM 耗时与前后文件大小，并校验 `auto_vacuum=2`。新库首次建表前自动启用 `auto_vacuum=INCREMENTAL`。
- 已有 `auto_vacuum=INCREMENTAL` 的库可快速回收当前 freelist（Hub 运行或停机均可，分批执行，避免长锁）：
  ```bash
  # 只查看 freelist，不回收
  nodesteer-hub --config /etc/nodesteer/hub.yaml --drain-free-pages-dry-run
  # 最多回收 100000 页，每批 5000 页，30s 预算
  nodesteer-hub --config /etc/nodesteer/hub.yaml --drain-free-pages \
    --drain-free-pages-max 100000 --drain-free-pages-batch 5000 --drain-free-pages-budget 30s
  ```
  `--drain-free-pages-max 0`（默认）表示回收当前全部 freelist，仍受预算限制；预算耗尽后可重复执行。
- `node_metric` 规则使用 Hub 原生样本和实际样本时间，保留新鲜度、最大样本延迟、gap、乱序/冲突中断、去重、冷却、失败暂停、验证和补偿语义。目标已切换为 NodeSteer `node_id + metric + series`；`/metrics` 查询必须带 `series` 参数。
- 节点规则可配置分批、验证调度和显式补偿调度；未知结果停止后续自动推进，需要检查每个节点的执行记录。停用规则不会取消已下发任务，并会阻止后续批次、验证和自动补偿；必要时通过已有任务手动恢复。
- 采集并发默认 8，可通过 `NODESTEER_MONITOR_WORKERS` 或 `monitoring_workers` 配置为 1–128。调度优先处理最早到期的目标；实际负荷超过并发处理能力时，采样会延迟，页面显示过期，服务日志报告延迟目标数。并发不保证所有目标一定满足设定周期，请按目标数量、超时和网络延迟规划容量。
- HTTP 采样历史最多保存 30 天、每目标最多 10 万条，周期性清理；未完成或结果未知的联动事件保留以便人工检查。同一数据目录仅允许一个 Hub 运行监控调度。
- 观察记录包含耗时字段（`duration_ns`），`/api/monitoring/latest` 与目标历史观察接口均返回该字段；最近观察页面展示耗时（`duration_ns` 转为毫秒）。
- Nocturne 后端、路由、配置和绑定已移除；`monitoring_node_bindings` 仅作为遗留表保留，不参与新链路。已有 Nocturne 历史不迁移、不回填、不在新界面展示。
- 升级/回退：schema v23 新增原生指标表，v24 新增告警/通知表，均为增量迁移；升级前备份 Hub DB，回退恢复升级前 DB 备份和旧二进制；Nocturne 原历史保持原状，不随 NodeSteer 升级或回退迁移。遗留绑定表是否物理删除由后续独立迁移决定。

### 告警与通知

- 告警规则基于原生指标（节点/指标/序列、操作符/阈值、持续时间、恢复时间、冷却、severity、渠道、启停、静默）；评估周期默认 30s，无样本、过期或节点离线时 fail-closed，不触发、不恢复。
- 告警只通知和记录，不触发任务；任务联动仍由自动化控制 `/rules` 的 `monitoring.Rule` 执行链负责。
- 通知渠道支持 webhook、Slack、钉钉、企业微信、Telegram、Email（SMTP）。渠道配置只保存环境变量名：`url_env`、`token_env`、`password_env`、`to_env`；实际值只从 Hub 进程环境读取，不写入 DB、日志或 API 响应。
- 环境变量示例（仅变量名，真实值由部署环境注入）：
  - `ALERT_WEBHOOK_URL`、`ALERT_SLACK_WEBHOOK_URL`、`ALERT_DINGTALK_WEBHOOK_URL`、`ALERT_WECOM_WEBHOOK_URL`
  - `ALERT_TELEGRAM_TOKEN`
  - `ALERT_SMTP_PASSWORD`
  - `ALERT_EMAIL_TO`（多个收件人用逗号分隔）
- 通知采用发件箱：默认最多 3 次尝试、指数退避；配置/环境变量错误直接失败。渠道支持测试发送；测试发送不要求渠道启用。
- 已实现规则/事件/渠道 API、确认（ack）、静默和测试发送。钉钉加签、邮件 HTML/附件、通知发送记录查询 API 尚未实现。

原生监控链路已接入：Agent 采集 → gateway ingest → v23 存储 → 周期聚合/清理 → 原生查询 API → 前端总览/历史/规则；告警/通知链路已接入（评估/发件箱/渠道）。最终 `make test`/`make lint`/race 子集和前端全量回归仍在进行，P3/E2E 债务见 `openspec/changes/unified-monitoring-automation/tasks.md`。
