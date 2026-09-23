## Context

仓库目前只有 `docs/` 下的产品与架构文档，无代码。需要从零搭建 Go + React 的 Hub-Agent 平台。架构约束见 proposal.md（What Changes）与 `docs/ARCHITECTURE.md`（19 条核心不变量、§64 代码结构、§65 研发顺序）。

## Goals / Non-Goals

**Goals:**
- Go 单二进制 Hub 与 Agent，共享 `internal/` 模块。
- Hub：REST API + WebSocket Agent Gateway + 内嵌 React Web UI。
- Agent：主动连接、本地 SQLite + WAL、Local Scheduler、Offline Execution。
- Native/Docker Agent 共用 Agent Core，差异收敛到 Host Adapter。
- 三层同步（Realtime Notify + Periodic Check + Reconnect Reconcile）保证最终一致。
- Execution 幂等 + Journal + Idempotent Upsert 保证可靠性。
- Artifact HTTP 下载 + SHA256 内容寻址缓存；Managed Application 部署/升级/回滚 + Deployment Journal。

**Non-Goals:**
- 后续规划能力（Workflow DAG、Rolling/Canary、Hub HA、LDAP/OIDC、External S3、Advanced Queue 等）一期不实现，仅预留扩展边界。

## Decisions

### D1: 通信协议使用 WebSocket + JSON Envelope
- 长连接采用 WebSocket（库：`github.com/coder/websocket`，为独立维护且 API 稳定），消息格式为统一 Envelope：`{type,id,timestamp,payload}`，协议版本化（protocol_version）。
- 理由：HTTP/1.1 之上可用，NAT/反向代理友好，Go 端实现简单，满足一期 100~1000 Agent 目标。
- 备选：gRPC Stream（更重，依赖 HTTP/2，反向代理兼容性差）；纯 HTTP 轮询（无法满足实时下发与双向控制）。

### D2: Hub 存储 SQLite 默认、PostgreSQL 可选
- 抽象 `internal/store` 数据层接口，默认实现 SQLite（`modernc.org/sqlite`，纯 Go 无 CGO），正式环境提供 PostgreSQL（`pgx`）实现。
- Revision 采用独立 `revision_sequence` 表 / PostgreSQL SEQUENCE 保证全局单调递增。
- 理由：一期轻量、本地事务边界多；PostgreSQL 仅作适配层，避免锁死。
- 备选：仅 SQLite（无法满足正式环境推荐）；直接上 PostgreSQL（增加部署复杂度）。

### D3: Agent 本地状态 SQLite + WAL
- Agent 用 SQLite + WAL 持久化 identity/sync_state/scripts/tasks/schedules/applications/artifacts/executions/deployments，置于 `/var/lib/cadentra/state.db`。
- 关键同步修改使用事务（BEGIN…COMMIT），Revision 仅在成功提交后前进。

### D4: 代码结构遵循 ARCHITECTURE.md §64
- `cmd/hub`、`cmd/agent`、`internal/hub/*`、`internal/agent/*`、`internal/protocol`、`internal/models`、`web/`、`migrations/`、`packaging/`。

### D5: Host Adapter 隔离 Native/Docker
- `HostAdapter` 接口（WriteFile、AtomicReplace、Chmod、Chown、Mkdir、Remove、InstallUnit、DaemonReload、Enable/Disable/Start/Stop/Restart/ServiceStatus）+ `NativeHostAdapter`（HOST_ROOT=/）与 `ContainerHostAdapter`（HOST_ROOT=/host）。
- Application Manager 只依赖接口，不判断部署模式。

### D6: Artifact 传输分离
- 控制通道只传元数据与 PREFETCH；二进制通过 HTTP(S) `/api/artifacts/<id>/download`。
- Agent 内容寻址缓存 `artifacts/<sha256>`，`.tmp` 下载 → 校验 → Atomic Rename。

### D7: 前端 React + Vite + TypeScript，内嵌进 Go 二进制
- `web/` 独立 Vite 工程，构建产物 `embed` 进 Hub 二进制（`go:embed`），单镜像部署。
- 理由：避免多容器 Web Server；Vite 构建链已确认可用。

### D8: Local Scheduler 内置实现
- Agent 内置 Cron 解析（`github.com/robfig/cron/v3` 解析 + 自有 next 计算，或 `github.com/adhocore/gronx`）与 Interval/One-Time 调度循环，不依赖 Linux cron/systemd timer。
- Execution Owner=AGENT 的 Schedule 仅由 Agent 触发；HUB Owner 由 Hub 触发 RUN_EXECUTION。

### D9: Deployment Journal 驱动应用恢复
- 部署阶段状态机写入 SQLite deployments 表；Agent 重启读取半完成部署，默认安全恢复 Previous。

### D10: 认证
- Hub 用户：bcrypt 密码 + 会话 Token（内存或 DB 存储）。Agent：Registration Token 首次注册换取 Agent Credential（Bearer Token），连接携带认证。

## Risks / Trade-offs

- [systemd/Health/Rollback 需要 root 与真实 systemd 环境] → 提供集成测试脚本与模拟 HostAdapter 的单元测试；真实 systemd 验证在具备权限环境执行，未执行项如实报告。
- [一期范围巨大，单次实现量大] → 按 Phase 1-6 分阶段实现并逐步验证。
- [PostgreSQL 适配增加工作量] → 一期以 SQLite 为主实现，PostgreSQL 提供迁移与配置路径，确保接口抽象到位。
- [SQLite 并发写冲突] → WAL + busy_timeout；Agent 写路径单一协程化。
- [日志体积] → 限制 stdout/stderr/total，截断并标记，不写满磁盘。

## Migration Plan

- 新建仓库结构并初始化 Go module 与前端工程。
- 数据库迁移文件放置 `migrations/`，启动时自动应用（简单 embedded migration runner）。
- Docker Compose 提供 Hub（SQLite 卷）+ 可选 Agent 容器；systemd unit 文件在 `packaging/systemd/`。
- 回滚：无历史版本，迭代开发。

## Open Questions

- 无阻塞性开放问题；实现细节在 tasks 中细化。
