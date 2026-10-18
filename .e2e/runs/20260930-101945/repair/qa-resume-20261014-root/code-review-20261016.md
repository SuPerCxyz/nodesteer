# 7 条缺陷修复 代码评审报告（2026-10-16）

- 评审人：资深 Go 代码评审员（子 Agent），**只读**独立评审
- 评审对象：`repair-plan-20261016.md` 7 条缺陷的实施 + 测试（HEAD `dddee4f` 工作树 diff + untracked 文件）
- 评审输入：方案文件、实施报告、`git diff`、untracked 新文件、`docs/ARCHITECTURE.md` §52/§67、`docs/PRODUCT.md` §41/§42
- 评审方式：逐文件读代码与测试 + 独立复跑验证（`go build ./...`、`go vet`、`gofmt -l`、`go test ./internal/agent/... ./internal/hub/... ./internal/store/... -count=1` 全绿）+ 全仓调用方检索（前端 web/src、E2E web/e2e、Hub/Agent 各分支）
- 结论摘要：**blocker 0 条、major 2 条、minor 5 条、nit 5 条**；7 条修复的主路径实现与测试可信，可进入回归验收；2 条 major 需在交付/Archive 前处理（文档同步）+ 建议在 AR-32 回归前决定 superseder 边界是否先收紧。

---

## 一、严重度分布与逐条清单

| 编号 | 严重度 | 主题 | 位置 |
|---|---|---|---|
| CR-1 | **major** | superseded 判定把“非成功部署”的 DONE 行也当成功部署 → 误跳过可恢复的回滚 | `internal/agent/application.go:808-816` |
| CR-2 | **major** | 新 phase `ROLLBACK_FAILED` 与 P1-4 新约束未同步 ARCHITECTURE §52 / PRODUCT §42 | `docs/ARCHITECTURE.md:1203-1224`、`docs/PRODUCT.md:1167+` |
| CR-3 | minor | legacy/绑定失效/superseded/PREPARING 行无终态、无运维出口，永久悬挂 | `internal/agent/application.go:743-761`、`internal/agent/store.go:755` |
| CR-4 | minor | “三组合均可用”表述易被读成“三组合都已修复”；升级顺序约束需写入发布说明 | `implementation-report-20261016.md:31`、`internal/agent/sync_validation.go:35-53` |
| CR-5 | minor | 组名唯一性校验在事务外，竞态下仍回源 raw SQLite UNIQUE 文案；名称未存储归一化 | `internal/hub/node.go:302,324-338`、`internal/hub/api/api.go:1098-1111` |
| CR-6 | minor | P2-6 非 `ErrNoRows` 的存储错误仍回 `err.Error()` 原文（已声明 A5，残余外泄面） | `internal/hub/api/api.go:305-311` |
| CR-7 | minor | P3-7 方法收紧后 E2E 证据/台账未同步（GAP-17 文案过期、API-CONTRACT 期望待更新） | `web/e2e/tests/gap-ui.spec.ts:641-649` |
| CR-8 | nit | `failed()` 内联 if 在 journalErr 非 nil 时吞掉 ROLLBACK_FAILED 写失败错误 | `internal/agent/application.go:415-417` |
| CR-9 | nit | `deploymentStartedAfter` 单边解析失败即回退字典序比较，方向性不确定 | `internal/agent/application.go:819-828` |
| CR-10 | nit | `handleGroupByID` PUT 预检把任何 `GetGroup` 错误（含 DB 故障）都映射 404 | `internal/hub/api/api.go:1141-1144` |
| CR-11 | nit | P1-3 缺 `applySync` 级验证（现状为 store 层等价模拟） | `internal/agent/store_test.go:303-360` |
| CR-12 | nit | UNKNOWN/空值也下发 `ttl_remaining_sec=60`，语义上“不存在状态的剩余秒数”为 60 易误读 | `internal/hub/gateway.go:585-587,595-618` |

---

## 二、逐条详情

### CR-1（major）superseded 判定把“非成功部署”的 DONE 行也当成功部署 → 误跳过可恢复的回滚

