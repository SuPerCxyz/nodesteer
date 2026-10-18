# 文档同步报告 D1：3 项已裁决行为语义（2026-10-17）

- 任务：把 3 项已裁决的行为语义同步进 `docs/`（审计粒度、REMOTE_STATE 保留分支、手工置在线节点）
- 依据：
  - 裁决记录：`root-decisions-20261017.md`（A 表 SYS-40 行；B.3「代码级验证后定」两条：REMOTE-TTL-PATH-UNREACHABLE、OFFLINE-NODE-MANUAL-ONLINE-NO-SWEEP）
  - 判定证据：`qa-resume-20261017-tail-verify/case-result.json`（第 8 条手工置在线、第 9 条 REMOTE_TTL_PATH_UNREACHABLE）+ `qa-resume-20261017-partial-rest/run_sys40.py`（SYS-40 retry=2 取证脚本，产物 `sys40-retry-audit.json`）
  - 源码逐处回核：`internal/hub/api/api.go:1435/1445`、`internal/hub/execution.go:22/260-276`、`internal/hub/gateway.go:562-627`、`internal/agent/agent.go:1690-1725/1728`、`internal/agent/connection/manager.go:357`、`internal/agent/sync_validation.go:30-42`、`internal/hub/session.go:88-118`、`internal/hubserver/hub.go:297-317`、`internal/hub/node.go:206-229`、`internal/protocol/protocol.go:34-35/336-350`
- 边界：只改 `docs/PRODUCT.md`、`docs/ARCHITECTURE.md`、`docs/TEST_PLAN.md` + 本报告；未改源码/配置/`web/**`；未触碰 `.e2e/**` 既有状态文件；无 Git 操作
- 说明：`internal/hub/api/execution.go` 不存在，相关代码实际在 `internal/hub/execution.go` 与 `internal/hub/api/api.go`；`internal/agent/manager.go` 不存在，Remote State 接收路径实际在 `internal/agent/connection/manager.go:357`

---

## 一、逐条改动

### 1. 审计粒度 = 逻辑触发（SYS-40 裁决：接受为设计）

- 文件/位置：`docs/PRODUCT.md` §45 Audit（改后 :1342，在审计动作枚举之后追加一段）
- 改前要点：§45 只有审计动作枚举（Login、Script、Task Create/Modify/Delete/Execute、Node Enable/Disable 等），无审计粒度语义；读者可能按执行记录理解"每次执行/每次重试各一条审计"。
- 改后要点：新增一段——**审计记录以逻辑触发为单位**：一次 Manual Run 只产生一条 `execute` 审计记录（`resource=task`），与执行内部按 Retry 重试的次数无关；每次 attempt 的细节只体现在执行双流日志（stdout/stderr）中，不产生额外审计行。
- 依据：`run_sys40.py` 实机取证（retry=2 → 日志 marker×3、`execute_rows_count=1`、`rows_for_execution_id=[]`、`per_attempt_audit_rows=0`）；`internal/hub/api/api.go:1435/1445`（`execute` 审计仅在 Manual Run 入口写一条，resource=task）；`internal/agent/agent.go:1076-1127`（Retry 循环在 Agent 侧产生多次 attempt，审计不介入）。
- 决策要求"台账注释"未在本任务范围（落 `.e2e/**`，禁改），见第四节第 1 条。

### 2. `REMOTE_STATE` 为不可达保留代码（裁决：保留 + 文档化）

- 文件/位置：`docs/ARCHITECTURE.md` §40 Remote State（改后 :959 一行限定补充；:967-969 追加两段）
- 改前要点：§40 把"Hub 保存最新（node_id/property/value/observed_at/expires_at）"作为活跃行为描述，且"Agent Offline 时只能使用仍在 TTL 内的 Cache"隐含 Agent 侧存在缓存；无任何"保留分支/非活跃路径"说明。
- 改后要点：
  - `:967` 新增"协议现状（保留分支）"：`REMOTE_STATE` 目前仅作为 Hub 对 Agent `REMOTE_STATE_REQ` 的应答类型；Agent 只发送查询请求、不主动上报 `REMOTE_STATE`；因此上方「Hub 保存最新」的写入路径与缓存命中/过期分支为**协议对称性与未来扩展保留，当前不是活跃路径**。
  - `:969` 新增：Remote State 实际由 Hub 在收到请求时**实时解析**（`online` → 节点当前状态；`last_execution` → 最近一次执行结果），有效性判定以 Hub 在应答请求时计算的 `ttl_remaining_sec` 为准。
