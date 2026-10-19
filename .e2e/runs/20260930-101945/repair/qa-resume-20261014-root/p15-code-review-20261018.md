# P15 独立代码评审：P6 并发修复 / P5 决策落地 / P14 同类收尾（2026-10-18）

## 0. 评审时点、范围与方法

- **评审时点**：2026-10-18 17:05 – 17:30 CST（评审对象 = 工作树磁盘状态，全部未提交，基线 HEAD `075a26f`）。
- **P14 写入稳定性**：最后一次源码写入为 `internal/hub/connection_gone_test.go` 16:54:37；评审从 17:05 起连续观察 ≥10 分钟无新写入（`find internal web/src -newermt '-6 minutes'` 为空），按**稳定状态**评审。`p14-same-class-report-20261018.md` 在评审时点**尚不存在**（P14 报告未落盘；其代码改动已由 `doc-sync-d5-report-20261018.md` §四 逐项核对）。
- **范围**：`git diff HEAD -- internal/ web/src/`（45 文件、+1558/−522）+ 新增未跟踪文件 16 个（Go 测试 9、Go 源码 2、Web 源码/测试 4）。
- **纪律**：只读。未修改仓库任何文件、未执行 Git 写操作、未部署、**未运行仓库测试**（避免与可能仍在跑的 P14/其他批次争用端口与临时目录；测试有效性改为阅读断言 + 反向验证记录交叉核对）。
- **独立验证手段**（不依赖被评报告自述，脚本在 `/tmp/opencode/`）：
  1. **扫描器 A**（`scan_loops2.py`）：全仓 `for rows.Next()` 循环体（括号配对提取）+ 全仓 129 个「方法体内发起 SQL」的方法名集合 → **嵌套借连接命中 0**。
  2. **扫描器 B**（`scan_tx_closures.py`）：全部写事务闭包（24 处 `runMutationTx`/`WithTx`）内是否用外层 ctx 调 store → **命中 0**；用 HEAD 版 `internal/hub/schedule.go` 做正样本对照，正确命中 `:166`（证明扫描器有效，负结果可信）。
  3. **扫描器 C**：`internal/store` 全部 UPDATE/DELETE 语句 × `RowsAffected`/`execExpectRow` 校验（见 §6）。
  4. **sqlite3 合成数据集**（3.46.1）：验证 JOIN 重写的 NULL / 空集合 / 多目标 / 排序 / 去重语义（附录 A）。
  5. **静态复核**：405 × `Allow` 全量（api.go 47 处 + agent 端点 2 处，**0 缺失**）；前端全部 API 路径 × 已知子路径集合（**0 误伤**）。

---

## 一、逐项结论

### 1. F1 的 JOIN 重写：语义等价 ✅（含 3 条无害行为差异）

**结论：与原逐行查询等价。** 独立验证（附录 A）：

| 维度 | 结论 |
|---|---|
| 字段顺序/完整性 | 传输 10 列 + 目标 7 列逐一对应，`sql.Null*` 仅用于 LEFT JOIN 未命中行；DDL 中 `mode/status/error/updated_at` 均 `NOT NULL`（`internal/store/migrate.go:366-375`），NULL 只可能来自「无目标传输」 |
| NULL（无目标传输） | LEFT JOIN 产生一行全 NULL 目标列 → `if !nodeID.Valid { continue }`（`sqlite_file_transfers.go:96-98`）→ 不构造空目标，与旧实现 `Targets=nil` 一致 |
| 空集合 | `out := []*models.FileTransfer{}`（`:71`）→ JSON `null` 变 `[]`（已在 P6 报告 §1.5 声明；前端 `web/src/features/transfers/index.tsx:409` 用 `transfers.data?.length` 判定，无影响） |
| 多目标 | `ORDER BY t.created_at DESC, t.id, tt.node_id` → 单个传输内按 `node_id` 升序，与旧 `listFileTransferTargets` 的 `ORDER BY node_id` 一致 |
| 去重 | `byID` map 按 `t.ID` 聚合（`:88-92`），每目标一行、每传输一个实例 |
| 错误处理 | 旧实现 `t.Targets, _ = ...` **吞掉**目标查询错误；新实现经 `rows.Err()` 上报 → 由 `writeInternalError` 归一化为 500（不再有半成品列表） |

**其余借用点：已独立确认为 0。** 扫描器 A/B 全仓负结果 + 正样本对照。补充确认：