- 位置：`internal/agent/application.go:808-816`（`supersededByLaterDoneDeployment` 的 `other.Phase != PhaseDone` 过滤），配合 `:293-299`（`prepareFailed` 写 `DONE`）与 `:418-419`（回滚成功也写 `DONE`）。
- 问题描述：函数注释与设计意图是“**已被更晚的成功部署取代** → 不重放”，但谓词只看 `phase='DONE'`。本仓库中 `DONE` 至少有两种非成功来源：
  1. `prepareFailed(...)`：准备阶段失败（artifact 下载失败、路径非法、备份目录创建失败等）也把行标成 `DONE`（`application.go:295`），**未做任何宿主替换**；
  2. `failed()` 中 rollback **成功**后同样写 `DONE`（`application.go:418-419`）——该次尝试最终回到了“该次尝试之前”的状态，不代表目标版本部署成功。
- 触发条件：同 app 存在活动行 A（半完成或 `ROLLBACK_FAILED`，绑定有效）+ 更晚的“非成功 DONE”行 B（Argo：A 回滚失败 → 操作者/Hub 重试 → 重试在准备阶段失败或回滚成功），重启恢复时 A 被判 superseded → **跳过重放且不落任何终态**。原实现（replay 每行）会按 §52“默认优先安全恢复 Previous”恢复 A 的前一版本；新实现让宿主停在半完成态（如服务已停、二进制半替换），直到下一次成功部署。
- 现有测试未覆盖该边界：`TestRecoverySkipsSupersededStaleDeployment` 的 newer 行是真实成功行；`TestRecoveryReplaysWhenLaterDeploymentNotDone` 覆盖的是“更晚行未 DONE”边界，没有“更晚行 DONE 但非成功”的用例。
- 建议改法（择一，推荐 a）：
  a. 收紧 superseder 谓词：要求更晚 DONE 行确实完成过替换（例如 `BackupPath != ""` 且经历过 STOPPED 之后才可能置空/置标记），或在 journal 增加结果列/独立终态（`PREP_FAILED`、`ROLLED_BACK`、`SUCCESS`）从根上区分；
  b. 若维持现状，必须补“更晚 DONE 但为准备失败”的边界测试，并在 §52 文档中写明“DONE 亦包含准备失败与回滚成功，视为取代”。
- 是否影响已确认范围：是（P0-2 核心保护逻辑的语义精度）。不阻塞回归启动；建议在重跑 AR-32 主链前决定先收紧或先补测。

### CR-2（major）新 phase `ROLLBACK_FAILED` 与 P1-4 新约束未同步 ARCHITECTURE §52 / PRODUCT §42

- 位置：`docs/ARCHITECTURE.md:1203-1224`（§52 Phase 枚举仍为 PREPARING…ROLLING_BACK，无 `ROLLBACK_FAILED`）；`docs/PRODUCT.md:1145-1180`（§41/§42 未提新 phase、未提恢复防护）；代码 `internal/agent/application.go:30-32`。
- 问题描述：方案文件明确要求“若采纳 C，`ARCHITECTURE.md` §52 / `PRODUCT.md` §42 的 Phase 枚举需增补 `ROLLBACK_FAILED`（文档变更，随方案一起评审）”且偏好“随方案一起评审”。实施报告仅在“与方案的偏差”中说明未新增 `SUPERSEDED`，未记录 §52/§42 未同步；本次全部源码 diff 中 `docs/` 零改动。仓库规则将 `PRODUCT.md`/`ARCHITECTURE.md` 列为强约束，新增 phase 与恢复语义（superseded/binding 双重防护）属于“行为变化/升级方式变化”，应同步文档。
- 触发条件：任何按 §52 phase 枚举审查 journal 的读者（后续回归、运维、文档读者）看到未登记的状态值；P1-4 的 256 KiB 上限与 `timeout=0 → 300` 语义同样无产品文档依据（pending-decisions A2/A3 仍待裁决，若采纳需一并落文档）。
- 建议改法：更新 ARCHITECTURE §52 phase 列表 + 在 §52/§53 补一句恢复防护（superseded/binding 不重放）；PRODUCT §42 同步；P1-4 按 A2/A3 决策把 timeout 语义与 content 上限写进脚本文档。
- 是否影响已确认范围：是（文档一致性，不影响运行时；交付/Archive 前必须闭环）。

### CR-3（minor）legacy/绑定失效/superseded/PREPARING 行无终态、无运维出口，永久悬挂

