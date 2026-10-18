## ADDED Requirements

### Requirement: 重新纳管恢复节点状态
重新纳管 SHALL 持久化 pending/sync pending、新凭证和撤销标记清除，同时保留 Node/Agent 身份、标签与历史；普通重连 SHALL 不覆盖人工 maintenance/disabled 状态。

#### Scenario: 撤销后同节点重新纳管
- **WHEN** 管理员重新纳管已撤销的同一节点
- **THEN** 旧凭证拒绝，新凭证连接后成为 online/synced 且可执行，无重复节点，标签和历史保留