- `SetMaxOpenConns(8)`/`SetMaxIdleConns(8)` 位于 `internal/store/sqlite.go:80-81`；agent 侧为 4（`internal/agent/store.go:57`），其 rows 循环同样 0 命中。
- `internal/store` 内唯一「先开 rows 再发 SQL」的方法为 `NextGlobalRevision`（`sqlite_executions.go:210-240`）与 `CreateFileTransfer`（`sqlite_file_transfers.go:13-35`），均为**顺序**调用（QueryRow+Scan 后再 Exec），不构成嵌套借用。
- 残余**非缺陷**风险：34 个 `s.db` 直读方法（P6 报告 §7 E′）今日安全（调用点与事务闭包交集为空），但仍是同一类隐患的温床——同意报告建议，作为独立批次机械替换为 `queryer(ctx)`/`execer(ctx)`（见 minor-04）。

**nit-01**：排序新增 `t.id` 兜底 → `created_at` 完全相同的记录展示顺序由「未定义」变为「id 升序」。仅影响并列时间戳，建议在 `docs/TEST_PLAN.md` 注明排序契约。

### 2. F2 的事务重构：无有害竞态 ✅；错误归一化覆盖完整 ✅

- **竞态窗口（存在但无害）**：`internal/hub/schedule.go:150-156` 在事务外读 `GetSchedule` 仅用于审计 `detail`。读取与写事务之间若发生并发更新，审计 detail 可能是**旧 expression/interval**（仅审计字段陈旧，不改变删除结果、revision、tombstone）。该模式与既有 `ScriptManager.Delete`（`definitions.go:119-121`）、`TaskManager.Delete`（`:359-361`）、`AppManager.Delete`（`application.go:101-103`）完全一致，属**对齐既有约定**而非新引入。
- **缺失 id 语义**：`DeleteSchedule` 现经 `execExpectRow` → 不存在 id 时事务整体回滚 → API 404（**修复前是 200 + 孤儿 tombstone/审计**），与 `docs/PRODUCT.md` §46「id 不存在 → 404」一致。
- **错误归一化覆盖**：`internal/hub/api/api.go` 现存 `err.Error()` 仅 4 处 —— `:376`（`writeStoreError` 业务分支，已先经 BUSY/驱动判定）、`:726`（`parseEnrollmentOptions` 输入校验）、`:835`（`normalizeArchitecture`）、`:2225`（`validateSettings`）。逐条确认均为**纯输入校验**，不可能携带驱动原文。所有 store/manager 错误均走 `writeMutationError`/`writeStoreError`/`writeInternalError`。
- **正向核对**：`writeStoreError`（`:362-370`）的 `status >= 500` 分支确保 5xx 目标永不回原文；`writeMutationError`（`:372-380`）先处理 `sql.ErrNoRows` → 404，语义与 PRODUCT §46 一致。
- **nit-02**：`IsDriverError`（`internal/store/sqlite.go:142-166`）含文本兜底（`"sql:"`、`"sqlite"`、约束类文案）。理论上业务文案若含这些子串会被误判为驱动错误 → 500（当前无此文案）。可接受，属防御性判定的已知代价。

### 3. 终态守卫缺口（SOAK-01/02）：根因定位准确 ✅，修复方向合理但**不完整** ⚠️

- **SOAK-01 根因链**：逐行核对**准确**。
  `agent.go:1398-1406`（`startFileTransfer` 取消旧 ctx）→ `agent.go:1415-1424`（`sendFileUploadResult` 把 `context canceled` 当 OK=false 上报）→ `file_transfer.go:536-551`（`HandleUploadResult` 只豁免 CANCELED/SUCCESS/已暂存，其余 `failTransfer`）→ `file_transfer.go:554-563`（FAILED 终态）。触发方是 Hub 自己：`ResumeNode`（`:284-302`）→ `dispatchSource`（`:251-281`）在**旧 HTTP 上传仍在飞**时重发 FileUploadRequest。
- **SOAK-02 根因链**：准确但不完整。报告列了 4 个入口（completeUpload/HandleDeliveryResult/failTransfer/updateTransferAggregate）；实测**还有 3 个同类写入点未列入**（见 major-01）。
- **修复方向评估**：Agent 侧「被取代的上传不上报失败」+ Hub 侧「终态冻结守卫」方向正确，但：
  1. 只做 Agent 侧抑制会留下**无兜底窗口**：源上传真失败时传输停在 UPLOADING，而 `SweepStalledDeliveries` 明确跳过未暂存传输（`file_transfer.go:152` `t.SHA256 == ""` → `continue`），传输永久悬挂并让 `DELETE /api/nodes` 一直 409（`node.go:447-457`）。建议同时补「源上传停滞兜底」或引入 **attempt epoch**（Hub 生成 attempt id，Agent 上报时带回，Hub 只接受当前 attempt 的结果）——后者能从机制上关闭整类问题。
  2. 终态冻结必须**同时覆盖 target 级**（`dispatchTarget`/`HandleDeliveryResult`），否则多目标部分失败后仍可被复活（major-01）。

