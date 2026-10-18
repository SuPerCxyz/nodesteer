# NodeSteer 文档同步报告（CR-2：7 条缺陷修复的产品语义同步）

- 日期：2026-10-16
- 范围：仅 `docs/ARCHITECTURE.md`、`docs/PRODUCT.md`；本次为纯文档同步
- 依据：`repair-plan-20261016.md`、`implementation-report-20261016.md`，以及源码逐处回核（下述每条给出 `文件:行`）
- 未执行：未修改产品源码/配置/测试/.e2e 既有状态文件；未做 Git 操作；未运行文档构建（仓库无文档构建入口，仅做实读与 diff 校验）

---

## 一、改动清单（逐条）

### 1. `docs/ARCHITECTURE.md` §40 Remote State（现 :949-958）

- 改前要点：只有 Hub 保存字段（node_id/property/value/observed_at/expires_at）与"Agent Offline 时只能使用仍在 TTL 内的 Cache"、"过期 UNKNOWN → BLOCKED"；无下发字段清单，无剩余 TTL 概念。
- 改后要点：新增"Remote State 下发字段"清单，明确 `observed_at` 为诊断字段、`ttl_sec` 为观测窗口长度、`ttl_remaining_sec` 为自 Hub 判定时刻起的剩余有效秒数（向上取整）；说明 Agent 优先用 Hub 计算的剩余 TTL 判定新鲜度、不做跨主机时钟比较，旧 Hub 未下发时回退 `observed_at + ttl_sec`（混版本行为与修复前一致），仅双端均升级后消除跨主机时钟偏移误判（对应 CR-4 的交付说明要求）。
- 源码依据：`internal/protocol/protocol.go:330-341`、`internal/hub/gateway.go:592-606`、`internal/agent/sync_validation.go:31-52`。

### 2. `docs/ARCHITECTURE.md` §52 Deployment Journal（现 :1224-1227 字段、:1231 绑定说明、:1241 Phase、:1243 语义、:1247-1254 防护）

- 改前要点：保存字段无部署绑定列；Phase 枚举止于 ROLLING_BACK；恢复语义只有一句无条件的"一期默认优先安全恢复 Previous"。
- 改后要点：
  - 字段补 `app_revision` / `binary_path` / `config_path` / `unit_name`，并注明为"部署上下文绑定，在产生宿主副作用前写入"；
  - Phase 补 `ROLLBACK_FAILED`，注明其与 `ROLLING_BACK`（含回滚中途崩溃）的区别，二者重启恢复均须经防护校验后决定是否重放；
  - 新增双重防护：① Superseded（同 application 存在 `started_at` 更晚且 `phase=DONE` 的部署；按 CR-1 要求注明当前 DONE 亦包含准备阶段失败、回滚成功等终止结果，均按取代处理）；② Binding（Journal 记录的 revision 与三路径与恢复时当前定义一致，记录缺失或任一不一致视为未知）；任一命中不改动任何文件、保留 Journal 待人工检查；
  - 补"通过校验后以 Journal 记录的路径重放"；原恢复句限定为"无法确认归属的记录不得覆盖更晚的 DONE 部署"。
- 源码依据：`internal/agent/application.go:29-32`（PhaseRollbackFailed）、`:351-360`（绑定早于副作用写入）、`:408-425`（回滚失败落 ROLLBACK_FAILED）、`:700-828`（RecoverDeployments 双重防护与 journal 路径重放）；`internal/agent/store.go:695-738`（绑定字段与读写）、`:753-755`（`phase NOT IN ('DONE')` 为活动行）。

### 3. `docs/ARCHITECTURE.md` §62 故障恢复场景 - Application Upgrade Crash（现 :1561）

- 改前要点：`依赖 Deployment Journal + Previous Backup 恢复。`
- 改后要点：补"重放前须通过 superseded 与 binding 校验，陈旧 Backup 不得覆盖更晚的 DONE 部署"。
- 源码依据：同第 2 条。

### 4. `docs/PRODUCT.md` §20 Script Library（现 :617-620）