- 位置：`internal/agent/application.go:743-761`（PREPARING / binding mismatch 分支只记日志 continue，不写任何状态）、`:719-722`（superseded 同样不改行）；`internal/agent/store.go:755`（`GetActiveDeployments` 取 `phase NOT IN ('DONE')`，无清理）。
- 问题描述：三类行永久留在 active 集合：旧行缺绑定（升级前写入）、定义漂移导致绑定失效、被取代行。它们每次重启都重新告警，永不收敛；除“重新部署（生成新行，旧行仍在）”外没有任何 API/CLI 可清理或标记。对“定义被合法修改”（例如仅改描述/健康检查导致 revision 前进）这一常见情况，旧的 `ROLLBACK_FAILED` 行将永远无法重放——这是用户已确认的“未知→不重放”取舍，故不计缺陷，但需记录后果：**这是有意为之且已被测试固化（`TestRecoverySkipsBindingMismatch` 的 revision drift 子例）**，建议补一个显式终态（如 `SUPERSEDED`/`REQUIRES_INSPECTION`）或运维命令，并把这些行数暴露到启动日志摘要/状态上报，避免“静默悬挂”。§67.11（Managed Application 必须存在恢复路径）的自动化部分对这些行实际只依赖 Hub 重新部署。
- 触发条件：升级过 Agent 且升级时存在半完成行；或恢复前应用定义发生过任何 revision 变化。
- 建议改法：见上；至少把“需要人工检查”的行数/ID 汇总为一条启动日志。
- 是否影响已确认范围：否（已确认取舍的边界记录）。

### CR-4（minor）“三组合均可用”表述与升级顺序约束

- 位置：`implementation-report-20261016.md:31`（“新 Hub+旧 Agent / 旧 Hub+新 Agent / 新+新 三组合均可用”）、`internal/agent/sync_validation.go:35-53`。
- 问题描述：实现本身正确（见第四节），但“可用”不等于“已修复”：**旧 Hub×新 Agent、新 Hub×旧 Agent 两个单向混版本下，原时钟偏移缺陷（Hub 领先 → 恒 BLOCKED）依旧存在**，只是行为不比修复前更差。若回归环境只升级 Hub 或只升级 Agent，13 条 Remote 条件会复现 BLOCKED，容易被误判为“修复无效”。
- 建议改法：在实施报告/发布说明显式写明“仅 Hub 与 Agent 均升级后消除偏移误判；单向混版本行为与本修复前一致（不更差）”，并在回归前置检查两侧构建版本（或注明本环境为双端新版本）。
- 是否影响已确认范围：否（表述/交付说明）；回归批执行前需知悉。

### CR-5（minor）组名唯一性校验在事务外，竞态下仍回源 raw SQLite UNIQUE 文案；名称未存储归一化

- 位置：`internal/hub/node.go:302`（`validateGroupName` 在 `runMutationTx` 之前）、`:324-338`（比对用 TrimSpace/EqualFold，但不清洗存值）；`internal/hub/api/api.go:1098-1111`（POST 错误一律 `writeErr(400, err.Error())`）。
- 问题描述：两个并发同名 POST/PUT 可同时通过校验，随后第二个请求的 INSERT/UPDATE 撞 `node_groups.name UNIQUE`，返回 400 + `UNIQUE constraint failed: node_groups.name` 原文——与缺陷本体（“未归一化 SQLite UNIQUE 错误”）同类，只是概率面从“常态”缩到“竞态”。另外 `" foo "` 会被原样存储（仅比较时 trim），与“忽略首尾空白”的语义不一致。
- 建议改法：把唯一性检查移入 `runMutationTx`（或在 store 层捕获 UNIQUE 并归一化文案）；create/update 存储前 `strings.TrimSpace`。
- 是否影响已确认范围：否（已确认修复的加固项）。

### CR-6（minor）P2-6 非 `ErrNoRows` 的存储错误仍回 `err.Error()` 原文

- 位置：`internal/hub/api/api.go:305-311`（`writeUpdateError` 的 400 回退分支）。
- 问题描述：已观察到的 `sql: no rows in result set` 外泄路径全部封闭（4 端点 × `errors.Is` 映射 404，测试覆盖），但其他存储错误（如 `database is locked`、约束失败、后续 manager 包装错误）仍会原文回给调用方。实施报告已作为偏差声明（A5），此处仅登记残余风险：一旦 manager 改用 `%w` 包装或新增约束，外泄面会重新出现而测试未必拦住。
- 触发条件：PUT 更新触发非 `ErrNoRows` 的存储错误。
- 建议改法：下一批按方案把管理器层错误分类（`store.ErrNotFound` / `store.ErrConflict`），handler 只回归一化文案；或在 manager 层保证不回传驱动错误原文。
- 是否影响已确认范围：否（已披露残余项）。