### 4. P5 CR-3 摘要：原子写正确 ✅；恢复主流程不受影响 ✅

- **原子性**：`internal/agent/deployment_summary.go:88-97` —— 同目录 `.tmp` 写入 + `os.Rename`（POSIX 原子）→ 读方不会看到半截文件 ✅；rename 失败时清理 `.tmp` 并记 ERROR ✅。
- **错误路径**：`buildDeploymentsSummary` 失败（`:79-81`）、序列化失败（`:83-87`）、写失败（`:90-92`）均为「记 ERROR + return」，`writeDeploymentsSummary` 无返回值，调用点 `application.go:861-863` 位于 `RecoverDeployments` 末尾（`logRecoverySummary` 之后），**不影响恢复语义与 journal 状态** ✅。
- **并发**：生产仅一个写者（`agent.go:171` 单次调用 `RecoverDeployments`）→ 无损坏风险。**防御性 nit（nit-03）**：`.tmp` 文件名固定，若将来出现并发刷新（手动刷新/重试），两个写者对同一 tmp 文件 `O_TRUNC` 写入可能交错，rename 后得到**混合内容 JSON**；建议 `os.CreateTemp(dir, name+".tmp.*")` 或加唯一后缀。
- **计数语义**：`CountClosedDeploymentsByKind`（`internal/agent/store.go:852-869`）用 `GROUP BY` 全量统计，不受 `ListAllDeployments` 的 LIMIT 100 影响；`completed_kind TEXT NOT NULL DEFAULT ''`（`store.go:165`、迁移 `:247`）→ NULL 不可能出现，扫进 `string` 安全；旧行 `''` → `unknown` ✅；`deployed` 被排除 ✅。
- **快照一致性（nit-04）**：计数与 active 行来自两次独立查询（`deployment_summary.go:43,54`），无事务包裹 → 恰好跨查询迁移的部署可能「既不在计数、也不在 active」。对「恢复时刻快照」语义可接受，建议在文件契约注释中显式声明。

### 5. P5 跨标签收敛：方案成立 ✅；降级路径与回环已确认无风险

- **回环**：发起标签自身不收到自己的 `storage` 事件（规范保证）；接收端守卫 `if (!user && !accessToken) return`（`session-convergence.ts:19-21`）使回声最多一轮即终止 ✅。
- **幂等**：收敛即 `reset()` 清空会话，后续重复信号被守卫拦截 ✅；多标签同时退出只收敛一次 ✅。
- **存储不可用**：`notifySessionInvalidated` 用 `try/catch` 包裹 `setItem`（`session-sync.ts:19-27`）→ 本标签退出不受影响，其它标签不收敛（报告 §1.7 已声明为限制）。
  **minor-02**：`if (typeof localStorage === 'undefined') return`（`session-sync.ts:20`）本身在某些环境（`dom.storage.enabled=false` / 存储被策略阻断）**取属性即抛 SecurityError**，异常会冒泡到 `reset()` 的调用方（退出登录/401 收敛路径）。`web/src/lib/api.ts:9` 是同一模式（既有风险），非本次新引入，但建议统一改为 `try { ... } catch` 或复用同一个守卫 helper。
- **未验证项**：真实双标签 E2E 未执行（报告已声明）。单页测试用 `StorageEvent` 注入，因此「本标签不收到自己的事件」是**假设**而非被测性质（`session-sync.test.ts:111-122` 在单页里等价重言式）。见 minor-05。
- 触发点选在 `reset()` 单点（`auth-store.ts:52-57`）覆盖面正确：退出登录、401 `nodesteer-unauthorized`、`/me` 401 三条生产失效路径都会广播。

### 6. P14 静默成功修复：覆盖**已完整**（就 API 可见路径而言）✅；未知子路径 404 **未误伤** ✅

