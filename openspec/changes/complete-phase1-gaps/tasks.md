# Tasks: Complete Phase 1 Gaps

## A. 后端核心功能缺口

- [x] A1 修复 Cron 调度双侧失效：重写 `internal/hub/cron.go` 与 `internal/agent/scheduler/scheduler.go` 的 cron 触发逻辑，引入 last-fire 记录
- [x] A2 修复 Manual Run 幂等：Agent 端 CreateExecution 按 Execution ID 去重，调整 executions 唯一索引为 partial（非空 scheduled_time）
- [x] A3 实现 Remote Node Condition 全链路：协议 REMOTE_STATE_REQ/REMOTE_STATE、Hub gateway 处理、Agent 查询与 Fail Closed
- [x] A4 实现 Tombstone 删除同步：Hub tombstones 表 + BuildSyncResponse 填充，Agent 消费验证

## B. 一致性与符合性

- [x] B5 实现 Realtime Log：Agent 分片发送 LOG_CHUNK，Hub 存储 execution_logs 并 API 查询
- [x] B6 实现 Task Retry：Agent executeTask 重试循环
- [x] B7 systemd Unit Registry：Agent unit_registry 表 + operate 前校验
- [x] B8 Maintenance/Disabled 不被心跳覆盖：store UpdateHeartbeat 判断状态
- [x] B9 Agent 同步事务：applySync BEGIN/COMMIT/ROLLBACK
- [x] B10 Jitter：心跳/周期校验/重连退避加随机偏移
- [x] B11 Docker Host Integration Inventory 读 /host
- [x] B12 total log 上限生效：limitedBuffer.Write 检查 totalLimit
- [x] B13 READY 门禁：对账完成前置 ready
- [x] B14 Interval Misfire SKIP 生效
- [x] B15 Operator 权限收紧：auth.HasPermission / api.canWrite
- [x] B16 WorkingDir / RunUser 接线：Hub payload + Agent runner

## C. Web UI

- [x] C17 Schedule 创建/编辑页
- [x] C18 Application Deploy/Upgrade/Start/Stop/Restart 按钮
- [x] C19 Script Clone + Revision History 列表（含后端端点）
- [x] C20 Task Parameters/Condition 编辑
- [x] C21 Node Detail 补齐 Tasks/Schedules/Managed Apps/Sync 分区
- [x] C22 Dashboard Sync/Application 统计
- [x] C23 Execution Duration / Artifact Usage

## D. 验证

- [x] D1 全量 Go 单测 + vet 通过
- [x] D2 前端 `npm run build` 通过
- [x] D3 OpenSpec validate 通过
- [x] D4 KVM 全量部署与功能测试（Cron/Manual/Remote Condition/Tombstone/Log/Retry/Registry/Rollback 等全部功能）