### CR-7（minor）P3-7 方法收紧后 E2E 证据/台账未同步

- 位置：`web/e2e/tests/gap-ui.spec.ts:641-649`（探针仍记录 `method_contract: 'current /api/audit handler reads for every method; HTTP200/read-array is not an audit mutation'`）。
- 问题描述：该用例对 `/api/audit` 发 PUT/DELETE 只记录状态码、不断言 200，因此不会硬失败；但证据行文案与 API-CONTRACT 的期望值在修复后已过期（现在应为 405 + `Allow: GET`）。方案附注 2 已提示“测试台账联动”，实施报告未列此项。
- 触发条件：重跑 GAP-17 / API-CONTRACT 时。
- 建议改法：更新探针证据描述与 `.e2e` 用例期望（非产品代码）。
- 是否影响已确认范围：否（E2E artifact）。

### CR-8..CR-12（nit）

- **CR-8**：`application.go:415-417` 的 `if err := ...; journalErr == nil { journalErr = err }` 只有在 `journalErr` 为 nil 时至多保留一个错误；当 ROLLING_BACK 写入与 ROLLBACK_FAILED 写入都失败时，第二个错误被静默丢弃。建议独立记录两条日志。
- **CR-9**：`application.go:819-828` 当只有一侧时间解析失败时回退字典序比较，方向可能错误（如 `a` 合法、`b` 为异常格式）。当前所有写入方都写 RFC3339Nano UTC，实际不可达；建议单侧失败即返回保守结果（不 supersede 或按需记日志）。
- **CR-10**：`api.go:1141-1144` PUT 预检把任何 `GetGroup` 错误都映射 404 “group not found”，DB 故障时误导。建议区分 `sql.ErrNoRows` 与其他错误。
- **CR-11**：P1-3 的 store 层测试精确复刻了“Commit 失败→RollbackSync no-op→锁已释放→可再次同步”的序列，但缺 `applySync` 级验证（真实 commit 失败 → SyncAck(false) → 下一次同步成功、revision 不前进）。建议后续补一条 DB 故障注入的 agent 级测试。
- **CR-12**：`gateway.go:585-587,595-618` 对 `UNKNOWN`/空值也下发 `ttl_remaining_sec=60`。当前 Agent 先判 value，安全；但新字段语义是“剩余有效秒数”，对“不存在的状态”给 60 会被未来消费者误读。建议 UNKNOWN 分支返回 0（或省略），并在字段注释中写明。

---

## 三、按评审重点的核查结论（含“为何可信”）

