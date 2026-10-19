# P16 修复报告：P8 长稳批 SOAK-20261018-01 / -02

- 批次：`qa-resume-20261018-soak` 后续修复（P16）
- 日期：2026-10-18
- 仓库：`/home/superc/code/nodesteer`
- 依据：`.e2e/runs/20260930-101945/repair/qa-resume-20261018-soak/findings.json`、`evidence/repro-superseded-upload.json`、`run_soak.py repro`
- 范围：仅 Go 源码与单测；未改 `web/**`、`.e2e/**` 既有状态文件、未 Git、未装依赖、未部署
- 本报告随附证据：`p16-evidence/`（修复后 repro 结果与日志）

---

## 0. 结论摘要

| 缺陷 | 根因 | 方案 | 结果 |
|---|---|---|---|
| SOAK-20261018-01 | 断连重连时 `ResumeNode` 重投新上传尝试，Agent 主动取消旧尝试并把 `context canceled` 当失败上报；Hub 无世代概念，一律 `failTransfer` | Hub 为每次下发分配**世代号（attempt）**并随请求下发，Agent 在结果回执中原样回传；Hub 只接受**当前世代**的结果，旧世代回执忽略并记 WARN | 确定性 repro：不再出现错误 FAILED，续传正常完成（SUCCESS，SHA 一致） |
| SOAK-20261018-02 | `completeUpload` / `HandleDeliveryResult` / `failTransfer` / `updateTransferAggregate` / `ResumeNode` 等写入路径无终态守卫，终态可被迟到事件改写 | 引入**终态冻结**：`SUCCESS/FAILED/CANCELED` 为终态，所有异步写入路径拒绝改写（WARN 日志）；显式 `Retry` 是 FAILED 的唯一恢复路径 | 修复前 `FAILED→DELIVERING→SUCCESS` 的复现脚本，修复后不再出现任何 FAILED/终态改写 |
| P15 major-01（评审扩围） | 终态守卫漏 3 个写入点：`handleUpload` 分片写回 UPLOADING（受 `failTransfer` 清空 SHA256 影响）、`Cancel` 无终态守卫、目标级写入（`dispatchTarget`/`HandleDeliveryResult`） | 守卫覆盖源级 + 目标级 + 取消 + 上传/投递回调全部写点；终态判定先于“已暂存”早退；目标终态判定统一为 `isTerminalTargetStatus` | 新增 4 组测试；撤守卫 → 全部失败（见 §6） |
| P15 major-02（评审扩围） | 修复 SOAK-01 后，当前尝试失败但结果丢失时源上传无兜底，永久停在 UPLOADING 并阻断节点删除 | 世代号机制（真失败必上报即终态化）+ 新增**源上传停滞看门狗** `SweepStalledUploads`（30min + 按 1 MiB/s 折算，进展信号 = part mtime / UpdatedAt 较新者；仅 UPLOADING，PENDING 豁免） | 组合测试覆盖「被取代不误报」+「真失败/无进展必终态化」；全量 soak 回归通过 |

---

## 1. SOAK-20261018-01：被取代的在飞上传误报失败 → 传输被错误终态化

### 1.1 根因（代码级）

1. Agent：`ResumeNode` 重投的 `FILE_UPLOAD_REQUEST` 到达时，`startFileTransfer()` 主动 cancel 旧上传 ctx（修复前 `internal/agent/agent.go:1398-1413`）。
2. Agent：被取消的旧上传返回 `ctx.Err()=context canceled`，被 `sendFileUploadResult()` 作为 `OK=false` 上报（修复前 `internal/agent/agent.go:1236-1303, 1415-1424`）。
3. Hub：`HandleUploadResult` 仅豁免 CANCELED/SUCCESS/已暂存，其余一律 `failTransfer` → 传输+目标终态 FAILED（修复前 `internal/hub/file_transfer.go:532-559`），而续传请求仍在飞行。
4. 附带同源缺陷：被取代旧尝试的 `done()` 无条件删除 `transferCancel[id]`，会把新尝试的取消登记删掉，导致后续 Hub 取消指令命中不到仍在进行的尝试（修复前 `internal/agent/agent.go:1406-1412`）。

### 1.2 方案与取舍

