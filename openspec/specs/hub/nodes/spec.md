# hub/nodes Specification

## Purpose
Hub 管理 Agent Node 的注册、心跳、在线状态、Inventory、Capability、Label 与 Group，作为节点生命周期与同步调度的基础。
## Requirements
### Requirement: Node 列表与详情 API
系统 SHALL 提供 Node 列表与详情 API，至少包含 Node ID、Hostname、IP、OS、Architecture、Labels、Agent Version、Deployment Mode、Host Integration、Sync Status、Last Seen。

#### Scenario: 查询节点列表
- **WHEN** 调用 GET /api/nodes
- **THEN** 返回包含上述字段的 Node 列表

