# 文档同步报告 D5：P6/P13/P14 并发与契约收紧批次（2026-10-18）

- 任务：把 **P6/P13/P14 批次的对外契约与运维语义变更**同步进 `docs/`
- 依据：
  - 实现报告：`p6-concurrency-fix-report-20261018.md`（同目录，F1–F8）
  - 缺陷清单：`qa-resume-20261018-stress-authz/findings.json`（F1–F8）、`qa-resume-20261018-scale/findings.json`（OBS-SCALE-02）
  - 源码逐处回核（以 2026-10-18 16:52 工作树冻结状态为准）：
    - `internal/hub/api/api.go`（`:305-308` 405 统一、`:336-355` writeInternalError、`:492-505` login、`:655-710` nodes、`:966-1060` node 子路径/revoke、`:1157-1220` transfers、`:1261-1310` groups、`:1350-1425` scripts、`:1466-1530` tasks run、`:1636-1681` schedules、`:1740-1800` artifacts、`:1843-2020` applications、`:2064-2130` executions、`:2370-2460` users）
    - `internal/hub/file_transfer.go:362-380`、`internal/hub/gateway.go:740-790`、`internal/hub/connection_errors.go:11-33`
    - `internal/store/sqlite.go:64-91/138-164`、`internal/store/sqlite_file_transfers.go:56-109`、`internal/store/sqlite_users_nodes.go:69-98`、`internal/store/sqlite_definitions.go`、`internal/store/sqlite_artifacts_apps.go`、`internal/hub/schedule.go:147-185`
  - 测试证据：`internal/hub/api/method_contract_test.go`、`concurrency_stress_test.go`、`normalized_error_test.go`、`subpath_contract_test.go`、`download_method_test.go`；`internal/hub/agent_transfer_method_test.go`、`connection_gone_test.go`；`internal/store/sqlite_file_transfers_test.go`、`sqlite_tx_read_test.go`
- 边界：只改 `docs/PRODUCT.md`、`docs/ARCHITECTURE.md`、`docs/TEST_PLAN.md` + 本报告；未改源码、配置、`web/**`、`.e2e/**` 既有状态文件；无 Git 操作
- **过程说明（重要）**：本批 P14 修复在本次文档同步期间由并行会话持续写入工作树（`api.go` 16:35→16:52、`file_transfer.go` 16:36、`gateway.go` 16:37→16:52、`subpath_contract_test.go` 16:46、`download_method_test.go` 16:52）。本报告以 **16:52 冻结状态**为准，并在第五节记录验证命令与结果；若 P14 之后继续改动，本文档需按最终状态复检。

---

## 一、逐条改动（7 项同步 + P14 追加细节）

### 1. 方法语义收紧（login / run / 应用子资源 / 脚本版本 / 制品下载）

- 文件/位置：`docs/PRODUCT.md` §46 方法语义（改后 `:1388-1392`）；`docs/TEST_PLAN.md` §8.2（改后 `:500`）
- 改前要点：仅列 `/api/audit`、`/api/me`、`/metrics`、`/api/logout` 四个端点的方法语义；`/api/login`、`/api/tasks/{id}/run`、应用子资源、脚本 revisions、制品 download 的方法约束均无描述。
- 改后要点：
  - `/api/login` 仅 `POST`（其它方法 → 405 + `Allow: POST`，且不建立会话）；
  - `/api/tasks/{id}/run` 仅 `POST`（`GET`/`PUT` 等 → 405 + `Allow: POST`，不进入 RunNow、零执行记录）；
  - `/api/applications/{id}/revisions`、`/nodes` 仅 `GET`；`/assign`、`/deploy` 仅 `POST`；
  - `/api/scripts/{id}/revisions`（含 `/{版本号}`）仅 `GET`（`PUT` 等 405 + `Allow: GET`，不再落入脚本本体写路径——P14 追加收紧）；
  - `/api/artifacts/{id}/download` 仅 `GET`/`HEAD`（其它方法 405 + `Allow: GET, HEAD`，且不返回文件内容——P14 追加收紧）。
- 依据：`api.go:492-505`（login 405）、`:1466-1530`（run 405 + 不触发 RunNow）、`:1843-2020`（revisions/nodes GET、assign/deploy POST）、`:1350-1425`（scripts revisions 仅 GET）、`:1740-1800`+`:1787`（download GET/HEAD）；测试 `TestLoginRejectsNonPostMethods`、`TestTaskRunRejectsNonPostMethods`、`TestApplicationSubresourcesRejectUnsupportedMethods`、`TestValidSubpathsStillWork`、`TestArtifactDownloadMethodGuard`。

