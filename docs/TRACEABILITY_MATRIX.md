# NodeSteer 一期 Requirement Traceability Matrix

状态：DONE = 已实现；VERIFIED = 已通过测试验证；PARTIAL = 部分实现。

## Hub

| Req ID | 来源 | 需求 | 状态 | 实现位置 | 验证 |
|---|---|---|---|---|---|
| H-01 | PRODUCT §4 | Hub Docker / Compose 部署 | VERIFIED | packaging/docker/hub, docker-compose.yml | Docker 构建 + Compose 起 Hub |
| H-02 | PRODUCT §2 | Web 管理 UI | VERIFIED | web/ | Playwright E2E 11/11 |
| H-03 | PRODUCT §46 | REST API | VERIFIED | internal/hub/api | API lifecycle 测试 |
| H-04 | PRODUCT §17 | Node/Group/Label 管理 | VERIFIED | internal/hub/node.go, sqlite_users_nodes.go | 单元 + E2E |
| H-05 | PRODUCT §6 | Registration/Heartbeat/Inventory | VERIFIED | internal/hub/gateway.go | Gateway 集成测试 |
| H-06 | PRODUCT §10/14 | Sync/Revision Manager | VERIFIED | internal/hub/sync.go, revmanager.go | 单元测试 |
| H-07 | PRODUCT §20 | Script + Revision History + SHA256 | VERIFIED | internal/hub/definitions.go | API 测试 |
| H-08 | PRODUCT §21 | 任务定义（Task）+ Revision；Task 不含目标与执行策略 | VERIFIED | internal/hub/definitions.go | API/Store 测试 + 本地浏览器创建任务 |
| H-09 | PRODUCT §23 | 调度计划（Schedule）：任务+目标+策略+manual 类型 | VERIFIED | internal/hub/schedule.go | API/Store 测试 + 本地浏览器创建调度 |
| H-10 | PRODUCT §22 | Manual Run：请求级临时目标与策略 | VERIFIED | internal/hub/execution.go | API 测试 + 本地 Agent 手动执行 SUCCESS |
| H-11 | PRODUCT §33 | Artifact 管理 | VERIFIED | internal/hub/artifact.go | E2E upload/download |
| H-12 | PRODUCT §35 | Managed Application | VERIFIED | internal/hub/application.go | API 测试 |
| H-13 | PRODUCT §44 | RBAC | VERIFIED | internal/hub/auth | 单元测试 |
| H-14 | PRODUCT §45 | Audit | VERIFIED | internal/hub/api/api.go | API 测试 |
| H-15 | PRODUCT §61 | Observability (/healthz /readyz metrics) | VERIFIED | internal/hub/api/api.go, internal/metrics | 冒烟测试 |
| H-16 | ARCHITECTURE §59 | Web/Gateway TLS、Agent Credential 与撤销 | VERIFIED | cmd/hub, hubserver, agent/connection, hub/api | TLS/WSS + Credential 测试 |
| H-17 | PRODUCT §43 | Application 节点健康/执行状态 API | VERIFIED | internal/store, hub/execution.go, hub/api/api.go | Store + API/执行测试 |
| H-18 | PRODUCT §34.1 | Hub-mediated Agent 文件中继 | VERIFIED | internal/hub/file_transfer.go, internal/hub/gateway.go | Hub/Agent HTTP+WebSocket E2E、多目标隔离测试 |
| H-19 | PRODUCT §6 | Native/Docker 节点纳管命令 | VERIFIED | internal/hub/api/api.go, web/src/features/catalog/index.tsx | Enrollment API 测试 + 前端构建 |
| H-20 | PRODUCT §22/§29 | Task/Schedule 数据迁移、Execution 来源字段与协议协同升级 | VERIFIED | internal/store, internal/models, internal/protocol | 迁移测试 + 协议拒绝测试 + 本地 Agent 协同升级 |
| H-21 | PRODUCT §27/§28 | Task 本地条件 / Schedule 远程条件归属与拆分迁移 | VERIFIED | internal/hub/validation.go, internal/store/migrate_condition_split.go | API 归属测试 + 迁移测试 |

## Agent