- **RowsAffected 覆盖**（扫描器 C，逐条人工复核）：`internal/store` 全部 UPDATE/DELETE 语句中，**API 可寻址的单行资源写路径已全部校验**：users(role/password)、scripts/tasks/schedules/applications(update+delete)、artifacts(delete)、nodes(status/sync/heartbeat/labels/delete)、groups(update/delete)、application 删除与分配。
- **未校验清单（11 个方法）及判定**（见附录 B）：`UpdateExecution`、`UpdateFileTransfer`、`UpdateFileTransferTarget`、`UpdateUserAvatar`、`SetApplicationAssignment`、`DeleteApplicationNodeState`、`RemoveGroupMember`、`DeleteSetting`、`DeleteTombstone`、`PruneChangeLog`、`NextGlobalRevision` —— 逐条核对**调用方**后确认：要么天然幂等（取消分配/删设置/清 tombstone），要么仅内部（revision 计数器、执行结果 upsert），要么上游已有存在性前置检查（`AppManager.Assign` → `application.go:156-164` 先查应用与节点；`FileTransferManager.Retry/Cancel` → `file_transfer.go:659,702` 先 `GetFileTransfer`），要么文档已声明的例外（`UpdateUserAvatar`）。**未发现仍可从 API 触发「静默成功」的路径**（含 `PUT /api/groups/{id}`：`sqlite_users_nodes.go:349-359` 有手工 `RowsAffected` 校验，扫描器只认 `execExpectRow` 才会漏报，已人工纠正）。
- **未知子路径 404 误伤核对**：枚举前端全部 API 路径（`web/src/**` 非测试）与 Agent 侧 URL，命中集合 = {run, cancel, retry, logs, revisions, state, executions, nodes, assign, deploy, revoke-credential, download}，与 `splitResourcePath` 的已知子路径集合**完全吻合**；`/api/artifacts/{id}/download`、`/api/nodes/upgrade`、`/api/nodes/enrollment` 均由更具体路由优先匹配（`api.go:131-135,162`）。
- **尾部斜杠**：`splitResourcePath`（`api.go:315-323`）剥除尾部空段 → `/api/tasks/{id}/` 等价于基础资源（有意收紧，PRODUCT §46/TEST_PLAN 已记录，`subpath_contract_test.go:236-258` 覆盖）。
- **编码字符**：`r.URL.Path` 是**已解码**路径，因此含 `%2F` 的 id 会被拆成额外段 → 404（旧实现会落到基础资源）。节点/任务 id 均为服务端生成 UUID（`node.go:125-126`），无实际影响，但契约上隐含「id 不得含 `/`」。
- **nit-05（解析器不一致）**：`handleTransferByID` 用 `strings.Trim(path, "/")`（`internal/hub/api/api.go:1165`），会把 `/api/transfers//id` 归一为基础资源；`splitResourcePath`（`:315-323`）则把 `//id` 视为未知子路径 → 404。两者对「多余前导斜杠」语义不一致，建议统一到 `splitResourcePath`。

### 7. 契约与文档一致性：基本一致 ⚠️（1 处硬约束表述过宽）

逐条核对（`docs/PRODUCT.md` §46/§47、`docs/ARCHITECTURE.md` §54/§61/§28 与代码）：

| 文档断言 | 代码 | 结论 |
|---|---|---|
| 405 一律带 `Allow`（全端点统一，不再有例外） | api.go 47 处（35 `methodNotAllowed` + 12 直设）+ agent 端点 2 处；**0 缺失** | ✅ |
| `/api/login`、`/api/tasks/{id}/run`、应用 `revisions/nodes/assign/deploy`、脚本 `revisions`、制品 `download` 方法语义 | `api.go:495-499,1487-1493,1883-1899,1919-1928,1953-1959,1783-1790` | ✅ |
| 5 条「不存在资源 → 404 归一化」 | revoke-credential/retry/cancel/executions cancel/users PUT 均经 `writeMutationError` + 上游 `GetX` 前置检查 | ✅ |
| 未知子路径 404 / 已知子路径多余段 404 / 末尾斜杠归一 | `splitResourcePath` + 各 handler | ✅ |
| `POST /api/nodes` 非法 status → 400 `invalid node status: <值>` | `node.go:206-210` 业务错误 → `writeStoreError(400)` 保留文案 | ✅ |
| 存储忙 → 503 全写路径归一化 | `writeInternalError:338-360` + `IsBusyError` | ✅ |
| §61 日志级别语义（连接消失 → Debug/WARN） | `connection_errors.go:19-33` + `api.go:339` + `gateway.go:744-753` | ✅ |
| §54 只读运维查询面（deployments-summary.json 原子写） | `deployment_summary.go` + `application.go:861` | ✅ |
| §54「资源写路径（UPDATE/DELETE）**必须**校验命中行数（execExpectRow）」 | **仅子集实现**（11 个方法未用，其中多数为合法幂等/内部路径） | ⚠️ **minor-01** |

### 8. 回归风险：未发现测试被削弱 ✅（含 2 处需知悉）

