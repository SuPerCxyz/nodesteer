## Why

NodeSteer 已能安全执行节点任务，但缺少由自身采集、存储和展示的节点指标，监控与自动化仍依赖独立的 Nocturne 服务，带来部署、身份、许可和历史一致性成本。需要把指标采集并入同一个 NodeSteer Agent，由 Hub 原生保存时序数据，并在同一界面完成观察、判断和操作。

## What Changes

- 同一个 NodeSteer Agent 新增 Linux 节点指标采集（CPU、内存、负载、磁盘、网络），默认 30s；断线时有界缓冲并按序重传；声明 `node_metrics` 能力。旧 Agent 不支持时显示不可用，不伪造数据。
- 新增 `NODE_METRICS` 批量协议，样本包含 `node_id`、`sequence`、`timestamp`、`metric`、`dimensions`、`value`、`quality`。
- Hub 使用 SQLite 原生时序存储；默认 raw 7d、1m 90d、5m 1y、1h 2y 的聚合与保留策略，提供聚合、保留和查询 API（overview/series/metrics/latest）。
- Web 总览直接遍历 NodeSteer 节点，展示在线状态、CPU/内存/负载/磁盘/网络、最近采样和告警；点击查看历史；节点操作跳转 `/agents/{id}`；不再有“监控节点绑定”。
- `node_metric` 规则改用 NodeSteer 原生样本；外部 HTTP(S)、TCP、ICMP 探测保留为独立监控对象。规则的新鲜度、gap、去重、冷却、失败暂停、验证和补偿语义不变。
- 移除 Nocturne 绑定、代理、页面入口、环境变量和部署文档；已有 Nocturne 历史不迁移、不回填、不在新界面展示。
- 更新产品与架构基线；保持现有 Desired State、Schedule、幂等、Journal 和 fail-closed 不变量。

## Capabilities

### New Capabilities

- `unified-monitoring`: 原生节点指标、外部服务探测、历史数据与 NodeSteer 风格统一界面。
- `monitoring-automation`: 规则触发、节点任务编排及明确配置的验证、补偿和恢复。

### Modified Capabilities

无：既有任务及调度契约继续适用，新能力通过其入口执行。

## Impact

Hub/Agent、协议、SQLite 时序存储、HTTP API、Web、持久化、测试和产品架构文档均受影响。本次是架构变更：NodeSteer 不再依赖 Nocturne，已有 Nocturne 历史不迁移；NodeSteer 仍为 MIT，不复制 AGPL 代码。旧 Agent 可继续执行任务，但未声明 `node_metrics` 时监控显示不可用。

## Implementation Status

当前工作树已完成 Agent 指标采集、`NODE_METRICS` 协议和 `node_metrics` 能力；Hub schema v23、原生存储/聚合/后台清理、gateway ingest、`/api/monitoring/nodes/*` 查询 API、`node_metric` 原生规则和前端总览/历史均已接入；Nocturne 后端、路由、配置和绑定已移除，遗留 `monitoring_node_bindings` 表保留但不参与新链路。最终回归、真实大规模压测、ACK/重传确认、overview N+1/索引/节点删除清理和完整 E2E 仍在进行。逐项状态见 `tasks.md`，未验证项不得写成已交付。
