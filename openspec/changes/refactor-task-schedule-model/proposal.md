## Why

当前 Task 同时承担「执行什么」和「在哪些节点执行」，导致同一命令要绑定不同节点集合时必须复制任务；Schedule 只引用 Task，节点目标和执行策略分散在两处，Execution 也无法反查来源调度。把定义、调度绑定和执行记录拆开后，任务定义可复用，调度计划成为唯一的「任务 + 节点 + 策略 + 触发器」绑定入口，执行记录同时承载过程与结果。

## What Changes

- **BREAKING**：Task 移除 `target`、`timeout`、`retry`、`offline_policy`、`run_user` 和无用的 `schedule` 字段；Task 只描述执行内容（类型、命令/脚本/应用、参数、条件、启用状态、修订号）。
- **BREAKING**：Schedule 新增 `name`、`target`（node/group/label）、`timeout`、`retry`、`run_user`；保留 `offline_policy`、`misfire_policy`、`execution_owner`、触发器、时区、启用状态。Schedule 成为任务与节点绑定及执行策略的唯一来源。
- 新增 `manual` 调度类型：不自动触发，只承载「任务 + 目标 + 策略」的仅手动绑定；用于旧数据迁移和需要固定目标的场景。
- Manual Run 改为请求级临时目标与策略：任务定义页「试运行」可选择节点/分组/标签，不落库；调度计划页「立即执行」使用调度自己的目标与策略。
- Desired State 同步改为按 Schedule 目标解析；任务定义不再直接同步到节点。
- **BREAKING**：Execution 新增可空 `schedule_id` 和 `created_at`；执行记录可展示来源调度、创建/开始/结束时间线，并在详情页区分「执行过程」与「执行结果」。
- Web UI 统一命名为「任务定义 / 调度计划 / 执行记录」，菜单、页面标题、列表列、表单和空态同步调整；三页做一致性样式整理。
- 数据迁移：旧 Task 的 `target`、`timeout`、`retry`、`run_user`、`offline_policy` 复制到其全部 Schedule；有目标但没有 Schedule 的旧 Task 生成一条禁用的 `manual` 迁移调度，保留原目标。历史 Execution 的 `schedule_id` 留空，`created_at` 用可用时间回填。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `hub/definitions`：Task 与 Schedule 的字段、校验和绑定关系调整。
- `hub/core`：调度触发、Manual Run、执行策略来源调整。
- `hub/executions`：Execution 新增来源调度和创建时间，过程/结果字段语义明确。
- `hub/sync`：Desired State 改为按 Schedule 目标计算。
- `hub/api`：Task/Schedule/Execution 的 REST 契约与 Run 请求调整。
- `agent/core`：Agent 本地调度器使用 Schedule 上的目标与策略。
- `agent/executions`：本地 Execution 记录来源调度和创建时间，并与 Hub 协议同步。
- `web-ui`：任务/调度/执行页面结构、字段、文案和执行过程展示。
- `web-ui/layout`：侧边栏命名、页面标题与三页一致性布局。

## Impact

- 后端：`internal/models`、`internal/hub/{definitions,schedule,execution,sync,node}`、`internal/hub/api`、`internal/store`（迁移/查询）、`internal/agent`（调度器、执行记录、协议）、`internal/protocol`（协议版本与 payload）。
- 前端：`web/src/locales`、`web/src/components/layout`、`web/src/features/{tasks,schedules,executions,catalog,editors}`、`web/src/lib`、`web/src/routes`。
- 数据：Hub 与 Agent SQLite 的 `executions` 表新增 `schedule_id`、`created_at`；Hub 增加数据迁移版本。
- 文档与验收：`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`、`openspec/specs`、`docs/TEST_PLAN.md`、`docs/UI_FULL_TEST_PLAN.md`、`docs/TRACEABILITY_MATRIX.md`、`web/e2e`。
- 兼容性：API JSON 中 `target` 从 Task 移到 Schedule，属于破坏性契约变更；项目无 API 版本前缀，需要 Hub、Agent、Web 协同升级。