- 改前要点：字段列表止于 SHA256，无 timeout/content 边界表述。
- 改后要点：新增"定义校验（Create/Update）"：`timeout < 0` → 400；`timeout == 0` 归一为默认 300s（与 Task、DB 默认值一致）；`content` 上限 256 KiB（262144 字节，按字节数判定，超限 → 400）；`script.timeout` 暂不参与执行超时，执行超时以下发 Task 的 `timeout` 为准。
- 源码依据：`internal/hub/validation.go:202-220`、`internal/hub/definitions.go:38`（Create 顶部接入）、`:76`（Update 顶部接入）、`internal/hub/api/api.go:1197-1200`（Create 校验错误 400）。
- 说明：评审所提"Settings/脚本相关章节"在本仓库中即脚本章节；PRODUCT.md 无独立 Settings 语义章节（见"无法判定/待裁决"第 1 条）。

### 5. `docs/PRODUCT.md` §28 Remote Node Condition（现 :804-807）

- 改前要点：Remote State 必须包含 Value/Observed At/TTL；过期 UNKNOWN → BLOCKED。
- 改后要点：补"新 Hub 随 Remote State 下发自判定时刻起的剩余有效秒数（`ttl_remaining_sec`）；Agent 优先据此判定新鲜度，不做跨主机时钟比较；旧 Hub 未下发时回退 Observed At + TTL；仅 Hub 与 Agent 均升级后消除跨主机时钟偏移误判；Observed At 保留为诊断字段"。原"必须包含"列表与"过期 → BLOCKED"未改动（fail-closed 语义保持）。
- 源码依据：同第 1 条。

### 6. `docs/PRODUCT.md` §42 Deployment Journal（现 :1187-1190 字段、:1201 Phase、:1203 区分说明、:1207-1211 防护）

- 改前要点：字段示例无绑定列；Phase 枚举无 ROLLBACK_FAILED；恢复要求仅为"必须识别半完成部署并恢复到明确状态"。
- 改后要点：字段示例补 4 个绑定列；Phase 补 `ROLLBACK_FAILED` 并说明与 `ROLLING_BACK` 的区别；新增双重防护（Superseded + Binding，Superseded 注明 DONE 含准备失败/回滚成功等终止结果）与"不得用陈旧 backup 覆盖更晚的 DONE 部署"。
- 源码依据：同第 2 条。

### 7. `docs/PRODUCT.md` §46 REST API（现 :1355-1362）

- 改前要点：仅端点清单 + "Web UI 调用真实 API"。
- 改后要点：新增两组契约：
  - 方法语义：`/api/audit`、`/api/me`、`/metrics` 仅接受 GET（其它方法 → 405 + `Allow: GET`）；`/api/logout` 仅接受 POST（其它方法 → 405 + `Allow: POST`；GET 不再注销）。
  - 状态码契约：`PUT /api/schedules|applications|scripts|tasks/{id}` id 不存在（请求体合法）→ 404，错误体不回传底层存储错误原文，字段校验失败仍为 400；`POST /api/groups` name 缺失或空白 → 400 且组名忽略大小写与首尾空白判重；`PUT /api/groups/{id}` id 不存在 → 404（不做 upsert）。
- 源码依据：`internal/hub/api/api.go:303-311`（writeUpdateError）、`:447-473`（logout/me 方法语义）、`:1098-1101`、`:1135-1152`（groups POST/PUT）、`:1255`、`:1344`、`:1489`、`:1754`（四个 PUT 的 404 映射）、`:1895-1900`（metrics）、`:1909-1914`（audit）；`internal/hub/node.go:323-339`（组名判重）。

---

## 二、与新语义冲突的旧表述改正（关键）

以下**既有句子**因与新恢复/契约语义不完整而被直接改写或追加限定（均未删除原有意图）：

| # | 文件/章节 | 改前要点 | 改后要点 | 原因 |
|---|-----------|----------|----------|------|
| 1 | `ARCHITECTURE.md` §52 | "一期默认优先安全恢复 Previous。"（无条件重放） | 保留原句并限定："无法确认归属的记录不得覆盖更晚的 DONE 部署，需人工检查。" | 旧表述会让读者认为任何半完成部署都无条件恢复，与新 superseded/binding 防护直接矛盾 |
| 2 | `ARCHITECTURE.md` §52（Phase 语义） | Phase 枚举止于 ROLLING_BACK，无失败终态 | 增加 `ROLLBACK_FAILED` 及两 phase 区分与恢复校验说明 | 缺 `ROLLBACK_FAILED` 使文档与实现（application.go:32）不符 |
| 3 | `ARCHITECTURE.md` §62 | 崩溃恢复"依赖 Journal + Backup"（无前置校验） | 补"重放前须通过 superseded 与 binding 校验，陈旧 Backup 不得覆盖更晚的 DONE 部署" | 恢复场景描述不完整会误导运维预期 |
| 4 | `PRODUCT.md` §42 | "Agent Crash/Restart 后必须识别半完成部署并恢复到明确状态。"（原句保留） | 追加防护段："任一命中即不改动文件、保留 Journal 待人工检查；不得用陈旧 backup 覆盖更晚的 DONE 部署。" | 原句未说明"恢复什么情况下允许/禁止"，与新语义不完整 |
| 5 | `PRODUCT.md` §42（Phase） | 枚举无 ROLLBACK_FAILED | 增加并注明区分 | 同第 2 条 |

