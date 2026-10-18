# WEB-LIVE-LOG PENDING UI：最小执行方案（待 Root 审核）

状态：只读探索完成；没有创建 Task、Run 或写 proxy controls。

`.e2e/runs/20260930-101945/repair/feature-inventory.json` 当前 `P11-02` 为 `NOT_RUN`（创建 PENDING、发送到在线 Agent、最终 SUCCESS/FAILED）；没有字面 `WEB-LIVE-LOG` case ID。06:49:35Z repair API 只读预检显示 child `15961ef7-81b4-43f3-80c8-1b6977ca2aea` / Agent `agent-ab15a133-73cd-4025-8c97-f2ff37e000f5`、parent 和原四 Native 均 online/synced；child 无 Task、Schedule、PENDING/RUNNING Execution。完整摘要见 `readonly-preflight.json`。

## 现有代理能力与隔离边界

Native4 child 使用 loopback WebSocket proxy；已有 source/task evidence 表明 proxy 只允许 loopback 且单活跃连接（supervisor 源码 `supervisor-source-evidence.json` line 11；PID 44932、startticks 123677922、UID 65534）。因此它只承载这个 owned child，不承载原四 Agent。最近手动 child restart evidence 仍记录同一 proxy PID，父 Unit MainPID 32487 未操作。

原生 proxy 控制帧集合包含 `RUN_EXECUTION`；downlink `hold_types` 将帧暂存，release coroutine 每 100 ms 检查 controls，在类型从 hold 集合移除后写回并 `drain`（`.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend/setup/qa-resume-20261008-native4-child-setup-1f9433db42-definition.json` 中嵌入 `PROXY_SOURCE` 行 10、91–98、144–155）。这个 hold 按帧类型而非 Task ID 生效；执行前必须确认 child 无其他 in-flight Run/无 Schedule，整轮只点一次这一个 Task。历史 `remote_delivery_delay` 扩展只匹配 downlink `REMOTE_STATE` 的 target node/property/task_id，不能延迟 Task 的 `RUN_EXECUTION`（`BE-REMOTE-TTL-enable-observed-response-delay-c0931e2891-definition.json` 的 embedded command lines 36–45），所以本方案不用它。

## 唯一拟执行路径

1. Root 审核后，先复核 child PID 49873/startticks 137980573/UID 65534、binary SHA `d55dc5…`、config/root/DB inode、proxy PID 44932/startticks、父 Unit 和 Node/Agent IDs；检查 controls 仍为 `{}`、原四在线、child 无 PENDING/RUNNING Execution/Task Schedule。只写 controls 时用 0700 owned root 与 controls 文件 UID 65534/mode 0600 的精确 guard、临时文件原子 rename。
2. 创建一个 child-only `type=command` Task：命令 `sleep 4; printf '%s\\n' 'QA_PENDING_UI_OK'`，timeout 15、retry 0、无参数、无 Schedule；不创建 Script 或 Artifact。先专用登录并把浏览器预置在该 Task 的 `/tasks/{id}/run` 页面，确认仅一个目标 child 与 Run Now 按钮。
3. 设置 controls 为 `{"hold_types":["RUN_EXECUTION"]}`，同时启动独立 8 秒 monotonic watchdog；随后在 UI 点一次 Run Now。Hub 先建 PENDING Execution 再 dispatch，proxy 只暂存该 owned child 的下行 RUN_EXECUTION。通过 API 精确确认 Execution ID/TaskID/NodeID 后直接打开 `/executions/{execution_id}`，截图并读 DOM 确认 UI 显示该 ID 与 PENDING/等待中；最多等两个 3 秒 UI poll，8 秒到期即自动 release。
4. 截到 PENDING 后立即把 controls 原子清为 `{}`。release loop 最迟约 100 ms 转发已暂存 RunExecution；UI detail 每 3 秒轮询 Execution 与日志。`sleep 4` 为 RUNNING 提供一次 poll 窗口，然后以同一 Execution ID 验证 SUCCESS、child ID、stdout marker，并核对 API/UI 状态和日志。heartbeat 与 Node status 要保持在线；不把 LastSeen 单独叫作 heartbeat packet。
5. `finally` 再次精确清 controls `{}`；如 UI/driver 中断，由独立 Root 备用 parent Command Task 只读核 owner/PID/unit 后原子清 controls（不 signal、不重启 Unit/proxy）。随后 logout、clear browser token、`/api/me` 401、close 专用会话；DELETE 唯一 Task 并 GET 404，保留 Execution/Audit 历史。控制 release 或 PENDING UI 观察不符合预期时，不重 Run，立即清 controls 并记录实际终态。

## 时间与恢复风险

当前 runtime API settings 为 heartbeat interval 30 秒、revision check 45 秒。Hub 源码默认 HeartbeatTimeout 90 秒、检查间隔 30 秒（`cmd/hub/main.go:62`、`internal/hub/session.go:88–115`）；安全 Settings API 不暴露已加载的 heartbeat_timeout 覆盖值，因此 8 秒低于源码默认 90 秒，但 live timeout override 尚不能从此只读 API 确认。更关键的是本方案只 hold Hub→Agent 的 RUN_EXECUTION，不暂停 child 进程/WebSocket，也不拦 Agent→Hub heartbeat，故不靠停止 heartbeat 来取得窗口；禁止用 SIGSTOP 作为主路径。

控制路径最后一次已证实为空是 manual restart guards（`restart/manual-attempt01/restart-evidence.json`）；执行前必须 fresh read 再确认。若截图、click、API polling 任一阶段超时，8 秒 watchdog 清 controls；Root fallback 只清 controls，永不 stop parent Unit/cgroup、proxy 或原四 Agent。

## 已核对的状态调用链

Hub `createAndDispatch` 先持久化 `PENDING` 再调用 `dispatchToAgent`（`internal/hub/execution.go:234–268`）；dispatch 发送 RUN_EXECUTION（`execution.go:271–`），Agent 发 Execution Started 后 Gateway 调 `MarkStarted` 写 `RUNNING`（`internal/hub/gateway.go:520–527`、`internal/hub/execution.go:342–356`）。UI Run Now 提交后跳转 `/executions`（`web/src/features/tasks/index.tsx:1415–1425`）；Execution detail 对 PENDING/RUNNING 每 3 秒 refetch 状态和 logs，PENDING 显示“等待中”（`web/src/features/executions/index.tsx:38–56,351–379`）。

本计划只请求 Root 审核；没有改全局台账、产品代码、配置或控制文件。
