# Proposal: Complete Phase 1 Gaps

## Why

对照 `docs/PRODUCT.md` 一期 32 项核心目标逐项核对代码，发现多项功能存在"未实现 / 部分实现 / 不符合文档要求"的缺口：Cron 调度双侧失效、Manual Run 幂等缺陷、Remote Node Condition 与 Tombstone 未实现、Realtime Log / Retry / systemd Unit Registry 缺失，以及多处一致性与前端 UI 缺口。这些属于一期必须交付的范围，需补齐并在 KVM 真实环境全量验证。

## What Changes

- **BREAKING**（行为修复）：
  - 修复 Cron Schedule 在 Hub 与 Agent 双侧不触发的问题
  - 修复 Manual Run 幂等缺陷：同一 Task+Node 连续手动执行被静默丢弃（原实现按 `(task_id,node_id,scheduled_time)` 唯一，Manual 的 scheduled_time 为空导致撞索引）
  - 实现 Remote Node Condition 全链路（REMOTE_STATE 上报/查询/过期 UNKNOWN→BLOCKED）
  - 实现 Tombstone 删除同步（Hub 产生、Agent 消费）
  - 实现 Realtime Log 分片（Agent 发送 LOG_CHUNK，Hub 存储）
  - 实现 Task Retry 重试循环
  - 实现 systemd Unit Registry（只允许操作已登记 Unit）
  - Maintenance/Disabled 状态不被心跳覆盖回 online
  - Agent 同步改为事务（BEGIN/COMMIT），消除半同步状态
  - 心跳/周期校验/重连退避加入 Jitter
  - Docker Host Integration Inventory 读 `/host` 而非容器自身
  - 总日志上限（stdout+stderr total）生效
  - Agent 对账完成前不视为 READY（READY 门禁）
  - Interval Misfire SKIP 策略生效
  - Operator 权限按文档收紧（移除创建/编辑/删除定义权限）
  - Script.WorkingDir / RunUser 接线到执行

- 新增（前端 UI）：
  - Schedule 创建/编辑页
  - Application Deploy/Upgrade/Start/Stop/Restart 操作按钮
  - Script Clone 与 Revision History 列表
  - Task Parameters / Condition 编辑
  - Node Detail 补齐 Tasks / Schedules / Managed Applications / Sync 分区
  - Dashboard Sync / Application 健康统计
  - Execution Duration、Artifact Usage 展示

- 不纳入：`/api/settings` 运行时生效（保持"端点存在 + 存储"，仅按文档范围）。

## Capabilities

- **Modified**: hub-core（Schedule/Cron、Remote Condition、Tombstone、Execution、权限、Audit）
- **Modified**: agent-core（同步事务、READY、Jitter、幂等、Retry）
- **Modified**: agent-applications（Unit Registry、日志上限）
- **Modified**: web-ui（页面补齐）