- **世代号（attempt）**：`dispatchSource` 每次实际下发分配单调递增世代号并放入 `FileUploadRequestPayload.Attempt`；Agent 在 `FileUploadResultPayload.Attempt` 原样回传；`HandleUploadResult` 仅接受 `p.Attempt == current`。目标交付同理（`dispatchTarget` / `FileDeliveryRequestPayload.Attempt` / `FileDeliveryResultPayload.Attempt`），因为目标侧存在完全同构的「重投取代旧下载 → 旧尝试报 context canceled → 目标/传输被误判 FAILED」问题，且终态冻结会使该误判变成不可恢复的终态，必须同时修。
- **世代表放在内存**（`uploadAttempts` / `deliveryAttempts`，`attemptMu` 保护）：Hub 重启后所有连接重建，`ResumeNode` 会重新下发并建立新世代；迟到的旧世代结果自然被忽略，无需持久化。
- **Agent 侧世代登记用指针身份**（`transferAttempt`）：被取代尝试收尾时只清理自己的登记，修复「取消丢失」同源问题。
- **Hub 不在 `dispatchSource`/`dispatchTarget` 抢 `m.mu`**：dispatch 由 Agent 读循环（`onAgentConn → ResumeNode`）触发，而 `handleUpload` 在整个上传请求期间持有 `m.mu`；若 dispatch 抢锁，慢速大文件上传会把 Agent 读循环（心跳/结果处理）阻塞到上传结束（本地 e2e 实测阻塞 ~20s，大文件/慢链路可超心跳超时）。改为「下发前以库内最新状态为准」（fresh read），下发时机与修复前保持一致，同时消除陈旧实例复活终态的问题。
- 备选方案（未采用）：
  - 「Agent 侧被取代就不上报」：Hub 无法区分「被取代」与「真实失败」，且缺结果时无兜底，可能把真实失败吞掉；
  - 「Hub 对 `context canceled` 文案做去终态化」：依赖错误字符串，脆弱且不能覆盖其他取消文案；
  - dispatch 统一持 `m.mu`：会引入上述读循环阻塞回归。

### 1.3 改动

| 文件 | 位置 | 内容 |
|---|---|---|
| `internal/protocol/protocol.go` | :256-265, :273-274, :279-288, :296-297 | 上传/交付请求与结果 payload 增加 `Attempt`（世代号） |
| `internal/agent/agent.go` | :77, :1232, :1316, :1400-1449 | 结果回传 `p.Attempt`；`transferAttempt` 指针身份登记，`done()` 只清理本尝试 |
| `internal/hub/file_transfer.go` | :166-196 | `nextUploadAttempt` / `currentUploadAttempt` / `nextDeliveryAttempt` / `currentDeliveryAttempt`（`attemptMu` 保护） |
| `internal/hub/file_transfer.go` | :424-462, :488-544 | `dispatchSource` / `dispatchTarget`：fresh read + 下发世代号 |
| `internal/hub/file_transfer.go` | :774-803, :845-901 | `HandleUploadResult` / `HandleDeliveryResult`：终态守卫 + 世代匹配 + WARN |

### 1.4 测试

- `internal/hub/file_transfer_epoch_test.go`
  - `TestFileTransferSupersededUploadAttemptIgnoredAndResumeCompletes`：部分上传 → ResumeNode 重投（世代+1，offset=已暂存偏移）→ 旧世代 `context canceled` 回执被忽略（状态保持 UPLOADING、无错误）→ 续传完成 → 交付 → SUCCESS。
  - `TestFileTransferSupersededDeliveryAttemptIgnored`：目标侧同构场景。
  - `TestFileTransferCurrentAttemptFailureStillFails`：**当前世代**的真实失败照常终态化（防止「忽略旧结果」变成「丢失有效结果」）。
  - `TestFileTransferConcurrentUploadResultsOnlyCurrentAttemptWins`：并发两世代回执，仅当前世代生效（-race）。
  - `TestFileTransferUploadAttemptWraparound`：uint64 回绕后仍以最近一次下发为准，回绕前远古世代被忽略。
- `internal/agent/file_transfer_test.go`
  - `TestAgentTransferSupersessionKeepsCurrentCancelable`：被取代尝试收尾后，取消指令仍命中当前尝试；当前尝试收尾后登记清空。
  - `TestAgentTransferResultEchoesAttempt`：真实 WS 连接下端到端验证上传/交付结果回传 `Attempt`（7/9）。

### 1.5 确定性复现验证（修复前 vs 修复后）

