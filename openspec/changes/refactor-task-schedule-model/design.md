## Context

当前 Task 同时持有执行内容、目标节点和执行策略；Schedule 只引用 Task。Desired State 同步、Manual Run、节点详情任务匹配都从 `Task.Target` 读取。Execution 没有来源调度与创建时间。详见 `proposal.md` 和 `specs/`。

本次设计需要在不改 REST 路径和数据库表名的前提下，把目标与策略迁到 Schedule，并保留旧数据。Hub 与 Agent 通过版本化 JSON 协议通信，Web 由 Hub 同版本托管，因此可以接受一次协同升级，但不能丢失历史执行记录。

## Goals / Non-Goals

**Goals:**

- Task 只描述「执行什么」，Schedule 描述「在哪、何时、按什么策略执行」。
- 旧 Task 的目标与策略无损迁移到 Schedule；没有 Schedule 的旧 Task 生成禁用的 manual 迁移调度。
- Desired State 按 Schedule.Target 计算，Agent-owned 调度继续可离线运行。
- Manual Run 支持请求级临时目标与策略，不修改定义。
- Execution 增加可空 `schedule_id` 与 `created_at`，支持来源调度与执行过程时间线。
- Web 三页统一命名与视觉基线，调度页成为任务+节点+策略的唯一入口。

**Non-Goals:**

- 不重命名 REST 路径、数据库表名、Go 类型或内部对象名（Task/Schedule/Execution 保留）。
- 不引入 Workflow/DAG、任务依赖、优先级队列、并发槽位或告警策略。
- 不新增执行步骤表；过程时间线由现有时间字段与日志派生。
- 不改变 Scheduled Execution 的幂等 Key（Task ID + Node ID + Scheduled Time）。
- 不做整体视觉换肤；只做三页一致性整理。

## Decisions

### 1. 保留内部命名，只改领域字段与 UI 文案

Task/Schedule/Execution 的 Go 类型、表名、API 路径保持不变，避免全仓重命名和外部契约二次破坏。UI 使用「任务定义 / 调度计划 / 执行记录」，代码仍用 Task/Schedule/Execution。

替代方案：把内部对象改名为 TaskTemplate/Job/ExecutionResult。放弃原因：会同时改变 API、Agent 协议、DB 对象类型和大量测试，收益仅是命名一致。

### 2. Task 移除目标与策略，Schedule 增加目标与策略

Task 保留 `id/name/type/parameters/param_values/script_id/command/application_id/app_operation/condition/enabled/revision/timestamps`。删除 `target/timeout/retry/offline_policy/run_user/schedule`。

Schedule 增加：

- `name`：可选，默认由任务名 + 触发器摘要生成，用于区分同一任务的多个绑定。
- `target`：复用原 Task.Target 结构（node/group/label）。
- `timeout`：默认 300 秒，`0` 表示使用默认值，负数拒绝。
- `retry`：默认 0，负数拒绝。
- `run_user`：可空，空表示当前用户。
- 新增 `type=manual`：不自动触发，仅承载目标与策略，支持手动执行。

Schedule 继续保留 `task_id/type/expression/interval_sec/run_at/timezone/execution_owner/offline_policy/misfire_policy/enabled/revision/timestamps`。

替代方案：保留 Task.Target 作为「默认目标」，Schedule 可选覆盖。放弃原因：保留两套目标来源，Desired State 和 Manual Run 仍然歧义，无法达到本次拆分目标。

### 3. Manual Run 使用请求级目标与策略

`POST /api/tasks/{id}/run` 请求扩展为：

- `schedule_id`：调度计划页「立即执行」时传入；使用该 Schedule 的 Target/策略并写入 Execution 来源，trigger_type 仍为 manual。
- `target`：node/group/label 临时目标。
- `node_ids`：兼容旧请求，等价于构造 node Target。
- `params`：参数覆盖。
- `timeout/retry/run_user/offline_policy`：本次执行的策略，缺省用安全默认值（300s、0 次、当前用户、Hub Online Required）。

目标解析优先级：`schedule_id` > `target` > `node_ids` > 400。手动执行不写入 Task 或 Schedule。应用类 Task 仍走 `AppManager.DeployTask`，把策略传入执行记录与下发载荷。

替代方案：只允许从 Schedule 手动执行。放弃原因：任务定义在没有调度时无法试运行，且现有 Run Now 能力会退化。

### 4. Desired State 按 Schedule.Target 计算

对每个节点：

1. 计算节点所属 static/label group。
2. 遍历所有 Schedule，按 `schedule.target` 匹配节点。
3. 命中且 Task 存在、Task.Enabled 时，加入该 Task 与其 Script，并加入 Schedule（含禁用项，便于 Agent 收敛 enabled 状态）。
4. 无 Schedule 引用的 Task 不同步；Manual Run 通过 Dispatch 载荷携带完整 Task 定义执行。

`ScheduleManager.Create/Update/Delete` 继续在事务提交后发 Change Notification；Group/Label 成员变化仍通过现有 revision 机制触发重算。若 `NotifyChange` 当前按 Task.Target 计算受影响 Agent，则改为按 Schedule.Target 计算或退化为通知全部在线 Agent，由 Agent 按 revision 拉取。

替代方案：只同步 agent-owned 调度。放弃原因：切换 owner 时需要额外补偿；同步全部命中调度更简单，Agent 本地调度器本来就会过滤 owner。

### 5. manual 调度类型

`manual` 与 cron/interval/one_time/on_start 并列：

- 校验不要求 expression/run_at/interval。
- Hub 与 Agent 调度器都跳过 manual。
- 调度页允许创建，显示「仅手动」。
- 迁移时为「有目标但无调度」的旧 Task 创建一条禁用的 manual 调度，名称 `迁移：<task name>`，保留旧目标与策略。