### 2. 405 一律带 `Allow` 头（全端点，含 Agent 侧）

- 文件/位置：`docs/PRODUCT.md` §46 方法语义（改后 `:1394`）；§34.1（改后 `:1012`）；`docs/TEST_PLAN.md` §8.2（改后 `:499`）、§12 SYS-59/60（改后 `:619-620`）
- 改前要点：docs 明确写「scripts/tasks/schedules/groups/artifacts/applications 等其余端点**暂不带** `Allow`（现状保留）」；Agent 侧 `/agent/transfers/*` 无方法契约。
- 改后要点：
  - 405 **一律附 `Allow` 头**（全端点统一，不再有例外；缺陷 QA-20261018-F6 补齐）；给出代表值示例；
  - Agent 侧数据端点方法固定：`upload` 仅 `POST`、`download` 仅 `GET`，其它方法 405 + `Allow: POST`/`Allow: GET`；未知动作或多余路径段 → 404（不得被当作已知动作执行）。
- 依据：`api.go` 全部 405 响应点（静态复核 45/45 带 Allow）；`file_transfer.go:362-380`（Agent 侧 Allow）；测试 `TestMethodNotAllowedCarriesAllowHeader`、`TestAgentTransferEndpoint405CarriesAllow`。

### 3. 不存在资源 → 404 归一化（5 条路径）

- 文件/位置：`docs/PRODUCT.md` §46 状态码契约（改后 `:1406-1408`）；`docs/TEST_PLAN.md` §8.2（改后 `:503`）
- 改前要点：docs 只有 PUT/DELETE 类 404 与 nodes oracle 行；`POST /api/nodes`、4 个子资源写端点、`PUT /api/users/{id}` 的存在性语义完全空白。
- 改后要点：
  - `POST /api/nodes`：id 不存在 → 404 `node not found`（状态与标签两路径一致，不再 500 / 静默 200）；status 非法 → 400 `invalid node status: <值>`；
  - `POST /api/nodes/{id}/revoke-credential` → 404 `node not found`；`POST /api/transfers/{id}/retry`、`/cancel` → 404 `transfer not found`；`POST /api/executions/{id}/cancel` → 404 `execution not found`（不再 400 + `sql: no rows in result set`）；业务错误仍保持 400 + 业务文案；
  - `PUT /api/users/{id}`：用户不存在 → 404 `user not found`（不再静默 200）；其余持久化失败保持 500。
- 依据：`api.go:655-710`（nodes POST 前置存在性检查）、`:966-1060`（revoke）、`:1157-1220`（retry/cancel）、`:2064-2130`（executions cancel）、`:2370-2460`（users PUT）；store 层 `execExpectRow`（`sqlite_users_nodes.go:69-98` 等）；测试 `TestMissingNodeStatusUpdateIs404`、`TestSubresourceMissingIDIs404`、`TestUserUpdateMissingIs404`。

### 4. 存储忙 → 503 全写路径归一化（含 schedules 删除路径）

- 文件/位置：`docs/PRODUCT.md` §46 状态码契约（改后 `:1412`）；`docs/TEST_PLAN.md` §8.2（改后 `:504`）
- 改前要点：已写「SQLITE_BUSY/LOCKED → 503 `storage busy, please retry`」，但未说明此前 schedules 删除路径漏网（409 + 驱动原文）与并发行为。
- 改后要点：503 归一化覆盖**全写路径**（含并发 `DELETE /api/schedules/{id}`）；并发删除正常时 200，真实 BUSY 才 503；驱动原文只进日志；测试断言 16 并发删除全部 200 或 503 且响应体无 SQLite 原文、BUSY 时有 error 日志。
- 依据：`api.go:1671-1674`（删除分支改 `writeMutationError`）、`internal/hub/schedule.go:147-185`（审计读取移出写事务）、`api.go:336-380`（`writeInternalError`/`writeStoreError`/`writeMutationError`）；测试 `TestSchedulesDeleteConcurrentNoBusyLeak`、`TestWriteMutationErrorNormalization`。

### 5. 未知子路径 → 404（P14；含多余段与末尾斜杠）

