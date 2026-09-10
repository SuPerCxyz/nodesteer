## 1. 工程搭建

- [x] 1.1 初始化 Go module、目录结构（cmd/hub、cmd/agent、internal/*、web/）
- [x] 1.2 添加 Go 依赖（sqlite、websocket、cron、uuid、bcrypt 等）
- [x] 1.3 初始化 Vite React TS 前端工程与基础布局
- [x] 1.4 建立 embedded SQLite migration runner 与基础迁移文件

## 2. 共享模型与协议

- [x] 2.1 定义 internal/models：Node、Script、Task、Schedule、Execution、Artifact、Application、User、Audit
- [x] 2.2 定义 internal/protocol：Envelope 与全部消息类型（HELLO、HEARTBEAT、SYNC_*、RUN_*、LOG_CHUNK、ARTIFACT_PREFETCH 等）
- [x] 2.3 定义 Revision 与 Change Log 模型

## 3. Phase 1: Hub 骨架与 Node 管理

- [x] 3.1 Hub 配置加载与启动入口（cmd/hub）
- [x] 3.2 Hub 数据层：users/nodes/node_labels/node_groups/scripts/tasks/schedules/artifacts/applications/executions/execution_logs/agent_sync_state/revision_changes/audit_logs
- [x] 3.3 Node Manager：注册/Heartbeat/Inventory/Capability/Online-Offline/Maintenance/Disabled
- [x] 3.4 Group/Label Manager
- [x] 3.5 Agent Gateway：WebSocket 接入、AUTH/HELLO、Session Manager
- [x] 3.6 REST API：/api/nodes、/api/groups 及认证中间件
- [x] 3.7 Web UI：Dashboard/Nodes/Node Detail/Groups 页面

## 4. Phase 1: Native Agent

- [x] 4.1 Agent 配置加载、SQLite + WAL 本地存储（identity/sync_state/...）
- [x] 4.2 Agent Connection Manager：主动连接、重连退避、AUTH/HELLO
- [x] 4.3 Heartbeat 与 Inventory Collector
- [x] 4.4 Agent 启动入口（cmd/agent）与 systemd unit 文件

## 5. Phase 2: 同步与 Revision

- [x] 5.1 Hub Revision Manager：对象/全局单调递增、Change Log、Audit 事务
- [x] 5.2 Sync Manager：按 Target/Group/Label/Assignment 计算 Agent Desired State
- [x] 5.3 Agent Sync Engine：增量同步、原子事务、Tombstone、Full Resync、Sync Plan 依赖校验
- [x] 5.4 Realtime Notification（Commit 后推送）
- [x] 5.5 Periodic Revision Check（30~60s + Jitter）
- [x] 5.6 Reconnect Reconciliation（配置对账 + 执行对账）

## 6. Phase 3: Script/Task/Execution

- [x] 6.1 Hub Script Manager + Revision History + SHA256
- [x] 6.2 Hub Task Manager + Revision + Definition JSON
- [x] 6.3 Agent Script/Task 同步与本地存储
- [x] 6.4 Manual Run 流程（Hub 创建 Execution、Dispatch）
- [x] 6.5 Agent Execution Manager：Journal、Process Runner、超时/取消、进程组清理
- [x] 6.6 日志：本地持久化、Realtime Stream、离线上传、大小限制
- [x] 6.7 Execution 幂等（Execution ID / 逻辑 Key）
- [x] 6.8 Hub Execution Manager：状态机、幂等 Upsert、取消
- [x] 6.9 Web UI：Scripts/Tasks/Executions 页面

## 7. Phase 4: Local Scheduler

- [x] 7.1 Agent Local Scheduler：Cron/Interval/One-Time、IANA Timezone、Misfire、Offline Policy
- [x] 7.2 Execution Owner 语义（AGENT 仅本地触发，禁止双触发）
- [x] 7.3 Scheduled Execution 逻辑 Key 幂等与数据库 UNIQUE
- [x] 7.4 Hub Schedule Manager 与 HUB Owner 触发
- [x] 7.5 Web UI：Schedules 页面

## 8. Phase 5: Artifact/Application

- [x] 8.1 Hub Artifact Manager：上传、SHA256、元数据、HTTP 下载端点
- [x] 8.2 Agent Artifact Cache：.tmp 下载 → 校验 → Atomic Rename
- [x] 8.3 Artifact Prefetch 协议与执行
- [x] 8.4 Host Adapter 接口 + NativeHostAdapter + ContainerHostAdapter
- [x] 8.5 Application Manager（Hub）：Definition/Revision/Assignment
- [x] 8.6 Agent Application Manager：Deploy/Upgrade/Rollback、Managed Unit Registry
- [x] 8.7 Managed systemd：Install/Update/daemon-reload/enable/start/stop/restart/status
- [x] 8.8 Health Checker：SYSTEMD/TCP/HTTP/COMMAND
- [x] 8.9 Deployment Journal 与 Crash Recovery
- [x] 8.10 Docker Agent 模式 + Host Integration 模式
- [x] 8.11 Web UI：Applications/Artifacts 页面

## 9. Phase 6: 收尾

- [x] 9.1 RBAC（Administrator/Operator/Viewer）+ 用户认证完整化
- [x] 9.2 Audit Log 全事件覆盖 + 查询 API
- [x] 9.3 Observability：/healthz、/readyz、metrics
- [x] 9.4 Settings 页面与配置 API
- [x] 9.5 Dockerfile ×2 + docker-compose.yml + .env.example + systemd packaging
- [x] 9.6 Docker Host Integration 安全加固（Path Allowlist、防 ../ 与 symlink escape）

## 10. 测试与验证

- [x] 10.1 单元测试：models/protocol/store/condition/host adapter
- [x] 10.2 集成测试：Hub+Agent 同步、Execution、Schedule、Offline、Artifact、Application
- [x] 10.3 故障测试：Hub Restart、Agent Restart、断连、丢包/乱序通知、重复触发、SHA 失败、Rollback、Crash During Deployment
- [x] 10.4 E2E：Web UI 页面逐项验收
- [x] 10.5 Native Agent 验证、Docker Agent 验证、Docker Host Integration 验证
- [x] 10.6 构建验证：go build、前端构建、Docker 构建
- [x] 10.7 生成 Requirement Traceability Matrix 与最终报告

## 11. 文档与收尾

- [x] 11.1 README（部署/使用）
- [x] 11.2 更新 OpenSpec tasks 状态并验证（openspec validate）
- [x] 11.3 最终完成报告（实现概览、RTM、验证结果、Known Issues）