- 依据：全仓检索——`REMOTE_STATE` 唯一发送点是 Hub 自身应答 `internal/hub/gateway.go:581`；Agent 只发送 `REMOTE_STATE_REQ`（`internal/agent/agent.go:1704`）、只接收应答（`internal/agent/connection/manager.go:357` → `OnRemoteState`）；`SetRemoteState` 唯一生产调用点 `gateway.go:571`（入站 `REMOTE_STATE` 触发），故 `gateway.go:598-608` 缓存分支不可达；`remote_state` 表在生产路径不被填充（`internal/store/sqlite_executions.go:187-199`）。
- 措辞说明：按实际机制写为"以 Hub 在应答请求时计算的 `ttl_remaining_sec` 为准"（与既有"不做跨主机时钟比较"一致）；`RemoteStateReqPayload`（`protocol.go:346-350`）不携带 TTL，TTL 由 Hub 应答时计算，故未写成"请求携带的 TTL"。

### 3. 手工置在线的节点语义（裁决：文档化，非缺陷）

- 文件/位置：`docs/PRODUCT.md` §17 Node 管理（改后 :539-541，在"一期支持"列表之后追加两段）
- 改前要点：§17 仅列出 Online/Offline、Maintenance、Disabled 等能力与节点页字段，无"人工设置状态"的语义说明；读者可能误以为 online 会由心跳自动维护。
- 改后要点：
  - `:539`：节点状态支持人工设置——管理员可通过页面「设为在线」或 `PUT /api/nodes/{id}` 把 offline 节点手工置为 online；手工置为 online 的节点**在无 Agent 会话时不会被自动清扫**（心跳检查只作用于已注册会话，仅 Hub 启动时一次性复位无会话的孤儿 online），会保持 online，直到 Hub 重启或再次人工修改。
  - `:541`：向此类节点派发**不会静默挂起**：派发明确返回 `400 agent offline`，执行记录落 `FAILED` 并携带 block_reason。
- 依据：`case-result.json` 第 8 条（隔离实例实测 PUT→200、观察窗 t+20/40/70s 持续 online；派发 HTTP 400 `agent offline`、Execution FAILED + block_reason）；`internal/hub/session.go:88-118`（心跳检查器只遍历已注册会话）；`internal/hubserver/hub.go:297-317`（启动时一次性复位孤儿 online）；`internal/hub/execution.go:22/260-276`（`ErrAgentOffline`，下发失败标 FAILED 并写 block_reason）；`internal/hub/api/api.go:1440-1443`（RunManual 错误 → 400）。
- 位置选择：`docs/ARCHITECTURE.md` 无独立"节点状态机"章节（§16 为 Gateway/Session 协议），按现有组织选 `docs/PRODUCT.md` §17 节点状态章节；与 §22「Agent Offline 时 Manual Run 明确返回不可执行」同向。

---

## 二、冲突旧表述改正（逐条）

| # | 文件/位置 | 改前要点 | 改后要点 | 原因 |
|---|-----------|----------|----------|------|
| 1 | `docs/ARCHITECTURE.md` §40（:959） | "Agent Offline 时只能使用仍在 TTL 内的 Cache。"（隐含 Agent 侧存在 Remote State 缓存） | 保留原句并追加限定："；当前协议下 Agent 不维护独立 Cache，离线或应答不可用时条件不可评估，按 Fail Closed 处理。" | 旧句是 REQ/ACK 改版前的设计遗留：Agent 侧无独立 Remote State 缓存（`agent.go:1690-1691` 未连接直接返回不可评估；`sync_validation.go` 只做应答新鲜度判定）；不限定会与新增"保留分支/非活跃路径"说明相邻矛盾 |
| 2 | `docs/TEST_PLAN.md` SYS-29（:558） | "Remote State TTL 未过期、过期、未观察：UNKNOWN → BLOCKED。" | "Remote State 未过期、未观察：UNKNOWN → BLOCKED（TTL 过期维度不可构造：当前协议无 Agent 主动上报路径）。" | 裁决明确要求"在测试计划中移除 SYS-29 的 TTL 过期维度"；该维度实测不可构造（Hub 应答恒为实时分支，剩余 TTL 恒 > 0） |
| 3 | `docs/TEST_PLAN.md` E2E-10（:460） | "online/last_execution、TTL 过期 Fail Closed" | "online/last_execution、未观察 Fail Closed" | 与 SYS-29 同一不可构造维度（裁决点名 SYS-29；E2E-10 为同类残留，按"一并改正"处理）。**此项为判断性扩展**，如要求严格按点名范围可单行回退（见第四节第 2 条） |

---

## 三、核查过但未改动（无冲突，说明）