- 文件/位置：`docs/PRODUCT.md` §46 状态码契约（改后 `:1409`）；`docs/TEST_PLAN.md` §8.2（改后 `:501`）
- 改前要点：docs 完全空白；findings `observations` 记录「未知子路径按基础资源处理」（如 `DELETE /api/tasks/{id}/bogus` 会删除任务）。
- 改后要点：`/api/<resource>/{id}/<未知子路径>` 一律 404 + 归一化 not found 文案（任意方法），不落入基础资源分支；已知子路径追加多余段（`/run/extra`、`/revisions/1/extra`）同样 404；末尾斜杠归一为同一资源（`/api/tasks/{id}/` 等价于 `/api/tasks/{id}`）。
- 依据：`api.go:310-324`（`splitResourcePath`）+ 10 个资源 handler（nodes/tasks/scripts/schedules/groups/applications/artifacts/executions/users/transfers）；测试 `TestUnknownSubpathIs404ForAllResources`（10 资源族 × 4 方法矩阵 + 零副作用核对）、`TestKnownSubpathExtraSegmentsAre404`、`TestTrailingSlashIsBaseResource`。

### 6. 日志级别语义（客户端取消 / 连接已关闭不记 ERROR）

- 文件/位置：`docs/ARCHITECTURE.md` §61 Observability（改后 `:1537`）；`docs/PRODUCT.md` §46 状态码契约（改后 `:1412` 尾部）；`docs/TEST_PLAN.md` §8.2（改后 `:504` 尾部）
- 改前要点：§61 只写「内部与存储层错误必须写 error 级结构化日志」，会被读作「所有错误必须 ERROR」。
- 改后要点：连接已关闭/客户端取消（`context.Canceled`、`net.ErrClosed`、`io.EOF`、`use of closed network connection`、`connection reset by peer`、`broken pipe`，经 `hub.IsConnectionGone` 统一判定）属正常清理场景：API 错误路径降级 Debug（`api request canceled or connection gone`，仅 method/path；HTTP 响应仍为归一化状态码），Gateway 在途写失败降级 WARN；真实错误（`io.ErrUnexpectedEOF`、写限额超限等）保持 error 级。
- 依据：`internal/hub/connection_errors.go:11-33`（判定）、`api.go:336-355`（Debug 降级）、`gateway.go:740-790`（WARN/ERROR 分流）；测试 `TestClientCancelNotLoggedAsError`、`TestWriteInternalErrorConnectionGoneNotError`、`TestIsConnectionGoneClassification`、`TestWriteLoopClosedConnectionLogsWarnNotError`、`TestLogWriteFailureGenuineErrorStaysError`。

### 7. 存储/事务并发硬约束（架构级）

- 文件/位置：`docs/ARCHITECTURE.md` §54 Hub 数据库（改后 `:1359-1364`）
- 改前要点：§54 只有连接层（WAL/`_txlock=immediate`/busy_timeout）与错误归一化，无「持 rows 再借连接」「事务闭包内用外层 ctx」约束；读者无法从文档推出 F1/F2 的根因边界。
- 改后要点：
  - **禁止在持有 `rows` 时再发起需要第二条池连接的查询**；列表读取必须单条 SQL 一次取回（如 `file_transfers LEFT JOIN file_transfer_targets`），不得在 `rows.Next()` 循环内嵌套查询；
  - **写事务闭包（`runMutationTx`/`WithTx`）内只允许用事务上下文（`txctx`）调 store**；`queryer(ctx)`/`execer(ctx)` 在事务上下文中复用事务连接；禁止用外层 `ctx` 在事务内发起新的存储调用（等于持写锁再向连接池借第二条连接）；
  - **连接池固定 `SetMaxOpenConns(8)`**；上述两类嵌套借用都会在 ≥8 并发时耗尽连接池并死锁挂起（只能靠客户端断开释放），挂起期间拖垮全库端点；
  - 资源写路径（UPDATE/DELETE）必须校验命中行数（`execExpectRow`）：零行命中返回 `sql.ErrNoRows`，由 API 层归一化为 404，不得静默成功。
- 依据：`internal/store/sqlite_file_transfers.go:56-109`（单条 JOIN 修复与注释）、`internal/hub/schedule.go:147-159`（事务外读取与注释）、`internal/store/sqlite.go:31-48/80`（`queryer`/`execer`/池大小）、`sqlite_users_nodes.go:69-98`（`execExpectRow`）；测试 `TestListFileTransfersConcurrentNoPoolDeadlock`、`TestReadHelpersInsideTxDoNotBorrowSecondConnection`、`TestTransfersListConcurrentNoHang`、`TestNodesDeleteConcurrentAllSucceed`。

### 8. （P14 追加）Agent 数据端点方法契约

