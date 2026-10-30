## 1. 基线与安全

- [x] 1.1 记录当前 Task/Schedule/Execution 的 API JSON、DB schema、前端页面与测试基线，确认迁移前备份路径
- [x] 1.2 确认 Hub/Agent 协同升级顺序与回滚步骤，写入部署说明草稿
- [x] 1.3 为旧 schema 准备迁移测试 fixture（含 Task.Target、无 Schedule 的 Task、已有 Schedule、历史 Execution）

## 2. 后端模型与校验

- [x] 2.1 修改 `models.Task`：移除 Target/Timeout/Retry/OfflinePolicy/RunUser/Schedule
- [x] 2.2 修改 `models.Schedule`：增加 Name/Target/Timeout/Retry/RunUser，新增 `manual` 类型常量
- [x] 2.3 修改 `models.Execution`：增加可空 ScheduleID 与 CreatedAt
- [x] 2.4 迁移 Task 目标校验到 Schedule，复用 node/group/label 校验并补充 timeout/retry/name/manual 校验
- [x] 2.5 调整 TaskManager：删除目标/策略默认值与已删除节点修复逻辑，保留类型/脚本/应用/条件/参数校验
- [x] 2.6 调整 ScheduleManager：Create/Update 写入新字段、默认值与 revision，处理 manual 类型
- [x] 2.7 调整 ExecutionManager：创建时写 CreatedAt/ScheduleID，Agent 上报幂等时保留已有来源

## 3. 存储与数据迁移

- [x] 3.1 提升 Hub schema 版本，为 `executions` 增加 `schedule_id`、`created_at` 及必要索引
- [x] 3.2 实现旧 Task → 全部 Schedule 的 target/timeout/retry/run_user/offline_policy 复制
- [x] 3.3 实现 Task/Schedule `definition_json` 重写，移除 Task 旧字段并补齐 Schedule 新字段
- [x] 3.4 为有 Target 但无 Schedule 的旧 Task 创建禁用的 manual 迁移调度
- [x] 3.5 回填 Execution `created_at`，历史 `schedule_id` 保持为空
- [x] 3.6 迁移后提升 Global Revision，确保 Agent 全量对账
- [x] 3.7 调整 Agent 本地 schema（ScheduleID/CreatedAt）与迁移
- [x] 3.8 增加迁移测试：字段复制、manual 调度、历史执行、幂等重跑

## 4. Hub API 与执行链路

- [x] 4.1 调整 `POST /api/tasks/{id}/run`：接受 target/node_ids/strategy，缺少目标返回 400
- [x] 4.2 调整 Manual Run 执行链路，把 timeout/retry/run_user/offline_policy 传入 Execution 与 Dispatch
- [x] 4.3 调整应用类 Task 的 DeployTask/Operation，传递调度或请求策略
- [x] 4.4 调整 Schedule API 响应/校验：name、target、strategy、manual
- [x] 4.5 调整 Execution API 响应：schedule_id、created_at
- [x] 4.6 调整 Task API 响应：不再返回 target/timeout/retry/run_user/offline_policy/schedule
- [x] 4.7 更新 API 契约测试：Task 旧字段不出现、Schedule 缺目标 400、Run 目标解析、Execution 来源字段

## 5. 同步、调度与协议

- [x] 5.1 改写 Desired State 计算：按 Schedule.Target 匹配节点并收集 Task/Script/Schedule
- [x] 5.2 确保无 Schedule 的 Task 不同步，Manual Run 仍可 Dispatch 执行
- [x] 5.3 调整 Change Notification 的受影响 Agent 计算或退化为全量通知 + revision 拉取
- [x] 5.4 调整 Hub 调度器：按 Schedule.Target 解析节点，manual 跳过，策略来自 Schedule
- [x] 5.5 调整 Agent Local Scheduler：按 Schedule.Target/策略触发，manual 跳过
- [x] 5.6 调整 Agent TriggerSchedule/RunExecution：使用 Schedule 策略并写入 ScheduleID/CreatedAt
- [x] 5.7 协议版本 +1，RUN_EXECUTION/EXECUTION_FINISHED 增加 schedule_id/created_at
- [x] 5.8 更新同步/调度/Agent 测试：target 变更、禁用、删除、manual、group/label 动态成员

## 6. 前端类型、文案与共享组件

- [x] 6.1 更新 `web/src/lib/api.ts` Task/Schedule/Execution 类型与 Run 请求类型
- [x] 6.2 更新 `web/src/lib/task-payload.ts` 与调度 payload，移除 Task 目标/策略字段
- [x] 6.3 更新 zh/en locale：任务定义/调度计划/执行记录、目标/策略/manual/时间线/来源调度文案
- [x] 6.4 抽取共享 TargetEditor，供任务试运行与调度编辑器复用
- [x] 6.5 抽取共享 ScheduleStrategyFields（timeout/retry/run_user/offline）
- [x] 6.6 统一三页页面头、工具栏、DataTable/SectionCard、加载/空态/错误态与 Select 控件

