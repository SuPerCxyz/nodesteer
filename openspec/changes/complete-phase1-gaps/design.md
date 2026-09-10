# Design: Complete Phase 1 Gaps

## Context

一期代码骨架已完成并通过真实环境验证，但存在多处功能缺口（见 proposal）。本设计说明关键修复的技术方案。现有技术栈：Go（hub/agent 共享 internal/）、SQLite（WAL）、WebSocket、React。

## Key Decisions

### 1. Cron 触发（Hub + Agent 双侧）
**问题**：`nextCronRun` / `scheduler.nextRun` 的 cron 分支只计算 next 时间且恒返回 `due=false`。
**方案**：改为"当前时刻是否已跨越某次触发点"判定：
- 记录每个 schedule 的 `lastRun` 时间戳（内存 + 可持久化到 DB 避免重启重复）。
- 在调度 tick 中：`next := spec.Next(lastRun)`，若 `!next.After(now)` 则本次到期，执行并更新 `lastRun = now`。
- Hub 侧同样引入基于 DB 的 last-fire 记录，保证 cron 在 Hub 与 Agent 均正确触发。
- 依赖：robfig/cron/v3 已引入，`spec.Next()` 语义用于计算下一次。

### 2. Manual Run 幂等
**问题**：唯一索引 `(task_id,node_id,scheduled_time)` 对 Manual（scheduled_time 空串）也唯一，导致同一 Task+Node 连续手动执行被丢弃。
**方案**：
- Scheduled 的 slot 幂等保留现有索引。
- Manual 执行改按 `Execution ID` 幂等：Agent `CreateExecution` 失败时仅当已存在同 ID 记录才忽略；不因同 slot 已存在 SUCCESS 记录而跳过。
- 通过 migration 将 Agent 端唯一索引调整：新增 partial index 仅对非空 scheduled_time 唯一；同 ID 用 PRIMARY KEY 去重。
- Hub 端 `dispatchToAgent` 无需改 slot 语义（Manual 本身无 slot）。

### 3. Remote Node Condition
**问题**：Agent `queryRemoteCondition` 是 stub，从不发送 REMOTE_STATE。
**方案**：
- 新增协议消息 `REMOTE_STATE_REQ`（Agent→Hub，含 target_node_id、property、task_id、要求）。
- Hub gateway 处理 REQ：查 node 在线状态 / 最近执行结果，返回 `REMOTE_STATE`（value、observed_at、ttl）。
- Agent 收到后参与条件求值；查询失败或超时 → Fail Closed（UNKNOWN→BLOCKED）。
- Hub 侧 `remote_state` 表已存在（Value/ObservedAt/ExpiresAt/TTL），用于缓存 Agent 主动上报的状态。
- 幂等与防阻塞：REQ 带 request id，Agent 对每个条件求值只发一次，设置短超时。

### 4. Tombstone 删除同步
**问题**：Hub 删除对象不产生 tombstone，Agent 本地残留。
**方案**：
- Hub 各 Manager Delete 时写入 `tombstones` 表（object_type、object_id、deleted_at、global_rev）。
- `BuildSyncResponse` 读取 tombstone 加入 `SyncResponsePayload.Tombstones`。
- Agent `applySync` 已实现 tombstone 消费（删除本地定义、停调度、保留 execution），无需改动 Agent，只需 Hub 产出。
- migration 新增 tombstones 表。

### 5. Realtime Log
**问题**：MsgLogChunk 仅协议定义，Agent 不发送，Hub `AppendLog` 空实现。
**方案**：
- Agent runner 在执行期间定期（如每 500ms 或每 N 字节）将累计 stdout/stderr 增量发送 `LOG_CHUNK`。
- Hub gateway `handleLogChunk` 追加到 `execution_logs` 表（chunk 累积），API 提供查询。
- 执行结束仍整包上报（作为最终一致）。
- 日志大小上限在 Agent 侧统一限制。

### 6. Task Retry
**方案**：Agent `executeTask` 增加重试循环：失败（非 canceled/timed_out 之外）且 Retry>0 时重新执行，最多 Retry 次；每次执行写独立 journal 或更新同一 execution（记录 attempts）。

### 7. systemd Unit Registry
**方案**：
- Agent 侧新增 `unit_registry` 表（application_id、unit_name），部署成功时登记。
- `operate()`/部署操作前校验 unit 已登记；未登记拒绝。
- Native/Container 的 systemctl 操作前置 Registry 检查。

### 8. Maintenance/Disabled 不被心跳覆盖
**方案**：心跳更新仅在当前状态为 online 时写 online；若为 maintenance/disabled 则仅更新 last_seen，不覆盖状态。需在 store 层 UpdateHeartbeat 中判断。

### 9. Agent 同步事务
**方案**：`applySync` 用 `db.Begin()` 包裹全部写入，成功 COMMIT 后再 SetGlobalRevision；失败 ROLLBACK。本地 store 增加事务方法。

### 10. Jitter
**方案**：Heartbeat/RevisionCheck ticker 周期加 ±20% 随机偏移；重连退避基础 2s + 随机 0~2s。

### 11. Docker Host Integration Inventory
**方案**：`collectInventory` 在 host_integration 模式读 `/host/proc/*`、`/host/etc/os-release` 等；读取失败返回 UNAVAILABLE。

### 12. 总日志上限
**方案**：`limitedBuffer` 的 `Write` 同时检查 single 与 total 上限。

### 13. READY 门禁
**方案**：`Agent.ready` 在 HELLO 对账（config + execution reconcile）完成后置 true；READY 前不响应 RunExecution 消息。

### 14. Interval Misfire SKIP
**方案**：`nextRun` 对 interval 判断错过的 slot 时，若 `MisfirePolicy==SKIP` 则直接跳过错过 slot，仅当恰好到点才触发。

### 15. Operator 权限
**方案**：`auth.HasPermission` 与 `api.canWrite` 调整——Operator 不授予 write 权限；保留 read/run/deploy/app_operate/view_logs。

### 16. WorkingDir / RunUser 接线
**方案**：Hub `dispatchToAgent` 在 RunExecutionPayload 携带 script/task 的 working_dir、run_user；Agent `executeTask` 传给 runner。

### 17-23. Web UI
**方案**：按页面逐个补齐表单、按钮、分区与统计；后端补充所需端点（Schedule POST/PUT 已有、Script revisions 列表、Artifact usage、Application deploy 已有、execution_logs 查询、dashboard stats）。

## Migration
- `tombstones` 表（Hub）
- Agent `unit_registry` 表
- Agent executions 唯一索引调整为 partial（非空 scheduled_time 唯一）
- （可选）`last_fire` 表存 cron 调度时间点

## Risks
- Cron 语义改变可能影响既有 interval/one-time 行为 → 重点回归。
- Remote Condition 查询若 Hub 与目标节点交互有延迟，条件求值可能超时 → 设置合理超时与缓存 TTL。
- Manual 幂等调整依赖索引 migration，需验证旧数据兼容。