- 文件/位置：`docs/PRODUCT.md` §34.1（改后 `:1012`）；`docs/TEST_PLAN.md` §12 SYS-59/60（改后 `:619-620`）
- 改前要点：§34.1 只写「二进制使用 Agent Gateway 的 HTTPS 数据端点」，无方法契约；SYS-59/60 只写身份认证与方向约束。
- 改后要点：`upload` 仅 `POST`、`download` 仅 `GET`；其它方法 405 + `Allow`；未知动作/多余段 → 404；SYS-59/60 增补 405 + `Allow` 断言。
- 依据：`file_transfer.go:362-380`；测试 `TestAgentTransferEndpoint405CarriesAllow`。

---

## 二、冲突旧表述改正（逐条）

| # | 文件/位置（改前） | 改前要点 | 改后要点 | 原因 |
|---|-----------|----------|----------|------|
| 1 | `docs/PRODUCT.md` §46 方法语义（改前 `:1388`） | 「405 已附 `Allow` 的端点：…；scripts/tasks/schedules/groups/artifacts/applications 等其余端点**暂不带** `Allow`（现状保留）」 | 「405 **一律附** `Allow` 头（全端点统一，不再有例外；…QA-20261018-F6 已补齐）」 | F6 修复后 api.go 全部 405 响应点（静态复核 45/45）带 Allow，旧表述与代码冲突 |
| 2 | `docs/TEST_PLAN.md` §8.2（改前 `:499`） | 「非法方法：405；`/api/me`、…、`/api/transfers/{id}` 须带 `Allow`」（仅列部分端点） | 「405，且**所有** 405 响应一律带 `Allow` 头（逐端点校验精确值）」 | 同上；旧句会被读作「仅列举端点带 Allow」 |
| 3 | `docs/PRODUCT.md` §46 状态码契约（改前 `:1402`） | 「…驱动原文只进 Hub 日志，不回响应体，且 Hub 对内部错误写 error 级日志」 | 增补「全写路径归一化，含并发 `DELETE /api/schedules/{id}` 路径」与「连接已关闭/客户端取消仅日志降级，HTTP 响应语义不变」 | 旧句未覆盖 schedules 删除漏网（F2）与 F8/SCALE-02 的日志降级语义 |
| 4 | `docs/TEST_PLAN.md` §8.2（改前 `:501`） | 「存储忙锁：503 `storage busy, please retry`；…Hub 日志须有 error 级记录」 | 增补「（含并发 `DELETE /api/schedules/{id}` 路径）」与「连接已关闭/客户端取消不记 error 级日志」 | 同上 |
| 5 | `docs/ARCHITECTURE.md` §61（改前 `:1528`） | 「内部与存储层错误**必须**写 error 级结构化日志…」 | 保留原句并新增「日志级别语义」段：连接已关闭/客户端取消降级 Debug/WARN，真实错误保持 error 级 | 旧句会被读作「所有错误必须 ERROR」，与 F8/SCALE-02 的降级语义冲突 |

> 说明：上表 3–5 属「旧表述仍被保留但已不完整/易误读」，本次以增补+限定语方式改正，未删除原有义务句；1–2 属与代码直接冲突，已改写。

---

## 三、核查过但未改动（无冲突 / 同向）

| 位置 | 原文要点 | 判定 |
|---|---|---|
| `docs/REAL_TEST_REPORT.md`、`docs/UI_FULL_TEST_REPORT_20260915.md`（含 409 行） | 历史测试报告；其中 409 为「应用被任务引用」的业务冲突语义 | 与 schedules 删除 BUSY 归一化无关；历史报告不重写（沿用 D2 原则） |
| `docs/TEST_ENV_SIM.md` §Bug B（`:225-231`） | Agent 侧同步事务并发修复（`execContext`/`execInTx`） | 与 Hub §54 约束同向、不同组件，未动 |
| `docs/ARCHITECTURE.md` §67 核心不变量、`docs/PRODUCT.md` §51 一期核心不变量 | 18/17 条不变量 | F1/F2 的「禁止嵌套借连接」属实现层硬约束，按 D2 先例不加核心不变量，落 §54 |
| `docs/PRODUCT.md` §17/§46 nodes oracle 行（`:1404`） | 先鉴权后存在性、admin 404 | D3 已同步，本批未改变该语义 |
| `docs/TEST_PLAN.md` §8.3 三角色矩阵 | 越权 403 语义 | 方法守卫/404 归一化不改变越权 403，无冲突 |
| `docs/PRODUCT.md` §46 「方法守卫先于角色鉴权与存在性判定」行 | 一般性契约 | 对本批新增守卫端点（login/run/revisions/nodes/assign/deploy/scripts revisions/download）成立，未动 |
| `docs/DEPLOYMENT.md`、`docs/TEST_ENV*.md`、`docs/TRACEABILITY_MATRIX.md`、`docs/IMPLEMENTATION_PROMPT.md` | 无本批契约表述 | 全 docs 检索 `暂不带 Allow`/`409`/`sql: no rows`/`use of closed` 确认无其他残留命中 |