- **无文件删除**（`git diff --diff-filter=D` 为空）。
- **Go 既有测试改动仅 1 处**：`internal/hubserver/rbac_audit_test.go:133-137` 400 → 404，属缺陷驱动的契约更新（仍断言「非 403」证明权限已授予），P6 报告 §8 已声明 ✅。
- **Web 既有测试**：`web/src/**/*.test.*` 净增 223 行、仅 3 行删改（`authenticated-layout.test.tsx` 的 `useLocation` mock 改为支持 `select` 的等价实现）✅。
- **新测试有效性**（阅读断言 + 与反向验证记录交叉核对）：
  - `TestListFileTransfersConcurrentNoPoolDeadlock` / `TestTransfersListConcurrentNoHang` / `TestNodesDeleteConcurrentAllSucceed`：文件库（真实池 8）+ barrier 同时发 16 请求 + 20s 超时 → 修复前必然超时失败，与报告 §1.6 的反向验证一致 ✅
  - `TestReadHelpersInsideTxDoNotBorrowSecondConnection`（池压到 1）：任何「事务内再借连接」都会死锁 → 强守卫 ✅
  - `TestWriteMethodsMissingRowReturnErrNoRows`：18 个方法 + 孤儿行断言 + 正向对照（存在行仍成功）→ 断言完整 ✅
  - `subpath_contract_test.go`：10 资源族 × 4 方法 = 40 组 404 + **副作用核对**（基础资源未被改写/删除）+ `TestValidSubpathsStillWork` 防误伤 ✅
  - `TestArtifactDownloadMethodGuard`：断言错误方法**不返回文件内容** ✅；`connection_gone_test.go`：WARN/ERROR 双向断言 ✅
- **需知悉（非本次改动引入的削弱）**：
  - `web/e2e/tests/safe-ui.spec.ts` 新增 `test.skip(!inspect, ...)`（SAFE-23 脚本缓存检查器需 SSH 红线外）→ 该检查在未提供 env 时不再执行；属**显式 skip + 理由**，不是静默删除，但会降低默认覆盖率。
  - `TestSchedulesDeleteConcurrentNoBusyLeak` 接受 503 → 证明的是「不挂起、不外泄驱动原文」，不是「全部成功」；与 PRODUCT §46/TEST_PLAN 记录的契约一致。
  - Web E2E 规格文件被大幅重构（去掉硬编码 QA 用户 UUID、自建夹具、显式 skip）。**在本次声明范围（web/src）之外**，仅按回归风险抽样检查，未发现断言删除式削弱；如需要，建议单独批次评审 `web/e2e/**`。

---

## 二、发现清单（按严重度）

### Blocker

**无。** 未发现会导致数据丢失、错误业务结果或服务不可用的缺陷；F1/F2 两个 P1 级缺陷已正确修复并有独立证据支撑。

### Major

**major-01｜终态守卫缺口还有 3 个未列入 SOAK-02 的写入点（同族未闭合）**
- 位置：
  1. `internal/hub/file_transfer.go:459-464`（`handleUpload` 把传输写回 UPLOADING）
  2. `internal/hub/file_transfer.go:699-724`（`Cancel` 无终态守卫）
  3. 目标级：`internal/hub/file_transfer.go:304-324`（`dispatchTarget` 复活 FAILED target）、`:609-613`（迟到结果改写已终态 target）
- 证据：
  - (1) 可达性由 `failTransfer` 自己造成——`:557` `t.SHA256 = ""` 清空暂存标记，使 `:400` 的「已暂存 → 直接返回」早退失效，因此**已 FAILED 的传输仍能接受新的分片上传**并回到 UPLOADING，随后 `completeUpload`（`:479-502`，无守卫）把它推回 DELIVERING。这正是 SOAK-01/02 复现时间线里 FAILED→DELIVERING 的一半路径。
  - (2) `Cancel` 只在入口拒绝「已取消」，不拒绝 SUCCESS/FAILED：对已成功传输执行 `POST /api/transfers/{id}/cancel` 会把传输置 CANCELED，而 `:710-714` 跳过 SUCCESS 目标 → 出现「传输 CANCELED + 全部目标 SUCCESS」的不自洽状态（且不重算聚合）。SOAK-02 未列此点。
  - (3) `dispatchTarget` 仅拦 `target.Status == SUCCESS/CANCELED`（`:305`），FAILED 目标会被改回 DELIVERING；`HandleDeliveryResult` 仅拦 CANCELED/未暂存（`:605`），已终态目标可被迟到结果改写。SOAK-02 的修复方向只提到「传输级冻结」。
- 建议修法：引入统一 `isTerminalTransfer/isTerminalTarget` 判定，在 `handleUpload`(460)、`completeUpload`(493)、`HandleDeliveryResult`(609)、`updateTransferAggregate`(639)、`failTransfer`(557)、`Cancel`(706)、`dispatchTarget`(320) 七个写入点前置守卫；**唯一解冻入口**保留为 `Retry`（`:656-697`）；`Cancel` 对终态传输应返回业务错误（如 409 `transfer already finished`）而非改写状态。

