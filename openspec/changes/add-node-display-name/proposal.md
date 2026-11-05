## Why

节点当前只有 `hostname`，而 `hostname` 会在重新注册路径被 Agent 上报值覆盖；用户需要给节点设置稳定的显示名（例如“生产数据库 01”），同时不能破坏 Agent 身份、重新纳管和既有 API 语义。`display_name` 与 `hostname` 分离后，Operator 可以重命名节点，Viewer 保持只读，所有页面统一展示显示名并在空值时回退 hostname。

## What Changes

- Hub 节点表新增 `display_name`，默认空字符串；创建、重新注册、HELLO 元数据刷新和普通 `UpsertNode` 均不覆盖该字段。
- 节点 API 返回 `display_name`；更新接口支持 `display_name`，要求 Operator 及以上权限，Viewer 只读。
- 校验：去首尾空白后最多 64 个字符，拒绝控制字符；空值表示清除显示名并回退 hostname；不要求唯一。
- 审计新增 `node.rename`，记录节点 ID、旧值、新值和操作者；未变化不写审计。
- 前端新增 `nodeDisplayName` helper，统一用于节点列表/详情、执行、告警、传输、概览、目标选择等所有用户可见节点名称；详情页增加重命名入口，空值回退 `hostname`。
- 数据库迁移预留 v26（`nodes.display_name`），与 `add-agent-inbound-connection` 的 v27 解耦。
- 更新测试与 PRODUCT/ARCHITECTURE 文档中的节点命名说明。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `hub/nodes`：新增 display_name 字段、默认/保留语义、API 与校验。
- `hub/access-control`：display_name 字段级权限与 `node.rename` 审计。
- `web-ui`：统一节点显示名 helper、所有页面回退规则和重命名入口。
- `node-enrollment`：重新纳管/重新注册保留 display_name，新节点默认空。

## Impact

- 后端：`internal/models`、`internal/store/sqlite_users_nodes.go`、`internal/store/migrate.go`、`internal/hub/node.go`、`internal/hub/api/api.go`、审计动作。
- 前端：`web/src/lib`（helper）、`web/src/features/catalog`、`web/src/features/executions`、`web/src/features/transfers`、`web/src/features/overview`、`web/src/features/monitoring`、`web/src/features/tasks`、`web/src/locales`。
- 数据：`nodes.display_name` 新增列，默认空；不迁移、不改写既有 hostname。
- 兼容性：旧 API 客户端忽略新字段即可；旧 Agent 不感知 display_name；重新注册不会丢失显示名。
- 风险：显示名可重复，不能作为唯一标识；审计和前端必须继续以 node ID 为准。
