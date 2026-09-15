## Why

NodeSteer 仓库当前仅包含产品与架构文档（`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`），没有任何可运行代码。用户要求按照规划实现文档定义的一期全部 32 项功能，构建轻量 Hub-Agent Linux 自动化控制平台，并严格遵循 19 条核心架构不变量。

## What Changes

- 新建 Go 单二进制 Hub（`cmd/hub`）与 Agent（`cmd/agent`），共享 `internal/` 模块。
- Hub 提供 REST API + Agent Gateway（WebSocket 长连接）+ Web UI（React + Vite + TypeScript，内嵌单镜像）。
- Hub 存储 SQLite（默认，支持 PostgreSQL）；Agent 存储 SQLite + WAL。
- 实现 Node/Group/Label、Inventory、Capability、Registration/Heartbeat、Session 管理。
- 实现 Desired State 三层同步：Realtime Notification + Periodic Revision Check + Reconnect Reconciliation。
- 实现 Revision Manager（对象/全局单调递增）、Change Log、Tombstone、Atomic Sync、Full Resync。
- 实现 Script（Shell/Bash/Python）、Task（Command/Script/Deploy/Operation）、Schedule（Cron/Interval/One-Time，IANA Timezone）。
- 实现 Execution：Manual Run、Scheduled Run、Execution ID/Key 幂等、Journal、Timeout、Cancel、Process Group、Realtime/Offline Logs、Reconnect Upload。
- 实现 Local Scheduler（Agent 内置，不依赖 cron）、Offline Execution、Misfire、Execution Owner（HUB/AGENT）。
- 实现 Condition Engine（Local + Remote Node Condition，Fail Closed）。
- 实现 Artifact（不可变、SHA256、HTTP 下载、Content Addressed Cache、Prefetch）。
- 实现 Managed Application、NativeHostAdapter/ContainerHostAdapter、Managed systemd、Deploy/Upgrade/Health Check/Rollback、Deployment Journal、Crash Recovery。
- 实现 Docker 部署（Dockerfile ×2 + docker-compose.yml + .env.example）与 Native systemd packaging。
- 实现 RBAC（Administrator/Operator/Viewer）、Basic Audit、Observability（/healthz、/readyz、metrics）。
- 实现 Web UI 全部页面（Dashboard/Nodes/Groups/Scripts/Tasks/Schedules/Applications/Artifacts/Executions/Settings）。
- 全部状态来自真实后端，不引入 Fake Data。
- 明确排除后续规划项（Workflow DAG、Rolling/Canary、LDAP/OIDC、Hub HA 等），但架构预留扩展边界。

## Capabilities

### New Capabilities
- `hub/nodes`: Node 注册/Heartbeat/Inventory/Capability，Group/Label 管理。
- `hub/definitions`: Script、Task、Schedule 定义与 Revision、Assignment、Tombstone。
- `hub/sync`: Global/Object Revision、Change Log、Realtime Notification、Reconnect Reconciliation、Sync Status。
- `hub/executions`: Execution 生命周期、Manual Run、幂等、Logs、Audit 关联。
- `hub/applications`: Artifact 管理与 HTTP 下载、Application Definition、Assignment、Deploy 触发。
- `hub/access-control`: RBAC 角色、Audit Log、Session 认证。
- `hub/api`: REST API、Agent Gateway（WebSocket）、Observability、Web UI 服务。
- `agent/core`: Agent 连接/认证/Heartbeat、Desired State 同步、Local Scheduler、Condition Engine、Inventory。
- `agent/executions`: Execution 幂等、Journal、Process Runner、Timeout/Cancel、Logs 与重连上传。
- `agent/applications`: Artifact Cache、Host Adapter（Native/Container）、Managed systemd、Health、Deploy/Upgrade/Rollback、Deployment Journal。

### Modified Capabilities
<!-- 无既有 spec，全部为一期新建 -->

## Impact

- 新增整个 Go 代码库（`cmd/`、`internal/`）、Web 前端（`web/`）、数据库迁移（`migrations/`）、打包（`packaging/`）。
- 新增 Docker/Compose 部署与 systemd unit 文件。
- 新增单元/集成/E2E 测试覆盖关键故障场景。
- 依赖：Go 标准库 + 少量第三方（sqlite driver、gorilla/websocket 或 nhooyr/websocket、cron 解析、uuid、bcrypt 等），React/Vite 前端依赖。