| Req ID | 来源 | 需求 | 状态 | 实现位置 | 验证 |
|---|---|---|---|---|---|
| A-01 | PRODUCT §5.1 | Native Agent | VERIFIED | cmd/agent, internal/agent | Native E2E |
| A-02 | PRODUCT §5.2 | Docker Agent | VERIFIED | packaging/docker/agent | Docker Compose E2E |
| A-03 | PRODUCT §5.3 | Docker Host Integration | DONE | internal/agent/host/container.go | 路径映射单测；真实 systemd 未在无特权环境验证 |
| A-04 | PRODUCT §9 | Persistent Connection | VERIFIED | internal/agent/connection | Gateway 集成测试 |
| A-05 | PRODUCT §7 | Registration | VERIFIED | internal/agent/agent.go | Gateway 集成测试 |
| A-06 | PRODUCT §11 | Heartbeat | VERIFIED | internal/agent/agent.go | Gateway 集成测试 |
| A-07 | PRODUCT §18 | Inventory | VERIFIED | internal/agent/inventory.go | E2E 显示 OS/kernel/CPU/mem |
| A-08 | PRODUCT §47 | Local SQLite + WAL | VERIFIED | internal/agent/store.go | 单元测试 |
| A-09 | PRODUCT §23 | Local Scheduler | VERIFIED | internal/agent/scheduler | 单测 + E2E 触发 |
| A-10 | PRODUCT §25 | Offline Execution | VERIFIED | internal/agent/agent.go | 幂等/离线上传测试 |
| A-11 | PRODUCT §31 | Execution Journal | VERIFIED | internal/agent/store.go | 单测 + E2E |
| A-12 | PRODUCT §33/44 | Artifact Cache | VERIFIED | internal/agent/artifact.go | SHA 失败/成功测试 |
| A-13 | PRODUCT §35 | Application Manager | DONE | internal/agent/application.go | 单元路径 |
| A-14 | PRODUCT §36 | HostAdapter | VERIFIED | internal/agent/host | 单元测试 |
| A-15 | PRODUCT §37 | Managed systemd | DONE | internal/agent/host | 未在无特权环境验证 |
| A-16 | PRODUCT §40 | Health Check | DONE | internal/agent/application.go | 逻辑实现 |
| A-17 | PRODUCT §41 | Rollback | DONE | internal/agent/application.go | 逻辑实现 |
| A-18 | PRODUCT §42 | Deployment Journal | VERIFIED | internal/agent/store.go | 单元测试 |
| A-19 | ARCHITECTURE §59 | Credential 重连、Artifact Agent 身份 | VERIFIED | internal/agent/connection, agent/artifact.go | Credential/WSS 测试 |
| A-20 | PRODUCT §34.1 | Agent 文件流式上传/下载、校验与原子写入 | VERIFIED | internal/agent/agent.go, internal/agent/host | Agent 文件传输单测 + Hub/Agent E2E |
| A-21 | PRODUCT §23/§29 | Agent 从 Schedule 读取目标/策略；Execution 记录来源与创建时间 | VERIFIED | internal/agent/scheduler, internal/agent/store.go | Agent 单测 + 本地 agent-owned interval 执行 |
| A-22 | PRODUCT §27/§28 | Agent 合并 Task 本地条件与 Schedule 远程条件 | VERIFIED | internal/agent/agent.go, internal/models | Agent 条件合并单测 |

## 同步

| Req ID | 来源 | 需求 | 状态 | 实现位置 | 验证 |
|---|---|---|---|---|---|
| S-01 | PRODUCT §11 | Realtime Notification | VERIFIED | internal/hub/sync.go | Gateway 测试 |
| S-02 | PRODUCT §12 | Periodic Revision Check | VERIFIED | internal/agent/agent.go | E2E sync status |
| S-03 | PRODUCT §13 | Reconnect Reconciliation | VERIFIED | internal/agent/agent.go | E2E |
| S-04 | PRODUCT §14 | Change Log | VERIFIED | internal/store | 单元测试 |
| S-05 | PRODUCT §15 | Tombstone | DONE | internal/agent/agent.go | 逻辑实现 |
| S-06 | PRODUCT §16 | Atomic Sync | DONE | internal/agent/agent.go | 事务实现 |
| S-07 | PRODUCT §14 | Full Resync | VERIFIED | internal/hub/sync.go | 单元测试 |
| S-08 | PRODUCT §16/17 | Schedule Target/Group/Label/Assignment 变化后的快照收敛 | VERIFIED | hub/node.go, hub/sync.go, agent/store.go | 同步单测 + 本地 Agent 按 Schedule.Target 同步并触发 |
| S-09 | ARCHITECTURE §55 | Desired State 变更事务顺序 | VERIFIED | store/sqlite.go, hub/transaction.go | 回滚测试 |

## 执行

| Req ID | 来源 | 需求 | 状态 | 实现位置 | 验证 |
|---|---|---|---|---|---|
| E-01 | PRODUCT §29 | Execution 状态机与过程/结果字段 | VERIFIED | internal/models, hub/execution.go | E2E |
| E-02 | PRODUCT §30 | Execution 幂等（Scheduled Key 不含 Schedule ID） | VERIFIED | internal/hub/execution_test.go | 单元测试 |
| E-03 | PRODUCT §32 | Process Runner | VERIFIED | internal/agent/execution/runner.go | 单元测试 |
| E-04 | PRODUCT §32 | Timeout/Cancel | VERIFIED | internal/agent/execution | 单元 + E2E |
| E-05 | PRODUCT §32 | 日志限制/截断 | VERIFIED | internal/agent/execution/runner.go | 单元测试 |
| E-06 | PRODUCT §31 | 离线上传 | VERIFIED | internal/agent/agent.go | 单元测试 |
| E-07 | ARCHITECTURE §35 | Execution ACK 后标记 synced | VERIFIED | protocol, hub/gateway.go, agent/agent.go | ACK 单测 + 进程测试 |
| E-08 | PRODUCT §29 | Execution 来源 Schedule 与 Created At | VERIFIED | internal/models, store, protocol, hub/execution.go | API/迁移测试 + 本地执行记录 |
| E-09 | PRODUCT §29 | Execution 来源 Schedule Revision | VERIFIED | internal/models, store, protocol, hub/execution.go, internal/agent | API/Agent/迁移测试 |