工具：`run_soak.py repro`（隔离 Hub 127.0.0.1:18091 + 2 owned Agent + HTTP 2MB/s 节流；未触碰生产）。修复后二进制由当前源码重建（hub `23f14bd4…`、agent `dc67b36d…`），在 run 目录副本中运行，未改动原批次 evidence。

| 项 | 修复前（批次原始 evidence） | 修复后（`p16-evidence/repro-superseded-upload-postfix-20261018.json`） |
|---|---|---|
| 状态序列 | UPLOADING → **FAILED**(dt=5.22s, `source upload failed: context canceled`) → DELIVERING(24.47) → SUCCESS(50.55) | UPLOADING → DELIVERING(24.47) → SUCCESS(50.94) |
| `failed_then_nonfailed` | **true** | **false** |
| 终态错误 | 出现 FAILED + 误导性错误文案约 19s | 无 FAILED、无错误文案 |
| 续传 | part 15.5MB→52.4MB 后 SUCCESS | 续传正常（proxy 时间线：旧 POST 关闭 → 新尝试 409 校正偏移 → 续传） |
| SHA | 一致 | 一致（`sha_match: true`） |
| 修复机制观测 | — | Hub WARN：`file transfer result ignored: superseded attempt … attempt=1 current_attempt=2`（旧尝试回执被世代守卫忽略） |

### 1.6 反向验证（撤掉修复 → 测试失败）

临时将 `isTerminalTransferStatus` 置为恒 false、两处世代比较短路为 `false`（等价于撤掉两个修复），运行新增测试：

- `TestFileTransferSupersededUploadAttemptIgnoredAndResumeCompletes` FAIL：`superseded attempt must not terminalize the transfer: … Status:FAILED Error:source upload failed: context canceled`（与线上现象完全一致）
- `TestFileTransferSupersededDeliveryAttemptIgnored` FAIL：目标被误判 FAILED
- `TestFileTransferCurrentAttemptFailureStillFails` FAIL：`stale failure changed status to FAILED`
- `TestFileTransferUploadAttemptWraparound` FAIL：`pre-wrap attempt must be ignored, status=FAILED`
- `TestFileTransferCancelRaceWithStaleDispatchDoesNotResurrect` FAIL：`stale dispatch resurrected canceled transfer: … Status:UPLOADING`

恢复后（sha256 校验一致）全部通过。

---

## 2. SOAK-20261018-02：传输终态未冻结

### 2.1 根因（代码级）

- `completeUpload` 不校验当前状态，直接把传输置回 DELIVERING（修复前 `internal/hub/file_transfer.go:475-498`）——SOAK-01 的 FAILED 在续传完成时被无声改写。
- `HandleDeliveryResult` 仅豁免 CANCELED/未暂存，不豁免终态；随后 `updateTransferAggregate` 按目标状态重新聚合（修复前 `:586-649`），反向翻转（SUCCESS→FAILED）同样可能。
- `failTransfer` 不校验当前是否已 SUCCESS（修复前 `:550-559`）；`ResumeNode` 对 FAILED 且已暂存的传输仍会重投目标交付（修复前 `:283-302`）；`Cancel` 可把 SUCCESS 改写为 CANCELED。

### 2.2 方案与取舍

- 明确终态集合：`SUCCESS / FAILED / CANCELED`（`isTerminalTransferStatus`）。
- 所有**异步/迟到写入**路径统一守卫（拒绝写入 + WARN 日志）：`handleUpload`（HTTP 续传入口，终态返回 410）、`completeUpload`（纵深防御，校验先于任何落盘副作用）、`HandleUploadResult`、`failTransfer`、`HandleDeliveryResult`、`updateTransferAggregate`、`SweepStalledDeliveries`、`ResumeNode`（终态不再自动重投）、`dispatchSource`/`dispatchTarget`（下发前以库内最新状态为准）。
- **显式管理动作语义**（保留产品既有交互）：
  - `Retry`：FAILED 仍可重试（唯一恢复路径）；CANCELED 拒绝（既有）；SUCCESS 直接返回（不改写）；
  - `Cancel`：CANCELED 幂等返回（不重复下发取消、不重复清理）；SUCCESS/FAILED 拒绝（400 `transfer already finished: <STATUS>`，不改写、不下发、不清理）。
- 取舍说明：`Cancel(FAILED)→CANCELED` 最初按前端语义保留为显式例外；**P15 评审 major-01(2) 后已收紧为拒绝**（见 §6.1/§6.6），前端需跟随隐藏 FAILED 行的取消按钮（§4）。冻结针对的是「迟到事件无声改写终态」，所有终态→进行中的回退路径均被拒绝。

