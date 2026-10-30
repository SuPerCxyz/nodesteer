## Context

当前 `Task.Condition` 支持 `local`、`remote` 和 `and`，由 Agent 在执行前统一评估。用户已确认混合模型：本地条件属于任务固有安全前提，远程条件属于调度依赖。详见 `proposal.md` 与 `specs/`。

本设计复用现有 `models.Condition` 类型，不新增条件类型；通过字段归属校验和运行时合并实现拆分。

## Goals / Non-Goals

**Goals:**

- Task 只允许本地条件，Schedule 只允许远程条件。
- 调度执行合并两者；任务页试运行只使用本地条件。
- 应用类任务手动执行不再绕过条件。
- Execution 记录 `schedule_revision`，可追溯远程条件/策略版本。
- 旧混合条件无损拆分并迁移。

**Non-Goals:**

- 不新增条件类型、表达式语言或工作流 DAG。
- 不做条件结果快照；`schedule_revision` 只标识版本。
- 不改变现有本地/远程条件的评估语义（false=SKIPPED，unknown=BLOCKED）。
- 不把远程条件引入任务页临时试运行。

## Decisions

### 1. 复用 Condition 类型，按 scope 校验

Task 与 Schedule 继续使用 `*models.Condition`。新增校验 scope：

- `local`：只允许 `local` 或本地 `and`。
- `remote`：只允许 `remote` 或远程 `and`。
- `and` 递归校验所有子条件，混合 scope 直接 400。

替代方案：拆成 `LocalCondition`/`RemoteCondition` 两个字段。放弃原因：现有 UI、JSON 和评估器都基于 `Condition`，拆字段会造成更大迁移和重复类型。

### 2. 运行时合并为一个 AND 树

`models.MergeConditions(local, remote)` 把两个非空条件合并为 `and`，本地在前、远程在后。Hub 在 `dispatchToAgent` 前合并；Agent-owned 调度在 `TriggerSchedule` 中合并。继续使用 `RunExecutionPayload.Condition` 单字段，不新增并行条件字段，保持条件评估器不变。

替代方案：payload 增加 `local_condition`/`remote_condition` 两个字段。放弃原因：需要协议/评估器双路径，收益只是更细的 block_reason；当前 `and` 已能正确评估。

### 3. 手动执行条件范围

- 任务页 `/tasks/:id/run`：只传 Task.Condition。
- 调度页「立即执行」：传 Task.Condition + Schedule.Condition，并记录 schedule_id/schedule_revision。
- 应用类任务手动执行从 `AppManager.DeployTask`（DEPLOY_REQUEST）改为 `ExecutionManager.RunManual`（RUN_EXECUTION），与调度应用路径一致，确保条件评估。

替代方案：给 DEPLOY_REQUEST 增加条件字段并在 Agent 侧重复评估。放弃原因：两套执行路径容易再次分叉。

### 4. Execution 增加 schedule_revision

Hub `executions` 与 Agent 本地 `executions` 增加 `schedule_revision INTEGER NOT NULL DEFAULT 0`。调度触发、调度页立即执行写入；任务页手动执行与历史记录为 0。协议增加可选字段，版本升到 3，避免旧 Agent 忽略 Schedule.Condition。

替代方案：不加版本字段，只靠 schedule_id。放弃原因：调度条件/策略修改后无法追溯历史执行使用的版本。

### 5. 数据迁移

新增迁移标记 `data_migration_condition_split_v1`：

1. 读取旧 Task.Condition，递归拆分为 local/remote 两棵条件树。
2. Task 只保留 local；remote 复制到该 Task 的全部 Schedule（已有条件时合并为 AND）。
3. 有目标但无 Schedule 的旧 Task，remote 写入已有/manual 迁移调度；确实无法绑定时记录告警并保留 local。
4. 重写 Task/Schedule definition_json，提升 Revision，写 Change Log。
5. 迁移幂等；失败阻止 Hub 启动。

### 6. 前端

- 从任务编辑器中移除远程条件表单，只保留本地条件；任务详情条件区只展示本地条件。
- 调度计划编辑器新增远程条件表单，复用节点/任务查询。
- 执行详情在 `schedule_revision > 0` 时展示来源调度修订号。
- 任务试运行页可展示只读本地条件摘要，不提供远程条件编辑。

## Risks / Trade-offs

- [混合条件拆分丢失] → 递归拆分 + 迁移 fixture 覆盖 local-only/remote-only/mixed/multiple schedules/无调度。
- [旧 Agent 忽略 Schedule.Condition] → 协议版本升到 3，Hub 拒绝旧版本；部署文档要求协同升级。
- [应用类任务执行路径变化] → 统一走 RUN_EXECUTION 并回归应用部署/操作、条件、日志、来源调度。
- [Task API 破坏性变化] → 文档标注 breaking；错误信息明确指出远程条件应配置在调度计划。
- [条件版本追溯仍不完整] → 记录 schedule_revision；不保存完整条件快照，历史展示以当前定义+版本号为准。

## Migration Plan

1. 备份 Hub/Agent 数据，停止服务。
2. 升级 Hub：应用 schema v11（execution.schedule_revision）与条件拆分数据迁移。
3. 验证 Task 仅本地条件、Schedule 远程条件、迁移 manual 调度、Revision/Change Log。
4. 升级 Agent：本地 executions 增加 schedule_revision，协议版本 3。
5. 验证调度/手动执行条件与来源修订号。
6. 回滚：恢复 Hub DB 与旧二进制；旧库不支持直接读取新库。
