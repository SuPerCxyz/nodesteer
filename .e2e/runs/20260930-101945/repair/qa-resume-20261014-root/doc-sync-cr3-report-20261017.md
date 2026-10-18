# CR-3 文档同步报告：新增 4 个 `completed_kind` 枚举值（2026-10-17）

- 任务：把 CR-3（不可重放部署行落终态）新增的 4 个 `completed_kind` 枚举值同步进 `docs/`
- 依据：`cr3-report-20261017.md`（方案 A、四类行→终态成因映射、对外行为变化）+ 实现 `internal/agent/application.go`（`CompletedKind*` 常量 `:41-51`、`closeUnreplayable` `:856-866`、`RecoverDeployments` `:751-854`、`hasDeploymentBinding` `:902-905`）
- 边界：只改 `docs/`；未改源码、配置、`web/**`；未改 `.e2e/**` 既有状态文件（本报告为新增交付物）；无 Git 操作

---

## 一、逐条改动

### 1. `docs/ARCHITECTURE.md` §52 Deployment Journal — `completed_kind` 枚举句（`docs/ARCHITECTURE.md:1234`）

- 改前：`completed_kind` 记录 `DONE` 的终态成因：`deployed`（成功部署）、`rolled_back`（回滚成功后收尾，含恢复重放成功）、`prepare_failed`（准备阶段失败）。旧记录缺该字段视为未知。
- 改后：在原枚举后按同样风格追加 4 个值——`superseded`（已被同应用更晚的成功部署取代）、`stale_binding`（Journal 绑定与当前应用定义不一致，备份归属未知）、`legacy_unbound`（旧行缺绑定字段，备份归属未知）、`prepare_interrupted`（准备阶段中断，未产生宿主副作用）；保留“旧记录缺该字段视为未知”。
- 依据：`application.go:41-51` 常量块注释、`cr3-report` 第一节映射表。

### 2. `docs/ARCHITECTURE.md` §52 — 新增恢复终态化语义句（`docs/ARCHITECTURE.md:1236`）

- 改前：无（该语义仅存在于源码注释与 CR-3 报告）。
- 改后：新增一段——恢复阶段判定为不可重放的记录（`superseded` / `stale_binding` / `legacy_unbound` / `prepare_interrupted`）落 `DONE` 终态并记录成因，离开活动部署集合、重启不再重复告警；四者均不构成取代依据，也不重放回滚。
- 依据：`closeUnreplayable`（`application.go:856-866`）、`RecoverDeployments` 判定分支（`:769-833`）、`supersededByLaterDoneDeployment` 只认 `completed_kind=deployed`（`:923`）。

### 3. `docs/ARCHITECTURE.md` §52 — 双重防护引导句（`docs/ARCHITECTURE.md:1252`）【冲突旧表述改正】

- 改前：重放回滚前必须通过双重防护，任一命中即不改动任何文件（保留 Journal 记录，日志标记待人工检查）：
- 改后：重放回滚前必须通过双重防护，任一命中即不改动任何文件、不重放回滚，记录落 `DONE` 终态并记录成因（备份文件保留，供人工检查）：
- 冲突点：旧文“保留 Journal 记录，日志标记待人工检查”描述的是“行永久留在活动集合、每次重启重复告警”的旧行为；CR-3 后命中防护的行当次恢复即落 `DONE` 终态（成因 `superseded` / `stale_binding` / `legacy_unbound`），不再悬挂。文件（含备份）仍不改动、不重放，故保留“供人工检查”的运维含义。

### 4. `docs/PRODUCT.md` §42 Deployment Journal — 新增 `completed_kind` 枚举与终态化句（`docs/PRODUCT.md:1196-1198`）

- 改前：字段清单只有 `- completed_kind` 一项，§42 全文无该字段的枚举说明（原文仅在 Superseded 条目引用 `completed_kind=deployed`）。
- 改后：在字段清单与 `Phase：` 之间补两段，与 ARCHITECTURE.md §52 同风格：
  1. 枚举句：`deployed` / `rolled_back` / `prepare_failed` / `superseded` / `stale_binding` / `legacy_unbound` / `prepare_interrupted`，各附含义；旧记录缺该字段视为未知。
  2. 终态化句：四类不可重放记录落 `DONE` 终态并记录成因，离开活动部署集合、重启不再重复告警；均不构成取代依据，也不重放回滚。