**major-02｜SOAK-01 若只做 Agent 侧抑制，会留下「源上传无兜底」的悬挂窗口**
- 位置：`internal/hub/file_transfer.go:152`（`SweepStalledDeliveries` 跳过 `t.SHA256 == ""`）+ `:554-563`（唯一把传输终态化的路径依赖 Agent 上报或上传 HTTP 错误）。
- 证据：修复后 Agent 不再上报「被取代的取消」，但若该传输的**当前**上传也失败（进程被杀、网络中断且未触发 HTTP 错误回报），Hub 无任何超时兜底：传输停在 UPLOADING，`NodeManager.Delete` 因 `:447-457` 的活跃传输判定返回 409，节点无法删除，直到下一次 Agent 重连触发 `ResumeNode`。
- 建议修法：为「等待源上传」的传输补停滞超时（可复用 watchdog 周期，按 `t.UpdatedAt` + 上传停滞阈值），或直接采用 attempt epoch 方案（Hub 生成 attempt id，非当前 attempt 的结果一律忽略）。

### Minor

**minor-01｜`docs/ARCHITECTURE.md` §54 硬约束表述过宽（与实现不符）**
- 位置：`docs/ARCHITECTURE.md:1359-1362`「资源写路径（UPDATE/DELETE）必须校验命中行数（`execExpectRow`）」。
- 证据：`internal/store` 中 11 个 UPDATE/DELETE 方法未校验（附录 B），其中 `UpdateExecution`、`UpdateFileTransfer`、`UpdateFileTransferTarget`、`UpdateUserAvatar` 属真实缺口但已被调用方前置检查/文档例外覆盖；`SetApplicationAssignment`、`DeleteApplicationNodeState`、`RemoveGroupMember`、`DeleteSetting`、`DeleteTombstone`、`PruneChangeLog`、`NextGlobalRevision` 属「零行命中合法」的幂等/内部路径。
- 建议修法：把该条限定为「**API 可寻址资源的单行 UPDATE/DELETE** 必须校验命中行数」，并列出「零行命中合法」的例外类别（幂等取消/清理/内部计数器），避免后续批次把文档读成「当前代码违规」。

**minor-02｜`typeof localStorage` 可能抛 SecurityError，冒泡到退出/401 收敛路径**
- 位置：`web/src/lib/session-sync.ts:20`（`web/src/lib/api.ts:9` 同模式，属既有风险）。
- 证据：部分浏览器配置下访问 `window.localStorage` 本身抛 `SecurityError`，`typeof` 不会吞掉异常 → `reset()` 抛出 → 退出登录/跨标签收敛中断。
- 建议修法：改为 `try { const s = window.localStorage; ... } catch { /* 降级 */ }`，或抽取共享的 `safeStorage()` helper 供 `api.ts`/`session-sync.ts` 共用。

**minor-03｜`Cancel` 允许把已终态传输改写为 CANCELED（并入 major-01 修复，单列以便追踪契约）**
- 位置：`internal/hub/file_transfer.go:699-724`；证据同 major-01(2)。
- 建议修法：与 major-01 一并处理；如需保留「取消已失败传输」语义，应在文档明确其为**显式**允许的状态迁移并重算聚合。

**minor-04｜34 个 `s.db` 直读方法仍是同类隐患温床（潜伏，非当前缺陷）**
- 位置：P6 报告 §7 E′ 列出的 executions/audit/logs/sync/revisions/artifacts/scripts 族（示例 `internal/store/sqlite_executions.go:120,148,260,295`）。
- 证据：本次扫描确认当前调用点与事务闭包交集为空（0 命中），故非活跃缺陷；但机制上「事务内用外层 ctx 读」仍是可能的。
- 建议修法：独立批次机械替换为 `queryer(ctx)`，并用池=1 守卫测试覆盖（扩展 `TestReadHelpersInsideTxDoNotBorrowSecondConnection` 的方法清单）。

**minor-05｜跨标签收敛的真实投递与「自身不收到事件」未被测试覆盖**
- 位置：`web/src/lib/session-sync.test.ts:111-122`（单页重言式）、报告 §1.7 已声明。
- 建议修法：部署新构建后按 X13 场景做双标签实测（内存会话 401 + ≤1s 收敛）；如需回归保护，可在 Playwright 侧加一条双 context 用例。

### Nit

**nit-01**｜`ListFileTransfers` 新增 `t.id` 兜底排序 → 并列 `created_at` 的展示顺序由未定义变为 id 升序（`internal/store/sqlite_file_transfers.go:69`）。建议在 `docs/TEST_PLAN.md` 记录排序契约。

**nit-02**｜`IsDriverError` 文本兜底（`internal/store/sqlite.go:142-166`）理论上会把含 `sql:`/`sqlite`/约束文案的**业务**错误升级为 500（当前无此文案）。可在函数注释中显式标注该取舍。

**nit-03**｜`deployments-summary.json.tmp` 文件名固定（`internal/agent/deployment_summary.go:89`）：当前单写者安全，建议 `os.CreateTemp` 防未来并发刷新。