经全 `docs/` 检索核对，**未发现其他直接冲突的旧表述**：

- `ARCHITECTURE.md` §40 / `PRODUCT.md` §28 的 TTL 过期语义与新字段为增量关系（`ttl_sec` 语义未变），不冲突；
- 文档中不存在 groups PUT upsert、任意方法 200、脚本无上限、负数 timeout 可接受等旧断言（`ARCHITECTURE.md:840` 的 "Hub idempotent upsert" 属 Execution Journal 上传语义，与 groups PUT 无关）；
- `docs/TEST_*.md`、`docs/UI_FULL_TEST_*.md` 中涉及 phase 的记述为历史测试证据（如 `TEST_ENV_SIM.md:109` 的 SYS-10 evidence），非规范性契约，按"不改历史报告"原则未动。

---

## 三、未改动但已核对无冲突的章节（说明）

- `ARCHITECTURE.md` §41 Script 架构：脚本校验属产品约束，已写入 `PRODUCT.md` §20；§41 的分层流程未受影响。
- `PRODUCT.md` §41 Basic Rollback、`ARCHITECTURE.md` §53 Rollback：回滚步骤流程不变；失败终态与恢复防护归入 Journal 章节（§42/§52）。
- `PRODUCT.md` §43 Web UI、`ARCHITECTURE.md` §29 Agent SQLite：文章未枚举 deployment 表列/phase，不产生冲突；未做超范围补充。

---

## 四、无法判定/待裁决项

1. **"Settings 相关章节"无落点**：`PRODUCT.md` 中 Settings 仅出现于 §43 Web UI 清单与 §46 `/api/settings` 端点清单；本轮 7 条修复未变更 `/api/settings` 行为，故无对应同步内容。若要求为 Settings 建立语义章节，属新范围。
2. **`script.timeout` 未来是否接入执行超时**：方案 P1-4 风险项建议另立 change；本报告仅按现状标注"暂不参与执行超时"，未写未来计划。
3. **同缺陷配套语义已一并补充**：CR-2 摘要未列出的"组名忽略大小写/空白判重"（`internal/hub/node.go:323-339`）已写入 §46，因其为缺陷 5 的可见行为；如认为超范围可删。
4. **superseded 边界与 CR-1 依赖**：当前实现的 superseded 谓词是"更晚的任意 `phase=DONE`"（DONE 含准备阶段失败与回滚成功，均视为取代），文档已按 CR-1 要求如实注明；若 CR-1 决议收窄谓词（仅"成功完成替换"的 DONE），需同步修改 `ARCHITECTURE.md` §52 与 `PRODUCT.md` §42 的该括号说明。此外，"更晚但未 DONE 不构成 superseded（不阻断重放）"与实现一致，是否需在产品层显式记录该限制请裁决。
5. **CR-4 混合版本表述**：已按 CR-4 建议在 `ARCHITECTURE.md` §40 与 `PRODUCT.md` §28 写明"仅 Hub 与 Agent 均升级后消除跨主机时钟偏移误判"，避免单向混版本被误读为已修复。
6. 未运行 Markdown 渲染/链接检查（仓库无该工具链）；仅做全文 diff 与逐节回读校验。

---

## 五、验证记录

- `git diff -- docs/` 复核：仅 `docs/ARCHITECTURE.md`（+28/-2）与 `docs/PRODUCT.md`（+31/-0）改动，无其他文件被本次任务触碰（工作树中其余源码/`.e2e` 改动为本次修复批次既有改动，未回退、未覆盖）。
- `git status --short`：新增本报告文件（`.e2e/.../doc-sync-report-20261016.md`）；`.e2e` 既有状态文件均未修改。
- 每条语义均已对照源码 `文件:行` 逐处回核（见第一节依据）。
