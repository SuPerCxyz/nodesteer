## 1. 模型与校验

- [x] 1.1 `models.Schedule` 增加 `Condition *Condition`
- [x] 1.2 `models.Execution` 增加 `ScheduleRevision`
- [x] 1.3 增加 `models.MergeConditions`，本地在前、远程在后
- [x] 1.4 `validateCondition` 增加 scope，Task 只允许本地、Schedule 只允许远程
- [x] 1.5 Task/Schedule API 错误归属返回 400，更新错误文案

## 2. 执行链路

- [x] 2.1 `RunManual` 接收 scheduleID/scheduleRevision/scheduleCondition
- [x] 2.2 `RunScheduledHub` 接收 scheduleRevision/scheduleCondition
- [x] 2.3 `createAndDispatch`/`createPausedSkip` 写入 ScheduleRevision
- [x] 2.4 `dispatchToAgent` 合并 Task 本地条件与 Schedule 远程条件
- [x] 2.5 任务页 Run 只传本地条件；调度页 Run 传远程条件与 Revision
- [x] 2.6 应用类任务手动执行统一走 `RUN_EXECUTION`，验证条件生效
- [x] 2.7 `MarkFinished`/`HandleDeployResult`/`UpsertFromAgent` 保留 ScheduleRevision

## 3. Store 与迁移

- [x] 3.1 Hub schema 增加 `executions.schedule_revision`
- [x] 3.2 实现旧 Task.Condition 递归拆分 local/remote
- [x] 3.3 远程条件复制到全部 Schedule，含 manual 迁移调度
- [x] 3.4 重写 Task/Schedule JSON，提升 Revision，写 Change Log
- [x] 3.5 迁移幂等标记，失败阻止启动
- [x] 3.6 增加迁移测试：local-only/remote-only/mixed/多调度/无调度/幂等

## 4. 协议与 Agent

- [x] 4.1 协议版本升到 3
- [x] 4.2 `RunExecutionPayload`/`ExecutionFinishedPayload` 增加 schedule_revision
- [x] 4.3 Agent 本地 executions 增加 schedule_revision 与旧库迁移
- [x] 4.4 `TriggerSchedule` 合并 Task 本地 + Schedule 远程条件
- [x] 4.5 `OnRunExecution`/`OnDeployRequest` 写入并上报 schedule_revision
- [x] 4.6 应用类任务条件评估测试
- [x] 4.7 Agent 条件合并/Journal/协议测试

## 5. 前端

- [x] 5.1 任务编辑器移除远程条件，只保留本地条件
- [x] 5.2 任务详情条件区只展示本地条件
- [x] 5.3 调度编辑器新增远程条件表单（节点在线/last_execution）
- [x] 5.4 调度 payload 与列表/详情展示远程条件摘要
- [x] 5.5 任务试运行页展示只读本地条件摘要
- [x] 5.6 执行详情展示 schedule_revision
- [x] 5.7 更新 zh/en locale 与相关单测

## 6. 文档

- [x] 6.1 PRODUCT：条件归属、手动/调度范围、应用类任务
- [x] 6.2 ARCHITECTURE：条件合并、schedule_revision、协议 v3
- [x] 6.3 TEST_PLAN/UI_FULL_TEST_PLAN/TRACEABILITY
- [x] 6.4 DEPLOYMENT/README 升级与回滚说明

## 7. 验证

- [x] 7.1 `go test ./...` 全绿
- [x] 7.2 `go vet ./...` 通过
- [x] 7.3 `npm run test` 全绿
- [x] 7.4 `npm run build` 通过
- [x] 7.5 `npm run lint` 通过（允许既有 warning）
- [x] 7.6 E2E：任务本地条件、调度远程条件、应用类任务条件
- [x] 7.7 本地 Hub+Agent 手工验收：任务试运行只评估本地；调度立即执行评估远程；执行详情显示 schedule_revision
- [x] 7.8 迁移演练：混合条件拆分、多调度复制、无调度告警
- [x] 7.9 残留审计与最终报告