1. **P0-1 混版本双向**：**可信**。① 新 Hub 报文对旧 Agent 无影响——`internal/agent/connection/manager.go:286` 与 `agent.go:1730` 均使用 `json.Unmarshal`（非 `DisallowUnknownFields`），未知字段被忽略；旧字段 `observed_at`/`ttl_sec` 语义与取值未变（缓存分支窗口 `expires-observed` 与 HEAD 完全一致，实时 60）。② 旧 Hub×新 Agent 回退路径与旧实现逐条等价：新增的 `p.TTL <= 0 → false` 在旧逻辑下也不可达 true（`observed.After(now)` 先挡未来时间，TTL=0 时 `age < 0` 必假）；负 age 唯一来源是 observed 在未来，两版都拒绝。③ `nil` 指针精确区分“未下发”与“≤0”，`ceil` + `remaining<1→1` 防御在 Hub 单侧时钟内计算，`now` 与 `expires` 同源（`SetRemoteState` 由 Hub 写入），无跨主机比较；`sync_validation_test.go` 14 例 + `remote_state_test.go` 覆盖 ceil/过期回落/时钟偏移用例。残留：单向混版本缺陷仍在（CR-4）。
2. **P0-2 防护逻辑**：主链**可信**（`TestRecoverySkipsSupersededStaleDeployment` 复刻 AR-32 主链：文件/服务/phase 全不动；`TestRecoveryReplaysMatchingIncompleteDeployment` 保住 SYS-51 类恢复路径）。解析时间比较在“双方均可解析”时可靠（同源 UTC RFC3339Nano，`time.Parse` 后比较），时区无关；并发面已核实：`RecoverDeployments` 在 `conn.Run` 之前调用（`agent.go:169`），恢复期间无并发部署写入。问题见 CR-1（谓词过宽）、CR-3（悬挂无终态）。`ROLLBACK_FAILED` 的读取方已全仓核对：仅 `GetActiveDeployments`（`NOT IN ('DONE')` 自然包含）与 `RecoverDeployments` 消费 phase；Hub 侧、UI、审计、心跳均不消费 Agent 本地 journal（`ListAllDeployments` 无调用方），无“既有读取方误处理”问题。
3. **P1-3**：**可信**。调用方仅 `agent.go:800`（defer）与 `:917`，无调用方依赖旧行为（旧行为是 panic）；`BeginSync` 失败自解锁、`CommitSync/RollbackSync` 仅在持有 tx 时解锁，语义为“锁只释放一次”；测试覆盖 Commit 失败后 RollbackSync no-op、无 Begin 调用幂等、失败后可重新同步。缺口见 CR-11。
4. **P1-4**：**可信**。`len(s.Content)` 即 UTF-8 字节数，与 TEXT 存储、Hub WS 读限（`gateway.go:107` 8MB）一致，多字节边界有测试；`timeout==0 → 300` 与 Task 侧（`definitions.go` 既有语义）、DB 默认（`migrate.go` `DEFAULT 300`）、前端 `min=0` 一致；Create/Update 均在 manager 顶部接入，失败不落库不推 revision（测试断言）；`script.timeout` 不参与执行超时的现状未扩散（与方案一致）。文档缺口见 CR-2。
5. **P2-5**：**可信**。三层防御（handler 预检 404 → manager 归一化校验 → store `RowsAffected()==0 → sql.ErrNoRows` 前置在成员 DELETE/INSERT 之前）确实杜绝了孤儿成员与无效 revision 推进；`UpdateGroup` 返回的 `sql.ErrNoRows` 未被 `WithTx`/manager 包装（`transaction.go:13-18` 直接透传），`errors.Is` 判定成立；契约测试与 manager 测试覆盖空名/重名/不存在 id/正常路径。竞态缺口见 CR-5。
6. **P2-6**：**可信**（范围内）。4 个 manager 的存在性检查均直接 `return err`（`sqlite_definitions.go:86-97,245-257,319-331`、`sqlite_artifacts_apps.go:141+` 的 `row.Scan` 错误直返），`errors.Is(err, sql.ErrNoRows)` 可穿透；存在 id → 200、校验错误 → 400 文案保留均有测试；DELETE/GET 未触碰。残余见 CR-6。
7. **P3-7**：**可信**。405 + `Allow` 头齐备；CORS `OPTIONS` 由 `withCORS`（`api.go:211-214`）在 handler 之前拦截为 204，不受影响；全仓调用方核对：前端 audit/metr/me 全为 GET（`web/src/features/catalog/index.tsx:1864` 等），logout 全部调用方（web e2e fixtures/safe-ui/gap-ui）均为 POST，无破坏。配套测试断言 405/Allow/OPTIONS/GET 保留 `X-Total-Count`/logout 语义。缺口见 CR-7。

## 四、对“是否可进入回归验收”的结论

**可以进入回归验收（0 blocker）**，附三点说明：

1. 独立复跑验证全部通过：`go build ./...`、`go vet`（agent/hub/store）、`gofmt -l`（本次 13 个文件无输出）、`go test ./internal/agent/... ./internal/hub/... ./internal/store/... -count=1` 全 ok。
2. CR-1（superseder 过宽）与 CR-2（文档未同步）不阻塞回归启动：前者仅在“更晚 DONE 行非成功部署”边界触发，不影响 AR-32 主链重跑结论；后者为文档交付项。但 **CR-1 建议在重跑 AR-32 之前决定**（先收紧谓词再回归可一次覆盖；若维持现状，须补边界测试并在报告/文档明确 DONE 语义）。**CR-2 必须在最终交付/Archive 前闭环**。
3. 本次评审为静态 + 单测层结论，方案所需的运行时验证（真实 Hub-Agent 混版本重启、AR-32 环境复现）仍按计划在回归批执行，不能以本评审替代。