**nit-04**｜`buildDeploymentsSummary` 的计数与 active 行来自两次独立查询（`:43,:54`），快照可能自相矛盾；建议在文件契约注释声明「两次查询之间发生的迁移可能不计入」。

**nit-05**｜路径解析器不一致：`handleTransferByID` 的 `strings.Trim(path, "/")`（`internal/hub/api/api.go:1165`）会归一前导多余斜杠，`splitResourcePath`（`:315-323`）不会（`/api/transfers//id` → 基础资源 vs 404）。建议 Hub 侧统一到 `splitResourcePath`（Agent 数据端点 `internal/hub/file_transfer.go:345` 使用同款 `strings.Trim`，属独立 API 面，可保持现状）。

**nit-06**｜`handleNodeByID`（`:1063-1065`）、`handleTransferByID`（`:1174-1177`）等仍用 `w.Header().Set("Allow", ...)` + `writeErr` 而非 `methodNotAllowed`，风格不一致（行为等价）。

**nit-07**｜`IsConnectionGone` 把 `io.EOF` 归入「连接消失」（`internal/hub/connection_errors.go:25`）：当前 API 侧无「真实读错误经 writeInternalError」的路径，安全；若将来新增，需重新评估。

**nit-08**｜`gateway.go:442,484,517` 忽略 `UpdateSyncState` 的 `sql.ErrNoRows`（含新语义）→ 孤儿会话仅靠**心跳**回收；P14 注释「Gateway 据此回收孤儿会话」对心跳成立，对 revision-check/sync-ack 不成立。可接受，建议注释补充适用范围。

---

## 三、必须在最终部署前修的项

| 优先级 | 项 | 理由 |
|---|---|---|
| **P0（部署前）** | 无 blocker；**建议将 major-01 + major-02 纳入 SOAK-01/02 的修复范围**（本次不修，仅要求修复方案覆盖这 3 个额外写入点与源上传兜底） | 若按 SOAK-02 现状的 4 个入口修复，`Cancel`/`handleUpload`/target 级仍可翻转终态 → 同一缺陷类半闭合，复测会再次命中 |
| **P0（部署前，零风险）** | minor-01（ARCHITECTURE §54 表述收窄） | 该文件是「不可违反的强制约束」，过宽表述会让后续批次误判当前代码违规或反向放宽实现 |
| P1（本批或紧随） | minor-02（localStorage 访问包裹 try/catch） | 退出登录路径的健壮性；改动 3 行、无行为回归 |
| P2（独立批次） | minor-04（34 个 `s.db` 直读 → `queryer(ctx)`）、minor-05（双标签实测）、nit-03/04 | 潜伏风险与可观测性完善，不阻塞本次部署 |

**部署后必须复测（本评审未执行，因纪律只读且未部署）**：
1. 16 并发 `GET /api/transfers`、16 并发 `DELETE /api/nodes/{id}`、16 并发 `DELETE /api/schedules/{id}` 的真实实例复跑（脚本 `evidence/scripts/deadlock_probe.py` 等）。
2. SOAK-01/02 的 50MB 断连重连场景（若修复已落地）。
3. 双标签登出收敛（X13）。
4. `deployments-summary.json` 在真实 Agent 数据目录的落盘与内容核对。

---

## 四、未验证项与剩余风险（如实列出）

1. **未运行任何仓库测试**（只读纪律 + 避免与在跑批次争用资源）：F1/F2 的反向验证（撤修复必失败）与全量测试结果均**引自 P6/P5 报告**，本次未独立复现；本评审以「代码路径 + 断言阅读 + 扫描器正样本对照」交叉验证其可信度。
2. **未部署、未做真实并发/断连/双标签复测**（同上）。
3. **P14 无独立报告**：其代码改动（RowsAffected 铺开、未知子路径 404、agent 端点 Allow、连接消失日志降级）已由 `doc-sync-d5-report-20261018.md` §四 记录，本次按磁盘状态评审，未见到 P14 自身的验证清单与反向验证记录 → **P14 的验证证据链尚不完整**（建议补 `p14-same-class-report-20261018.md` 后再进入部署）。
4. **`web/e2e/**` 未按评审标准检查**（不在声明范围）：抽样未发现断言删除式削弱，但该目录本轮改动量大（395+/216−），建议单独评审批次。
5. **扫描器局限**：扫描器 A/B/C 基于括号配对 + 正则，不做类型/别名分析（如 `st := s.store; st.GetX(ctx,...)` 这类间接调用不会被识别）。为降低该风险，已用正样本（HEAD 版 `schedule.go`）验证扫描器可命中目标模式，并人工复核了扫描器 C 的全部 11 条「未校验」结果（发现 1 处漏报：`UpdateGroup` 手工校验）。

---