---

## 四、P14 落地核对（逐项）

P14 修复在本次文档同步期间由并行会话写入工作树；以下为 16:52 冻结状态的实际核对结果：

| 项 | 结论 | 证据 |
|---|---|---|
| Agent 侧 405 带 `Allow`（upload/download） | ✅ 已落地 | `file_transfer.go:363-377`；`agent_transfer_method_test.go`（405 + 精确 Allow、未知动作 404、缺认证仍 401） |
| 未知子路径 → 404（不落入基础资源） | ✅ 已落地（10 资源族 × 任意方法） | `splitResourcePath` + 各 handler；`subpath_contract_test.go` `TestUnknownSubpathIs404ForAllResources`（含零副作用核对） |
| 已知子路径多余段 → 404 | ✅ 已落地 | `TestKnownSubpathExtraSegmentsAre404`（含 `/run/extra` 零执行记录） |
| 末尾斜杠归一 | ✅ 已落地 | `TestTrailingSlashIsBaseResource` |
| 连接已关闭/客户端取消日志降级 | ✅ 已落地（API Debug / Gateway WARN） | `connection_errors.go`；`gateway.go:740-790`；`connection_gone_test.go`；`download_method_test.go` |
| artifact download 方法守卫（新增发现） | ✅ 已落地并已同步文档 | `api.go` download 分支；`TestArtifactDownloadMethodGuard` |
| **因 P14 未落地而跳过的项** | **无** | 上述项均在本次同步窗口内落地并经测试复核后写入 docs |

残余风险：P14 若在 16:52 之后继续改动（工作树仍在演进），docs 需按最终状态复检；本报告已记录验证时点。

---

## 五、验证记录

- 定向契约测试（16:52 冻结状态，`TMPDIR=/var/tmp/nodesteer-go-tmp`）：
  - `go test ./internal/hub/api/ -run 'TestUnknownSubpathIs404ForAllResources|TestKnownSubpathExtraSegmentsAre404|TestValidSubpathsStillWork|TestTrailingSlashIsBaseResource|TestMethodNotAllowedCarriesAllowHeader|TestLoginRejectsNonPostMethods|TestTaskRunRejectsNonPostMethods|TestApplicationSubresourcesRejectUnsupportedMethods|TestMissingNodeStatusUpdateIs404|TestSubresourceMissingIDIs404|TestUserUpdateMissingIs404|TestSchedulesDeleteConcurrentNoBusyLeak|TestClientCancelNotLoggedAsError|TestTransfersListConcurrentNoHang|TestNodesDeleteConcurrentAllSucceed' -count=1` → ✅ `ok`（2.762s）
  - `go test ./internal/hub/ -run 'TestAgentTransferEndpoint405CarriesAllow|TestIsConnectionGoneClassification|TestWriteLoopClosedConnectionLogsWarnNotError|TestLogWriteFailureGenuineErrorStaysError' -count=1` → ✅ `ok`
  - `go test ./internal/store/ -run 'TestListFileTransfersReturnsTargets|TestListFileTransfersConcurrentNoPoolDeadlock|TestReadHelpersInsideTxDoNotBorrowSecondConnection' -count=1` → ✅ `ok`
  - 全包回归：`go test ./internal/hub/api/ ./internal/hub/ ./internal/store/ -count=1` → ✅ 三个包全部 `ok`
- 静态复核（脚本，只读）：`api.go` 全部 405 响应点 45 个（11 显式 + 34 走 `methodNotAllowed` 帮助函数）**全部带 `Allow`**，缺失 0；`file_transfer.go` 2 个 405 均带 `Allow`。
- `git diff --numstat -- docs/`：`docs/ARCHITECTURE.md` +11、`docs/PRODUCT.md` +15/-3、`docs/TEST_PLAN.md` +8/-5；其中会话前既有 CR-3 改动 2 处（共 +4 行）未触碰，本次 D5 净改动为 PRODUCT +13/-3、ARCHITECTURE +9、TEST_PLAN +8/-5。
- `git status --short docs/`：仅上述 3 个 docs 文件被修改；工作树中源码/`web/**`/`.e2e/**` 既有改动保持原样，未回退、未覆盖。
- 本次未执行部署与真实环境复测；文档结论以源码逐处回核 + 单测/契约测试结论为依据。