### 2.3 改动

| 文件 | 位置 | 内容 |
|---|---|---|
| `internal/hub/file_transfer.go` | :146-164 | `isTerminalTransferStatus`、`errTransferTerminal` |
| `internal/hub/file_transfer.go` | :198-222 | 终态/旧世代拒绝的 WARN 日志 |
| `internal/hub/file_transfer.go` | :603-609 | `handleUpload` 终态守卫（先于 SHA 早退，410 Gone，不落盘） |
| `internal/hub/file_transfer.go` | :711-717 | `completeUpload` 终态守卫（先校验后落盘） |
| `internal/hub/file_transfer.go` | :805-809 | `failTransfer` 终态守卫 |
| `internal/hub/file_transfer.go` | :903-906 | `updateTransferAggregate` 终态守卫 |
| `internal/hub/file_transfer.go` | :465-485 | `ResumeNode` 终态（含 FAILED）不自动重投 |
| `internal/hub/file_transfer.go` | :937-990 | `Retry`：先重置持久化再下发（避免 FAILED 状态下发被守卫拒绝）；SUCCESS 不改写 |
| `internal/hub/file_transfer.go` | :992-1005 | `Cancel`：CANCELED 幂等 / SUCCESS、FAILED 拒绝 |
| `internal/hub/file_transfer.go` | :259-301 | `SweepStalledDeliveries` 使用统一终态判定 |

### 2.4 测试

- `TestFileTransferTerminalStatesFrozen`（表驱动：SUCCESS / FAILED / CANCELED × 写入路径）：
  - 迟到的源失败回执、迟到的目标结果（OK 与失败两个方向）→ 状态不变；
  - 续传 POST：SUCCESS/FAILED→**410**、CANCELED→409（整段与分片两种）；且终态下不产生 part/blob 落盘；
  - 分片写回写点 `markUploading` → 拒绝且不改写；
  - `ResumeNode`（源/目标）→ 无新指令；
  - 交付/源上传看门狗 → 不终结；
  - 直接 `completeUpload` → `errTransferTerminal`。
- `TestFileTransferExplicitActionsOnTerminal`：CANCELED 幂等 + 拒绝 Retry；SUCCESS/FAILED 拒绝 Cancel 且状态/错误/目标不被改写（评审扩围后 FAILED 也拒绝）；FAILED Retry 可恢复且恢复后的真实失败仍生效。
- `TestFileTransferCancelRaceWithStaleDispatchDoesNotResurrect`：ResumeNode 在锁外读到陈旧实例后 Cancel 先落 CANCELED，陈旧 dispatch 不得复活传输/目标。
- 既有测试适配：所有构造结果的测试改为回传真实世代号（`internal/hub/file_transfer_test.go`、`internal/hub/pause_dispatch_test.go`），pause 用例改为以库内状态断言。

### 2.5 确定性复现验证

同一 `run_soak.py repro`：修复前 `FAILED(dt=5.22) → DELIVERING(dt=24.47) → SUCCESS`（终态被改写）；修复后无 FAILED、无终态改写（见 §1.5 表）。修复后 Hub 日志中亦无任何 `terminal state frozen` WARN——即不存在被拒绝的终态写入尝试，符合「终态零改写」预期。

### 2.6 反向验证

同 §1.6（撤掉 `isTerminalTransferStatus` 守卫）：
- `TestFileTransferTerminalStatesFrozen/FAILED` FAIL：`failed upload replay = 200 {…"status":"DELIVERING"}`（正是 SOAK-02 现象）；
- `…/CANCELED` FAIL：迟到的源失败先把 CANCELED 改写为 FAILED，续传 POST 再把状态改写为 DELIVERING；
- `…/SUCCESS` FAIL：`completeUpload` 未被终态拒绝；
- `TestFileTransferCancelRaceWithStaleDispatchDoesNotResurrect` FAIL。

---

## 3. 对外可见行为变化（逐条）