## 附录 A：JOIN 重写语义验证（sqlite3 合成数据）

```sql
-- 数据：A 无目标；B 双目标（z-node/a-node）；C 单目标；D 与 C 同 created_at
SELECT t.id, tt.node_id
FROM file_transfers t LEFT JOIN file_transfer_targets tt ON tt.transfer_id = t.id
ORDER BY t.created_at DESC, t.id, tt.node_id;
```
结果（3.46.1）：
```
B / a-node      -- 目标按 node_id 升序（与旧 ORDER BY node_id 一致）
B / z-node
C / m-node
D / NULL        -- 无目标 → 单行 NULL，代码 :96-98 跳过，不构造空目标
A / NULL
```
结论：字段顺序、NULL 行处理、多目标排序、去重均与旧实现等价；唯一差异是并列 `created_at` 的排序由未定义变为 `t.id` 升序（nit-01）。

## 附录 B：`internal/store` UPDATE/DELETE × RowsAffected 校验全量核对

| 方法 | 语句 | 校验 | 判定 |
|---|---|---|---|
| `UpdateUserRole` / `UpdateUserPassword` | UPDATE users | `execExpectRow` | ✅ API 可见 |
| `UpdateUserAvatar` | UPDATE users | 无 | ⚠️ 文档例外（仅 SSO 同步路径 `hub/auth/auth.go:210`） |
| `UpdateScript`/`DeleteScript`/`UpdateTask`/`DeleteTask`/`UpdateSchedule`/`DeleteSchedule` | UPDATE/DELETE 定义表 | `execExpectRow` | ✅ |
| `UpdateApplication`/`DeleteApplication`/`DeleteArtifact` | UPDATE/DELETE | `execExpectRow` | ✅ |
| `UpdateNodeStatus`/`UpdateNodeSyncState`/`UpdateNodeHeartbeat`/`SetNodeLabels`/`DeleteNode` | UPDATE/DELETE nodes | `execExpectRow` | ✅ |
| `UpdateGroup` | UPDATE node_groups | **手工 `RowsAffected`**（`sqlite_users_nodes.go:349-359`） | ✅（扫描器漏报，人工纠正） |
| `DeleteGroup` | DELETE node_groups | `execExpectRow` | ✅ |
| `SetApplicationAssignment` / `DeleteApplicationNodeState` | DELETE 关联表 | 无 | ✅ 零行命中合法（取消分配），上游 `AppManager.Assign` 已校验应用/节点存在 |
| `RemoveGroupMember` | DELETE 成员 | 无 | ✅ 幂等；无 API 调用点（仅接口声明） |
| `UpdateFileTransfer` / `UpdateFileTransferTarget` | UPDATE 传输/目标 | 无 | ✅ 调用方（Retry/Cancel/派发/结果处理）均先 `GetFileTransfer`；此处**刻意保守**，避免 ErrNoRows 中断交付流程 |
| `UpdateExecution` | UPDATE executions | 无 | ✅ 执行结果 upsert 语义；无 API 写入口 |
| `DeleteSetting` / `DeleteTombstone` / `PruneChangeLog` / `NextGlobalRevision` | DELETE/UPDATE 内部表 | 无 | ✅ 零行命中合法（清理/计数器） |

---

## 结论

- **F1（JOIN 重写）**：语义等价，全仓无其它嵌套借用点（双扫描器 + 正样本对照）→ **通过**。
- **F2（事务重构）**：无有害竞态，错误归一化覆盖完整（仅 4 处输入校验保留原文）→ **通过**。
- **终态守卫缺口（SOAK-01/02）**：根因定位准确，修复方向合理但**不完整**（major-01/02，本次按要求不修，仅要求修复方案扩围）→ **需扩围后再修**。
- **P5 CR-3 摘要**：原子写与错误路径正确，恢复主流程不受影响 → **通过**（nit-03/04 防御性建议）。
- **P5 跨标签收敛**：方案成立、无回环、幂等正确 → **通过**（minor-02 健壮性建议；真实双标签待复测）。
- **P14 静默成功修复**：API 可见路径覆盖完整，未知子路径 404 未误伤合法路径 → **通过**（minor-01 文档收窄；P14 验证证据链待补）。
- **契约/文档一致性**：除 ARCHITECTURE §54 一条硬约束表述过宽外一致 → **基本通过**。
- **回归风险**：无测试删除、无断言式削弱；新测试断言强度足以抓住目标缺陷（反向验证引自 P6 报告）→ **通过**。

**总体判定：本轮未提交改动中不存在 blocker；F1/F2 两个 P1 缺陷的修复可独立确认有效。建议在补齐 ARCHITECTURE §54 表述与 SOAK-01/02 修复方案扩围后进入部署；部署后按 §三 的 4 项复测清单执行。**
