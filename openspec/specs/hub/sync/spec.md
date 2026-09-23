# hub/sync Specification

## Purpose
Hub 维护 Global/Object Revision、Change Log，通过 Realtime Notification、Periodic Revision Check 与 Reconnect Reconciliation 三层保障 Agent Desired State 最终一致。
## Requirements
### Requirement: Realtime Change Notification
系统 SHALL 在 Desired State 变更（DB Commit 之后）向相关在线 Agent 推送通知，通知仅含 Object Type、Object ID、Object Revision、Global Revision，不含完整配置载荷。

#### Scenario: 提交后通知
- **WHEN** Hub 完成 Desired State 变更事务并 Commit
- **THEN** Hub 向受影响 Agent 推送 CHANGE_NOTIFICATION 且绝不在 Commit 前推送

#### Scenario: 通知语义允许丢包乱序
- **WHEN** 通知重复、丢失、乱序或延迟
- **THEN** 最终状态仍由 Hub Current Desired State 通过 Revision/Reconciliation 收敛

### Requirement: Periodic Revision Check
系统 SHALL 支持 Agent 周期性（默认 30~60 秒含 Jitter）上报 Local Global Revision，Hub 返回 Desired Global Revision，不一致则触发增量 Sync。

#### Scenario: Revision 不一致触发 Sync
- **WHEN** Agent 周期校验发现 Local Global Revision 小于 Hub Desired Global Revision
- **THEN** Agent 发起增量 Sync 拉取缺失对象

### Requirement: Reconnect Reconciliation
系统 SHALL 在 Agent 重连时按 CONNECT→AUTH→HELLO→REPORT LOCAL REVISION→CONFIG RECONCILIATION→EXECUTION RECONCILIATION→READY 顺序对账；对账完成前不得视为 READY。

#### Scenario: 重连完成对账
- **WHEN** Agent 重连并完成配置与执行对账
- **THEN** 双方状态一致后标记 READY 并恢复实时通道

#### Scenario: 未完成对账
- **WHEN** Agent 重连但对账尚未完成
- **THEN** Agent 不被视为 READY，不接收新执行

### Requirement: Revision Change Log
系统 SHALL 保存轻量 Change Log（global_revision、object_type、object_id、object_revision、operation、created_at），支持 since=<revision> 增量查询，仅保留合理窗口。

#### Scenario: 增量查询
- **WHEN** Agent 请求 SYNC_REQUEST since=<revision>
- **THEN** Hub 返回窗口内变化的对象的最终 Desired State

#### Scenario: Revision 超出窗口
- **WHEN** Agent 的 Revision 超出 Change Log 保留窗口
- **THEN** Hub 返回 FULL_RESYNC_REQUIRED

### Requirement: Atomic Sync
系统 SHALL 保证 Agent 只有配置成功校验并持久化后才能前进 Revision；任何失败必须回滚，不得出现半同步状态。

#### Scenario: 同步事务回滚
- **WHEN** Task 依赖的 Script 获取或校验失败
- **THEN** Agent 不写入任何部分状态，Revision 不前进

### Requirement: Sync 状态跟踪
系统 SHALL 记录每个 Agent 的 sync_state（Local Global Revision、最近同步时间、状态）。

#### Scenario: 查看 Agent 同步状态
- **WHEN** Hub 收到 Agent 周期 Revision 上报
- **THEN** 更新该 Agent 的 sync_state 记录

### Requirement: 通知仅传输元数据
系统 SHALL 保证 Binary/Artifact Payload 不通过控制长连接传输，Artifact 通过 HTTP(S) 下载。

#### Scenario: Artifact 下载
- **WHEN** Agent 需要获取 Artifact
- **THEN** Agent 通过 HTTP(S) 下载二进制而非控制长连接