1. **协议 payload 新增 `attempt` 字段**（上传/交付请求与结果）。Hub 与 Agent 需同版本；旧版 Agent 的结果回执因世代不匹配被忽略（本项目无需历史版本兼容）。
2. **终态后上传端点响应**：FAILED（未暂存）后续传 POST 由「被接受并复活为 DELIVERING」变为 `410 Gone` + `transfer is in terminal state <STATUS>`；CANCELED 仍为 409；仍非终态的 DELIVERING（已暂存）重放保持 200 幂等元数据（评审扩围后 SUCCESS/FAILED 统一 410，见 §6.6）。
3. **终态后迟到事件**：迟到的源失败/目标结果/聚合不再改写终态（此前可能改写），并产生 WARN 日志 `file transfer write rejected: terminal state frozen`（目标级为 `file transfer target write rejected: terminal state frozen`）。
4. **旧世代结果**：被取代尝试的失败/成功回执不再影响传输状态，产生 WARN 日志 `file transfer result ignored: superseded attempt`（可观测）。
5. **重连恢复**：`ResumeNode` 不再为 FAILED 传输自动重投（此前 FAILED 且已暂存时会对 PENDING/DELIVERING 目标重投）；恢复须显式 Retry。
6. **取消语义**：对 SUCCESS/FAILED 调用 Cancel 由「改写为 CANCELED」变为 400 `transfer already finished: <STATUS>`（前端需隐藏 FAILED 行的取消按钮）；对 CANCELED 重复取消变为幂等 200（不再重复下发取消/清理）。
7. **Agent 侧**：被取代尝试收尾不再误删新尝试的取消登记（修复「取消丢失」）；结果回执携带世代号。
8. **源上传停滞兜底**：UPLOADING 无进展超过 30min+size/1MiB/s → FAILED（文案 `source upload timed out after … without progress from the source agent`）；PENDING 不受影响。

## 4. 已知限制 / 未做项

- `dispatchSource`/`dispatchTarget` 的「fresh read → 写入」在极端并发下仍存在微秒级窗口（Cancel 与 dispatch 恰好同时发生）。这是为保持修复前下发时机、避免把 Agent 读循环阻塞在大文件上传之后而做的取舍；确定性场景（陈旧实例）已修复并有测试覆盖。
- 源上传看门狗与交付看门狗同构：重连重投会刷新 `UpdatedAt`（给源端新的完整窗口）；若源端持续「重连但不传数据」，只要仍在重连就会不断刷新窗口（与交付看门狗的既有语义一致），完全停止重连后按阈值终态化。
- 世代表为内存态：Hub 重启后旧世代回执被忽略，重连由 `ResumeNode` 建立新世代（设计如此，未做持久化）。
- P15 评审 `minor-01`（`docs/ARCHITECTURE.md` §54 表述收窄）不在本任务边界（只改 Go 源码与单测），未处理。
- `Cancel(FAILED)` 由「允许改写为 CANCELED」改为「返回业务错误」（评审 major-01(2) 要求）；`web/src/features/transfers/index.tsx` 的 `canCancel = !['SUCCESS','CANCELED'].includes(status)` 仍会对 FAILED 行展示取消按钮 → 点击将得到 400。**需前端跟随隐藏 FAILED 行的取消按钮**（`web/**` 不在本任务边界，未改）。
- 未改动 `web/**`、`.e2e/**` 既有状态文件；未执行 Git 操作；未部署。
- 其他场景（s1–s6）回归结果见 §5.3。

## 5. 命令与验证真实结果

### 5.1 构建 / 静态检查（扩围后最终代码）

```
$ go build ./...                     # exit 0
$ go vet ./...                       # exit 0
$ gofmt -l internal/                 # 无输出（干净）
$ go test ./internal/... -count=1    # exit 0；12 个含测试包全部 ok，3 个无测试包
```

### 5.2 并发（-race）

```
$ go test ./internal/hub/ -race -count=1                                    # ok 13.592s
$ go test ./internal/agent/ -race -count=1 -run 'TestAgentTransfer|TestAgentFile'  # ok 1.034s
$ go test ./internal/hub/ -race -count=1 -run '<14 个新增/扩展用例>'         # ok 3.558s；14/14 PASS
```

新增/扩展用例清单（全部 -race 通过）：`TestFileTransferSupersededUploadAttemptIgnoredAndResumeCompletes`、`TestFileTransferSupersededDeliveryAttemptIgnored`、`TestFileTransferCurrentAttemptFailureStillFails`、`TestFileTransferUploadAttemptWraparound`、`TestFileTransferConcurrentUploadResultsOnlyCurrentAttemptWins`、`TestFileTransferCancelRaceWithStaleDispatchDoesNotResurrect`、`TestFileTransferTerminalStatesFrozen`、`TestFileTransferExplicitActionsOnTerminal`、`TestFileTransferTerminalTargetsFrozen`、`TestSourceUploadStallTimeoutTerminalizesTransfer`、`TestSourceUploadStallSweepUsesPartProgress`、`TestSourceUploadStallSweepLeavesPendingAndStagedAlone`、`TestSupersededFailureThenStallTerminalizes`、`TestUploadStallTimeoutScalesWithSize`。

