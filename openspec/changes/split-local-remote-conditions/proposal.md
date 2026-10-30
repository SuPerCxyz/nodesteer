## Why

当前 Task 同时承载本地资源条件和远程节点依赖条件。本地条件属于任务自身的安全执行前提，应随任务复用；远程条件表达的是任务之间的调度依赖，应随调度计划的目标与时机变化。把两者混在 Task 中会导致同一任务在不同调度下重复配置，或让远程依赖错误地影响所有手动执行。

## What Changes

- **BREAKING**：Task.Condition 只允许本地条件（`local` 或本地 `and`），远程条件写入 Task 返回 400。
- Schedule 新增 `condition`，只允许远程条件（`remote` 或远程 `and`）；本地条件写入 Schedule 返回 400。
- 执行时把 Task 本地条件与 Schedule 远程条件合并为一个 `and`，本地先评估、远程后评估：
  - 调度触发、调度页「立即执行」：本地 + 远程。
  - 任务页「试运行」：只评估本地条件。
- 应用类任务手动执行统一走 `RUN_EXECUTION` 路径，修复当前 `DEPLOY_REQUEST` 绕过条件评估的问题。
- **BREAKING**：Execution 增加 `schedule_revision`，记录触发时使用的调度版本；协议版本升到 3。
- 数据迁移：把旧 Task 条件拆成 local/remote；远程部分复制到该 Task 的全部 Schedule；有目标无调度的旧任务写入迁移 manual 调度；混合 `and` 递归拆分。
- 前端：任务编辑器只保留本地条件；调度计划编辑器增加远程条件；执行详情展示来源调度修订号。
- 文档与测试覆盖拆分、迁移、手动/调度执行条件、应用类任务条件和协议升级。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `hub/definitions`：Task/Schedule 条件字段归属与校验。
- `hub/core`：条件合并、评估顺序与手动执行语义。
- `hub/executions`：Execution 增加调度修订号。
- `hub/api`：Task/Schedule/Run/Execution 契约变化。
- `agent/core`：Agent 合并并评估本地/远程条件。
- `agent/executions`：本地 Journal 与上报增加调度修订号。
- `web-ui`：任务/调度条件编辑与执行详情展示。

## Impact

- 后端：`internal/models`、`internal/hub/{validation,definitions,schedule,execution,application}`、`internal/hub/api`、`internal/store`（迁移/Execution 列）、`internal/protocol`、`internal/agent`（调度、Journal、执行）。
- 前端：`web/src/features/{tasks,editors,catalog,executions,shared}`、`web/src/lib`、`web/src/locales`。
- 数据：Hub/Agent `executions` 增加 `schedule_revision`；Hub 增加条件拆分迁移版本。
- 文档：PRODUCT、ARCHITECTURE、TEST_PLAN、UI_FULL_TEST_PLAN、TRACEABILITY、DEPLOYMENT。
- 兼容性：Task 远程条件与 Schedule 本地条件成为非法输入；协议版本 3，Hub/Agent 必须协同升级。