替代方案：用禁用的 cron + 占位表达式。放弃原因：语义错误，用户误启用后会产生非预期触发。

### 6. Execution 增加来源与创建时间

Execution 增加：

- `schedule_id`：可空；Schedule 触发时写入，Manual Run 为空，历史记录为空。
- `created_at`：创建时间；旧记录回填 `COALESCE(start_time, scheduled_time, end_time, now)`。

Hub 创建 Execution 时写 `created_at`；调度触发写 `schedule_id`。Agent 本地 Journal 同步增加两列，并在 `EXECUTION_FINISHED` 中上报。Hub `MarkFinished` 对 Agent 上报做幂等 Upsert：若已有记录有 schedule_id 而上报为空，保留已有值；旧 Agent 不上报时不影响。

`schedule_id` 不进入幂等 Key。同一 Task + Node + Scheduled Time 仍只允许一次执行；同一任务多个 Schedule 在同一时刻撞槽位是既有约束，本次不改变。

替代方案：把 schedule_id 加入幂等 Key，允许两个调度同时触发。放弃原因：改变一期幂等语义，历史数据无法完整回填，且需要重新定义槽位唯一性。

### 7. API 与协议兼容

- REST 路径不变；Task/Schedule/Execution JSON 字段变化属于破坏性契约。
- 协议版本 +1；`RUN_EXECUTION` 增加 `schedule_id`、`created_at`，`EXECUTION_FINISHED` 增加 `schedule_id`、`created_at`。Hub 与 Agent 必须同版本升级。
- 旧 Agent 被新 Hub 拒绝；旧 Hub 不识别新 Agent。部署顺序为：备份 → 停 Hub/Agent → 升级 Hub → 升级 Agent → 启动。
- Web 与 Hub 同版本发布，不需要单独兼容旧 API。

### 8. 前端结构

- 保留现有路由路径，避免外部深链失效；只改菜单文案、页面标题、字段和布局。
- 抽取共享 `TargetEditor`（node/group/label 选择）供任务试运行与调度编辑器复用。
- 抽取共享 `ScheduleStrategyFields`（timeout/retry/run_user/offline）供调度编辑器与试运行复用。
- 调度列表可保留在 `features/catalog` 或迁移到空置的 `features/schedules`；若迁移，只移动列表与共享 `ListLayout`，不同时重写编辑器，控制单次改动范围。
- 三页统一使用 `NodeSteerHeader + Main`、共享 `DataTable`/`SectionCard`、共享加载/空态/错误态与 Radix Select。
- 执行详情新增「执行过程」「执行结果」「日志」三个分区；过程时间线由 `created_at/start_time/end_time/status/block_reason` 派生，不新增后端步骤模型。
- 来源调度通过 `schedule_id` 查询 `/api/schedules/{id}` 或从已加载的调度列表解析；找不到时显示「历史记录/未知来源」。

## Risks / Trade-offs

- [旧数据迁移失败或丢失目标] → 迁移前备份 Hub DB 与 Agent 数据目录；迁移在单个事务内完成；增加旧 schema fixture 测试；失败时启动报错而不是继续运行。
- [Desired State 重算遗漏，Agent 保留旧任务] → 迁移时 bump global revision 触发全量对账；增加测试覆盖 schedule target 变更、禁用、删除和 group/label 动态成员。
- [Hub/Agent 协议不兼容] → 协议版本 +1；部署文档明确协同升级；旧 Agent 连接被拒绝并给出明确错误。
- [Rollback 后旧二进制无法读取新 Task JSON] → 文档明确 rollback 需要恢复 DB 备份，不支持旧 Hub 直接读新库。
- [manual 迁移调度被误启用] → 迁移调度默认禁用，名称带「迁移」前缀；调度列表显示类型「仅手动」，manual 永远不会自动触发。
- [同任务多调度同槽位冲突] → 保持既有幂等 Key；在执行记录展示来源调度，便于定位；若未来需要并行触发，单独设计 key 变更。
- [前端大范围文案/字段改动导致测试失效] → 先改 i18n 与类型，再逐页改组件；同步更新单测与 E2E 选择器；最后跑全量前端测试与构建。
- [样式整理扩大为整体改版] → 限定为三页共享组件与状态一致性，不做主题、字体、品牌色重设计。

## Migration Plan

1. 备份 Hub SQLite 与所有 Agent 数据目录。
2. 发布新 Hub：启动时执行 schema 迁移与数据迁移。
   - `executions` 增加 `schedule_id`、`created_at` 并回填。
   - 读取旧 Task 定义，把 target/timeout/retry/run_user/offline_policy 复制到其全部 Schedule。
   - 重写 Task JSON，移除旧字段；重写 Schedule JSON，补齐新字段。
   - 为有目标但无 Schedule 的 Task 创建禁用的 manual 调度。
   - 提升 Global Revision，触发 Agent 全量对账。
3. 发布新 Agent：本地 schema 增加两列；连接新 Hub 后全量同步新 Task/Schedule 定义。
4. 验证：任务数、调度数、迁移调度数、执行记录数；抽查 target 复制；抽查 agent-owned 调度仍可离线触发。
5. 回滚：停止服务，恢复 Hub DB 备份与旧二进制；Agent 恢复旧数据目录或重新同步。旧 Hub 不支持直接读取新库。

## Open Questions

- 是否在未来允许同一 Task+Node 的多个 Schedule 在同一时刻分别执行？当前保持既有幂等 Key，不在本次改变。
- 是否为 Schedule 增加参数覆盖（当前参数仍来自 Task.ParamValues 与 Manual Run 请求）？本次不纳入。