### 5.3 长稳回归（修复后全量 soak，s1–s6）

| 场景 | 修复前（批次原 run，`soak-run.log`） | 修复后 run1（SOAK-01/02 修复版，hub `23f14bd4…`） | 修复后 run2（评审扩围版，hub `17a8087c…`） |
|---|---|---|---|
| S1 断连风暴 | PASS（20 次 WS kill 34.8s，翻转 15，revision 5→6） | PASS（20 次 kill 35.1s，翻转 11，revision 5→6） | PASS（20 次 kill 33.4s，翻转 13，revision 5→6） |
| S2 执行中断连 | PASS（6 tick 落盘，重连 SUCCESS） | PASS（同左，journal synced） | PASS（同左） |
| S3 Hub 重启 | PASS（停机 2.5s，SUCCESS，恰 1 条执行，revision=6） | PASS（同左） | PASS（同左） |
| S4 传输中断/续传 | PASS（4a–4e；但该场景的 4a 同时拒绝 HTTP，未覆盖“在飞上传”路径——错误 FAILED 由 `repro` 子命令确定性复现） | PASS（4a part 14581760→17571840；4b/4d/4e 续传 SUCCESS；4c CANCELED 且 part/blob 清理；SHA 全一致） | PASS（4a part 14714880→17618944；其余同 run1；SHA 全一致） |
| S5 并发压力 | PASS（36.1s，全 SUCCESS，无 5xx/panic；RSS hub≈109MB） | PASS（36.1s，RSS hub=77552KB） | PASS（36.1s，RSS hub=77148KB） |
| S6 Agent 重启恢复 | PASS（6a FAILED(agent_restarted)；6b/6c 续传/重投 SUCCESS） | PASS（同左，SHA 一致） | PASS（同左，SHA 一致） |
| cleanup oracle | pass=True | pass=True | pass=True |

- run1 日志：`p16-evidence/soak-all-postfix-run1.log`；run2 日志：`p16-evidence/soak-all-postfix-v2.log`。
- **修复前后关键对比（同一 `repro` 场景）**：修复前 = 错误 FAILED（`source upload failed: context canceled`，dt=5.22s）→ DELIVERING → SUCCESS（终态改写）；修复后两轮均 = UPLOADING → DELIVERING → SUCCESS，无 FAILED、无终态改写、SHA 一致（详见 §5.4/§6.5）。
- 两轮 soak 均在隔离环境运行（Hub 127.0.0.1:18091 + 2 owned Agent + 故障注入代理），未触碰生产；run 目录副本在 `/tmp/opencode/p16/soak-repro`，原批次 evidence 未被覆盖。

### 5.4 确定性复现（修复前 vs 修复后）

- 修复前：`.e2e/runs/20260930-101945/repair/qa-resume-20261018-soak/evidence/repro-superseded-upload.json`（FAILED，`failed_then_nonfailed=true`）
- 修复后（两版均验证）：`p16-evidence/repro-superseded-upload-postfix-20261018.json` 与 `repro-superseded-upload-postfix-v2-20261018.json`（SUCCESS，`failed_then_nonfailed=false`，Hub WARN `superseded attempt … attempt=1 current_attempt=2`）
- 运行方式：`python3 run_soak.py repro`（在 run 目录副本 `/tmp/opencode/p16/soak-repro` 中执行，指向修复后重建二进制；原批次 evidence 未被覆盖）

### 5.5 反向验证

- 撤掉 SOAK-01/02 两处修复（终态守卫恒关闭 + 世代比较短路）→ 新增测试 6 项 FAIL，失败信息与缺陷现象一致（详见 §1.6/§2.6）；恢复后 hash 校验一致并全部通过。
- 撤掉评审扩围的 4 处守卫（目标级终态判定、`markUploading` 终态检查、`Cancel` 的 FAILED 拒绝、`SweepStalledUploads`）→ 对应 6 个测试全部 FAIL（见 §6.4）；恢复后 hash 校验一致并全部通过。

### 5.6 收尾与清理复扫（冻结状态）