## 7. 前端任务定义页

- [x] 7.1 任务列表移除目标/超时/离线策略列，增加关联调度数量或入口
- [x] 7.2 任务编辑器移除目标、timeout/retry/run_user/offline、Schedule 区块
- [x] 7.3 任务详情移除目标分区，保留概览/定义/条件/关联调度（只读）/执行历史
- [x] 7.4 任务详情/列表「试运行」跳转适配新 Run 请求
- [x] 7.5 Run 页面增加目标选择（node/group/label）、策略字段与参数覆盖
- [x] 7.6 更新任务相关单测与深链测试

## 8. 前端调度计划页

- [x] 8.1 调度列表增加名称、任务、目标摘要、策略、下次触发/立即执行入口
- [x] 8.2 调度编辑器增加名称、任务、目标、timeout/retry/run_user，支持 manual 类型
- [x] 8.3 调度编辑器加载 nodes/groups 并复用 TargetEditor
- [x] 8.4 调度列表/编辑器保存后刷新与错误反馈保持一致
- [x] 8.5 更新调度相关单测（manual、目标校验、策略、深链）

## 9. 前端执行记录页

- [x] 9.1 执行列表增加来源调度列/筛选或链接，保留状态/任务/节点/触发/耗时/退出码
- [x] 9.2 执行详情增加「执行过程」时间线与当前状态/block_reason
- [x] 9.3 执行详情增加「执行结果」摘要（状态/耗时/退出码/stdout/stderr/截断）
- [x] 9.4 执行详情展示来源调度名称与跳转，历史记录显示未知来源
- [x] 9.5 保留并整理实时日志、搜索、跟随、换行、复制与取消
- [x] 9.6 更新执行相关单测（时间线、来源、进行中状态）

## 10. 跨页与导航

- [x] 10.1 Sidebar 改为任务定义/调度计划/执行记录，更新分组与图标
- [x] 10.2 节点详情按 Schedule.Target 匹配调度，再关联任务
- [x] 10.3 Dashboard/审计/深链更新到新术语与 `/schedules`
- [x] 10.4 清理旧重定向与死文案，确保 `/tasks?view=schedules` 等旧链接仍可用

## 11. 文档与规格

- [x] 11.1 更新 `docs/PRODUCT.md` §21–30、§43–44：Task/Schedule/Manual Run/Execution 字段与页面
- [x] 11.2 更新 `docs/ARCHITECTURE.md` §17、§30–32、§67：Desired State、Schedule、执行 Key 与同步
- [x] 11.3 更新 `docs/TEST_PLAN.md`、`docs/UI_FULL_TEST_PLAN.md`、`docs/TRACEABILITY_MATRIX.md`
- [x] 11.4 更新 `README.md`/部署文档中的术语、升级与回滚说明
- [x] 11.5 核对 `openspec/specs` 主规格与 change delta 的差异，准备 archive

## 12. 测试与验收

- [x] 12.1 后端：`go test ./...` 全量通过，新增迁移/契约/调度测试覆盖关键路径
- [x] 12.2 后端：`go vet ./...`（或仓库既有静态检查）通过
- [x] 12.3 前端：`npm run test` 全量通过
- [x] 12.4 前端：`npm run build` 通过
- [x] 12.5 前端：`npm run lint`（若仓库提供）通过
- [x] 12.6 E2E：更新 p02/p09/p12/p17/p24 等选择器，运行可用场景
- [x] 12.7 迁移演练：旧库 fixture → 新库，核对任务/调度/执行数量与目标复制
- [x] 12.8 手工验收：创建任务定义 → 创建调度计划（节点+策略）→ 立即执行 → 查看执行过程/结果/来源
- [x] 12.9 兼容性验收：旧 Agent 被拒绝并给出明确错误；新 Agent 全量同步后 agent-owned 调度可触发

## 13. 最终审计

- [x] 13.1 全仓搜索 `Target`/`timeout`/`retry`/`run_user`/`offline_policy` 在 Task 上的残留引用
- [x] 13.2 检查 API/前端/E2E/文档术语一致，无「执行结果」窄化进行中状态
- [x] 13.3 检查迁移失败路径、回滚说明和备份要求已落地
- [x] 13.4 检查无无关修改、临时产物、敏感信息进入交付范围
- [x] 13.5 汇总验证结果、未验证项与环境限制，更新 change 状态