| 位置 | 原文要点 | 判定 |
|---|---|---|
| `docs/PRODUCT.md` §22（:697） | "一期 Agent Offline 时 Manual Run 不进入长期等待队列，明确返回不可执行。" | 与手工置在线派发语义同向一致（本次仅补充 400/FAILED 细节），不改 |
| `docs/PRODUCT.md` §28（:799-808）/ `docs/ARCHITECTURE.md` §40 判定句 | `ttl_remaining_sec` 为新鲜度判定唯一依据、缺失/<=0 视为未知 | 与新语义一致（新增句沿用同一口径），不改 |
| `docs/PRODUCT.md` §51 不变量 10（:1503）、`docs/ARCHITECTURE.md` §67 不变量 9（:1744） | "Remote State 未知/过期默认 Fail Closed" | 策略性不变量仍成立（"过期"为 Fail Closed 方向约定，非可测路径声明），不改 |
| `docs/TEST_PLAN.md` SYS-40（:572） | "Retry 次数、间隔、最终状态、每次日志和审计。" | 未改：裁决对 SYS-40 仅要求 PRODUCT.md 审计章节 + 台账注释；"每次日志和审计"若被读作要求 attempt 级审计存在歧义（见第四节第 3 条） |
| `docs/TEST_PLAN.md` P19（:377-383）、E2E-01（:451） | 泛述"执行、取消、部署…审计"/"审计完整" | 无粒度断言，不改 |
| `docs/UI_FULL_TEST_PLAN.md`、`docs/UI_FULL_TEST_REPORT_*.md`、`docs/TEST_ENV*.md`、`docs/REAL_TEST_REPORT.md` | 历史测试计划/报告 | 按"不改历史报告"原则未动（含 TEST_ENV_SIM.md:272 的旧 bug 记述，属历史证据） |
| `docs/PRODUCT.md` §17 status 列表、§43 Web UI 节点区 | 无与"手工置在线"冲突的旧表述 | 全 docs 检索「设为在线/手工/手动/人工/置为 online」：除本次新增外无冲突命中 |
| `docs/IMPLEMENTATION_PROMPT.md:193` | "Remote State Unknown/Stale 默认 Fail Closed" | 与不变量同向，不改 |

---

## 四、未执行 / 待裁决 / 边界

1. **SYS-40 台账注释未做**：裁决要求"`docs/PRODUCT.md` 审计章节 + 台账注释"；台账在 `.e2e/**`（本任务禁改）。核查 `.e2e/observations.json`：Remote TTL（:521）与手工在线（:466）两条已有 `root_decision_note`，但全文无 SYS-40 / per-attempt 条目（15 条 note 均不涉及），**SYS-40 台账 note 缺失**，建议 Root 补录。
2. **E2E-10 改动属判断性范围外延**：裁决点名的是 SYS-29；我按"同一不可构造维度、测试计划内一致性"一并改正并在此单列。若 Root 要求严格按点名范围，仅需回退 `docs/TEST_PLAN.md:460` 一行。
3. **TEST_PLAN SYS-40 措辞歧义待裁决**：现文"每次日志和审计"可被读作要求 attempt 级审计（与已裁决"逻辑触发粒度"冲突），也可读作"每次（retry 的）日志 + 审计（1 条）"。裁决未点名改此处，未擅自改动；如需要，建议改为"每次 attempt 双流日志 + 1 条 execute 审计"。
4. **`docs/PRODUCT.md` §28 未加"保留分支"说明**：决策点名位置为 ARCHITECTURE.md；§28 描述的是应答字段（Value/Observed At/TTL/TTL Remaining Sec），当前仍成立，未动。
5. 未运行 Markdown lint / 文档构建（仓库无对应入口）；仅做逐节回读与 `git diff` 校验。

---

## 五、验证记录

- `git diff --stat -- docs/`（含上一批次 CR-2/CR-3 既有未提交改动）：`docs/ARCHITECTURE.md` +41/-3、`docs/PRODUCT.md` +44/-0、`docs/TEST_PLAN.md` +2/-2；本次 D1 新增为其中 §40（1 行限定 + 2 段）、§17（2 段）、§45（1 段）、TEST_PLAN 2 行，其余为既有改动，未触碰。
- 本次新增内容行号（改后实读）：`docs/PRODUCT.md:539-541`（§17）、`:1342`（§45）；`docs/ARCHITECTURE.md:959`（§40 限定）、`:967-969`（§40 新增两段）；`docs/TEST_PLAN.md:460`（E2E-10）、`:558`（SYS-29）。
- `git status --short -- docs/`：仅三个 docs 文件被修改；工作树中源码/`web/**`/`.e2e/**` 的既有未提交改动保持原样，未回退、未覆盖。
- 每条语义均已对照源码 `文件:行` 与判定证据逐处回核（见第一节"依据"）。