- 冻结态最终命令（2026-10-18 18:0x）：见 §5.1/§5.2，全部 exit 0 / 干净。
- 清理：run 目录副本、重建二进制、临时备份与中间产物统一位于 `/tmp/opencode/p16/`，验证完成后整体删除；soak 工作目录 `/var/tmp/nodesteer-qa-20261018-soak` 已由 harness `cleanup`（oracle pass=True）清除。
- 复扫结论：端口 18091/18092/18093/18094/18192/18193/18194 无监听；无本次运行残留进程（进程表中仅存机器自身常驻 `nodesteer-agent`（PID 1496，`/etc/nodesteer/agent.yaml`）与其他批次的 vitest，非本次运行产物，未触碰）；`/tmp/opencode/p16/` 与 soak 工作目录均不存在。

---

## 6. 评审扩围（P15 major-01 / major-02）

依据：`p15-code-review-20261018.md` §二（major-01：终态守卫还有 3 个未列入的写入点；major-02：源上传无兜底窗口）。评审给出的是 HEAD 行号，本节按**扩围后实际代码**逐点核验与补齐。

### 6.1 major-01：终态守卫覆盖全部写点

| 评审点 | 扩围后处置 | 位置 |
|---|---|---|
| (1) `handleUpload` 分片写回 UPLOADING（受 `failTransfer` 清空 SHA256 影响） | ① 终态判定**先于**“已暂存”早退（SUCCESS/FAILED → 410，不再依赖 SHA256 残留）；② 分片写回经 `markUploading()`，终态返回 false 且不写入 | `file_transfer.go:603-609`、`:672-676`、`:701-710` |
| (2) `Cancel` 无终态守卫（SUCCESS/FAILED → CANCELED） | `Cancel` 对 SUCCESS/FAILED 返回业务错误 `transfer already finished: <STATUS>`（不改写、不下发、不清理）；CANCELED 幂等返回；仅非终态可进入 CANCELED | `file_transfer.go:992-1005` |
| (3a) `dispatchTarget` 复活 FAILED target | 目标级统一判定 `isTerminalTargetStatus`（SUCCESS/FAILED/CANCELED）→ 终态目标不重投；显式 Retry 先重置为 PENDING 再重投（保持既有语义） | `file_transfer.go:155-163`、`:504-506` |
| (3b) `HandleDeliveryResult` 迟到结果改写已终态 target | 目标级终态冻结（含同世代重复回执），拒绝并记 WARN；`failTargets` 同步改用统一判定 | `file_transfer.go:863-867`、`:825-827` |
| 其余 4 个写入点（`completeUpload`/`updateTransferAggregate`/`failTransfer`/传输级 `HandleDeliveryResult`） | SOAK-02 已覆盖，本次复核仍有效 | `:711-717`、`:903-906`、`:805-809`、`:868-871` |

**根因修法（“failTransfer 清空 SHA256 使早退失效”）**：原实现把「已暂存 → 200」早退放在状态守卫之前，而 `failTransfer` 会把 `SHA256` 清空，使 FAILED 传输绕过早退继续走分片写入。本次把 `handleUpload` 的判定顺序改为 **CANCELED(409) → 终态(410) → 已暂存(200)**，终态判定不再依赖 `SHA256` 是否残留；分片写回 UPLOADING 另经 `markUploading()` 二次判定（`:701-710`），即使未来新增旁路也不会写回进行中。已暂存但仍非终态（DELIVERING）的续传重放保持 200 幂等语义不变。

### 6.2 major-02：源上传兜底

- **世代号机制**：当前世代失败必被受理（`TestFileTransferCurrentAttemptFailureStillFails`），「被取代不误报」不再等价于「失败被吞」。
- **新增 `SweepStalledUploads`**（`file_transfer.go:320-348`）：仅作用于 `UPLOADING` 且未暂存的传输；进展信号 `uploadProgressSince`（`:350-363`）= max(part 文件 mtime, 传输 UpdatedAt)；阈值 `uploadStallTimeout`（`:67-79`）= 30min + size/1 MiB/s；超时 → `failTransfer`（FAILED + 终结未暂存传输的目标）+ WARN。`StartDeliveryWatchdog` 同一周期内同时执行两个 sweep（`:244-250`）。
- **取舍**：PENDING 明确豁免（“等节点重连”是产品语义的离线持久化重试）；与交付看门狗同构（E2E-20261017-XFER-REMOTE-TARGET-STUCK-DELIVERING 已确立「有界终态化 > 永久 409 悬挂」的取舍）。
- **未覆盖的极端形态**：上传 POST 处于内核级黑洞（连接既不断也不超时）时，`handleUpload` 持 `m.mu` 阻塞，看门狗同样被锁等待；该形态由 TCP keepalive/读错误最终释放（分钟级），随后由看门狗或重连重投终态化。