## 应用

| Req ID | 来源 | 需求 | 状态 | 实现位置 | 验证 |
|---|---|---|---|---|---|
| AP-01 | PRODUCT §33 | Immutable Artifact + SHA256 | VERIFIED | internal/hub/artifact.go | E2E |
| AP-02 | PRODUCT §34 | Prefetch | DONE | internal/hub/artifact.go, agent/agent.go | 逻辑实现 |
| AP-03 | PRODUCT §38 | Binary Deploy | DONE | internal/agent/application.go | 逻辑实现 |
| AP-04 | PRODUCT §39 | Upgrade | DONE | internal/agent/application.go | 逻辑实现 |
| AP-05 | PRODUCT §40 | Health Check | DONE | internal/agent/application.go | 逻辑实现 |
| AP-06 | PRODUCT §41 | Rollback | DONE | internal/agent/application.go | 逻辑实现 |
| AP-07 | PRODUCT §42 | Deployment Journal | VERIFIED | internal/agent/store.go | 单元测试 |
| AP-08 | PRODUCT §43 | Application 节点健康与执行历史 | VERIFIED | internal/models, store, hub/api, web | Store/API/UI 构建测试 |

## Web UI

| Req ID | 来源 | 需求 | 状态 | 实现位置 | 验证 |
|---|---|---|---|---|---|
| W-01 | PRODUCT §43 | Dashboard | VERIFIED | web/src/pages/Dashboard.tsx | E2E |
| W-02 | PRODUCT §43 | Nodes / Node Detail | VERIFIED | web/src/pages/Nodes.tsx | E2E |
| W-03 | PRODUCT §43 | Groups | VERIFIED | web/src/pages/Groups.tsx | E2E |
| W-04 | PRODUCT §43 | Scripts | VERIFIED | web/src/pages/Scripts.tsx | E2E |
| W-05 | PRODUCT §43 | 任务定义（Tasks） | VERIFIED | web/src/features/tasks/index.tsx | 组件测试 + 本地 Playwright P02/P09/P23 |
| W-06 | PRODUCT §43 | 调度计划（Schedules） | VERIFIED | web/src/features/catalog/index.tsx, web/src/features/editors/index.tsx | 组件测试 + 本地 Playwright P12/P24 |
| W-07 | PRODUCT §43 | Applications | VERIFIED | web/src/pages/Applications.tsx | 页面实现 |
| W-08 | PRODUCT §43 | Artifacts | VERIFIED | web/src/pages/Artifacts.tsx | 页面实现 |
| W-09 | PRODUCT §43 | 执行记录（Executions） | VERIFIED | web/src/features/executions/index.tsx | 组件测试 + 本地 Playwright P17/P18/P23 |
| W-10 | PRODUCT §43 | Settings | VERIFIED | web/src/pages/Misc.tsx | E2E |
| W-11 | PRODUCT §43 | Application 健康、分配、执行历史真实展示 | VERIFIED | web/src/pages/Applications.tsx, Dashboard.tsx | 前端构建 + 浏览器导航 |
| W-12 | PRODUCT §34.1/§6 | 文件传输页面、节点纳管命令 | VERIFIED | web/src/features/transfers, web/src/features/catalog | format/lint/typecheck/build |
| W-13 | PRODUCT §43 | 任务定义/调度计划/执行记录三页命名与一致性 | VERIFIED | web/src/components/layout, web/src/features | 本地 Playwright P02/P09/P12/P17/P23/P24 |
| W-14 | PRODUCT §27/§28/§29 | 任务本地条件、调度远程条件、执行调度修订号展示 | VERIFIED | web/src/features/tasks, editors, shared, executions | 组件测试 + 构建 |

## 说明

- DONE 表示代码已实现；VERIFIED 表示有单元/集成/E2E 测试或实际运行验证。
- `refactor-task-schedule-model` 已完成实现与本地验收：H-08/H-09/H-10/H-20、A-21、S-08、E-08、W-05/W-06/W-09/W-13 已从 `PARTIAL` 更新为 `VERIFIED`；远端生产环境与旧 Agent 实机升级仍需在部署环境复验。
- `split-local-remote-conditions` 已完成条件归属拆分、迁移与 `schedule_revision`：H-21、A-22、E-09、W-14 已纳入；远端生产环境与旧协议 Agent 升级仍需在部署环境复验。
- A-03、A-15、AP-03~06 依赖真实 systemd 与 root 权限；本地通过 HostAdapter/部署回滚测试，KVM 记录覆盖 Native systemd 运行路径，当前工作站未重复执行特权 systemd 场景。
- 其余"逻辑实现"项均有对应的 store 层单元测试覆盖持久化与状态机。