- 说明：CR-3 报告第 187 行称“PRODUCT.md §42 当前只枚举 deployed/rolled_back/prepare_failed”，与文档实际不符（PRODUCT.md 此前未枚举任何值，仅列出字段名）；本次按“两处一致”补齐完整枚举，已在下文第二节记录该差异。

### 5. `docs/PRODUCT.md` §42 — 双重防护引导句（`docs/PRODUCT.md:1214`）【冲突旧表述改正】

- 改前：恢复重放回滚前必须通过双重防护，任一命中即不改动文件、保留 Journal 待人工检查：
- 改后：恢复重放回滚前必须通过双重防护，任一命中即不改动文件、不重放回滚，记录落 `DONE` 终态并记录成因（备份文件保留，供人工检查）：
- 冲突点与第 3 条相同（PRODUCT 侧旧表述“保留 Journal 待人工检查”同样指向旧行为）。

**共 5 处（ARCHITECTURE.md 3 处、PRODUCT.md 2 处），其中 2 处为冲突旧表述改正，3 处为语义补充。**

---

## 二、核查过但未改动的内容（及差异记录）

| 位置 | 原文要点 | 判定 |
|---|---|---|
| `docs/ARCHITECTURE.md:1259` | “一期默认优先安全恢复 Previous；无法确认归属的记录不得覆盖更晚的 DONE 部署，需人工检查。” | 仍成立：不可重放/归属未知的行不重放、备份文件保留待人工检查；终态化不改变该结论，不改 |
| `docs/PRODUCT.md:1218` | “不得用陈旧 backup 覆盖更晚的 DONE 部署。” | 仍成立，不改 |
| `docs/ARCHITECTURE.md:1254`、`docs/PRODUCT.md:1215` | Superseded 条目仅列举 `prepare_failed` / `rolled_back` 不构成取代 | 仍成立且非穷举；4 个新成因的“不构成取代”已由新增终态化句明确，不改（避免与新增句重复） |
| `docs/ARCHITECTURE.md:1744`（§67 不变量 11）、`docs/PRODUCT.md:1501`（§51 不变量 12） | “Managed Application 必须存在恢复路径 / 更新必须可恢复” | 与终态化不冲突（终态化即“恢复到明确状态”的收敛手段），不改 |
| `docs/ARCHITECTURE.md:1564`（Application Upgrade Crash） | “重放前须通过 superseded 与 binding 校验，陈旧 Backup 不得覆盖更晚的 DONE 部署。” | 仍成立，不改 |
| `docs/IMPLEMENTATION_PROMPT.md`、`docs/TEST_PLAN.md`、`docs/TRACEABILITY_MATRIX.md` | 仅提及 Deployment Journal 名称，无 `completed_kind` 枚举 | 无冲突，不改 |
| `openspec/**` | 全库检索 `completed_kind` / 4 个新值：无命中 | 无冲突，且不在本次 `docs/` 边界内 |

**差异记录**：CR-3 报告（第 187 行）称 PRODUCT.md §42 “当前只枚举 deployed/rolled_back/prepare_failed”；实际核查（`git diff` 与原文）显示 PRODUCT.md 此前只有字段名 `- completed_kind`，未枚举任何取值。本次按“两处风格一致”在 PRODUCT.md 补齐完整枚举，属报告描述与文档现状的偏差，不影响语义。

---

## 三、验证

- `git diff -- docs/` 复核：本次新增内容全部位于 `docs/ARCHITECTURE.md`（§52）与 `docs/PRODUCT.md`（§42），无其他文件、无源码/配置/`.e2e/**` 改动（`.e2e/**` 既有未提交改动为其他 Agent 的产物，本次未触碰）。
- 文档改动仅为插入/替换上述 5 处文本，未触碰同文件内其他 Agent 的未提交改动（Remote State TTL、定义校验等）。
- 未执行：Markdown lint / 文档构建（仓库无对应入口）。