### 6.3 新增测试

- `TestFileTransferTerminalStatesFrozen`（扩展）：三终态 × 整段/分片续传 POST（SUCCESS/FAILED → 410，CANCELED → 409）× `markUploading` 直接写点 × 两个看门狗 × `completeUpload`。
- `TestFileTransferExplicitActionsOnTerminal`（扩展）：FAILED 取消被拒绝且状态/错误/目标不被改写；SUCCESS 取消被拒绝且目标不被改写；CANCELED 幂等；FAILED Retry 恢复。
- `TestFileTransferTerminalTargetsFrozen`（新增）：多目标下已成功目标不被迟到失败回执翻转（反向翻转防护）；FAILED 目标不被 `dispatchTarget` 复活，显式 Retry 仍是唯一恢复路径。
- `TestSourceUploadStallTimeoutTerminalizesTransfer`（新增）：未到阈值不终结 + 节点删除仍 409；越阈值 → FAILED + 可定位文案 + 目标 FAILED + 节点删除释放；幂等。
- `TestSourceUploadStallSweepUsesPartProgress`（新增）：part mtime 推进期间不判定停滞；停止推进后按 part mtime 计时终结。
- `TestSourceUploadStallSweepLeavesPendingAndStagedAlone`（新增）：PENDING（离线持久化重试语义）与已暂存 DELIVERING 不被终结。
- `TestSupersededFailureThenStallTerminalizes`（新增，major-02 两个分支合一）：被取代的 `context canceled` 不误报（保持 UPLOADING）→ 无进展时兜底确定性终结为 FAILED。
- `TestUploadStallTimeoutScalesWithSize`（新增）：阈值 = 基线 + 1 MiB/s 折算。

### 6.4 扩围反向验证（撤守卫 → 测试失败）

| 撤掉的守卫 | 失败测试与断言 |
|---|---|
| `isTerminalTargetStatus` 恒 false | `TerminalTargetsFrozen/late_duplicate_result_cannot_flip_succeeded_target`：`terminal target was flipped by late result`；`/dispatchTarget_does_not_resurrect_terminal_targets` FAIL |
| `markUploading` 终态检查短路 | `TerminalStatesFrozen/{SUCCESS,FAILED,CANCELED}`：`markUploading must refuse terminal transfer` |
| `Cancel` 的 FAILED 拒绝移除 | `ExplicitActionsOnTerminal/FAILED_cancel_rejected_and_state_preserved`：`cancel of failed transfer must be rejected (terminal freeze)` |
| `SweepStalledUploads` 空转 | `SourceUploadStallTimeoutTerminalizesTransfer`（`sweep after deadline = 0, want 1`）、`…UsesPartProgress`、`SupersededFailureThenStallTerminalizes` FAIL |

恢复后 sha256 校验一致，全部测试通过。

### 6.5 扩围确定性复现（repro v2，hub `17a8087c…`）

状态序列 UPLOADING → DELIVERING(24.47s) → SUCCESS(50.94s)；`failed_then_nonfailed=false`；`sha_match=true`；Hub WARN 世代忽略命中（旧尝试 attempt=1 被 current_attempt=2 拒绝）——与 §5.4 结论一致，无回归。

### 6.6 扩围新增行为变化

1. **已暂存传输的续传重放**：SUCCESS/FAILED 的整段/分片 POST 由「200 已暂存幂等返回 / 被接受并写回 UPLOADING」统一改为 `410 Gone`（终态判定先于 SHA 早退）；CANCELED 仍 409。
2. **取消已结束传输**：对 SUCCESS/FAILED 调 `POST /api/transfers/{id}/cancel` 由「200 改写为 CANCELED」改为 400 `transfer already finished: <STATUS>`（前端需隐藏 FAILED 行取消按钮，见 §4）。
3. **目标级冻结**：已终态目标的迟到结果/重投被拒绝并记 WARN `file transfer target write rejected: terminal state frozen`。
4. **源上传停滞兜底**：UPLOADING 无进展超过 30min+size/1MiB/s → FAILED，错误文案 `source upload timed out after … without progress from the source agent`；PENDING 不受影响。
