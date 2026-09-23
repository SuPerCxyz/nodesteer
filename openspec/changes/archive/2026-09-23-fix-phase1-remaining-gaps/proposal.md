# Proposal: Fix Phase 1 Remaining Gaps

## Why

对照 `docs/PRODUCT.md` 一期范围进行三路（Agent / Hub / Web UI）代码核查后，发现仍存在多项未实现或部分实现项，其中 Remote Node Condition 的 Fail Open 违反核心不变量 10（UNKNOWN → BLOCKED），Run User 从未接线，Agent-owned 调度的 Application Deploy/Upgrade 链路不可用。本次全部修复。

## What Changes

- **BREAKING**（正确性）：
  - Remote Condition 返回 `UNKNOWN` 时按 Fail Closed 处理（BLOCKED），禁止把未知值当普通值参与比较
  - Run User 全链路接线：模型字段 → 协议载荷 → Hub 下发 → Agent 执行设置
  - Agent-owned 调度的 Application Deploy/Upgrade 可用（补全 Agent 本地定义解析与 artifact 下载链路）
  - Artifact Prefetch 覆盖 Scheduled / Offline Deployment
  - Settings（heartbeat_interval_sec 等）运行期被消费，不再"存而不用"
  - 实时日志 stderr 分片正确标记 stream
  - Inventory 采集 OSVersion / CPU MHz
  - Application 部署做 Capability 校验
  - Rollback 恢复 config/unit 并做回滚后 Health Check
- 新增（前端）：
  - Node Detail Managed Applications 改用真实分配数据
  - Application Versions / Environment 输入 / 真实 Health 状态
  - Task Schedule 区块与 Execution History
  - Script Parameters / Environment 编辑、Group 编辑、Condition remote/and 编辑
- 一致性：
  - Hub 侧 Sync Error 状态、调度 tick 精度、changelog 窗口清理

## Capabilities

- **Modified**: hub-core（Remote State Fail Closed、RunUser 下发、Prefetch、Settings 消费、Capability 校验、changelog、调度 tick）
- **Modified**: agent-core（RunUser、stderr 流标记、Inventory、Agent-owned app deploy、rollback）
- **Modified**: web-ui（Node Detail / Application / Task / Script / Group / Condition UI）
