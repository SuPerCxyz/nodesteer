# G7「缺陷与产品裁决」组 · 裁决简报

> 性质：只读分析。本文件为本次任务**唯一写入**；未修改源码/配置/台账，未访问远程环境或浏览器，未运行任何测试。
> 台账核对时间：**2026-10-17**（读 `repair/feature-inventory.json`、`.e2e/failures.json`、`.e2e/observations.json`、`.e2e/run-state.json`、`repair/defect-regression-matrix.json` 当前内容 + 源码只读核对）。
> 与 `authorization-checklist-20261016.md` G7 组（5 条）逐条核对后，**已结案 1 条**，**仍待决 4 条（3 个裁决点）**。
> 与 `pending-decisions-20261016.md` 的关系：A 节 12 条、G 节 CR-1/CR-3 均**不在本简报范围**，不重复列项；F1 中已被独立核验改判的条目见 §8。

---

## 0. 仍待决 / 已结案 对照表

| # | 用例 | G7 原裁决点 | 台账现状（2026-10-17 核对） | 本次判定 |
|---|---|---|---|---|
| 1 | `G04-01` | 立项修复 or 豁免 | **FAIL**（`baseline_status=FAIL`，P1，2026-10-16 由独立核验转 FAIL）；缺陷 `E2E-20261016-EDITOR-PENDING-NOT-DISABLED-DUPLICATE-POST`（P2，`CONFIRMED_TEST_FAILURE_REPAIR_SCOPE_REVIEW_PENDING`）已立案 | ✅ **已结案**（定性 = 立项修复；实施另需授权，不占裁决） |
| 2 | `X08` | ……（清单只列"顺带补 1 条断言"） | **PARTIAL**；`remaining_dimensions` 整体过期（核验已证 8 项维度有真实证据），唯一真缺口 = **"慢响应重复点击"口径** + "取消编辑不改 Audit"未直接断言 | ⏳ **仍待决**（口径裁决 + 1 条只读断言） |
| 3 | `G04-04` | Enter 不提交 / Escape 焦点落 BODY：设计 or 修复 | **PARTIAL**；真实证据两条都成立（Enter 0 POST、焦点落 BODY） | ⏳ **仍待决**（设计 vs 修复，建议拆分处置） |
| 4 | `X19` | 同上（同两条键盘缺口） | **PARTIAL**；与 `G04-04` 同源，Tab/批量工具栏维度已有真实证据 | ⏳ **仍待决**（与 `G04-04` **合并为 1 个裁决点**） |
| 5 | `P12P13-05` | 过去时间 One-Time 保存可用 与 空 `run_at` 服务端拒绝口径 | **PARTIAL**；时区换算维度已由 WORKFLOW-20261008 真实证据覆盖，剩余 2 项 | ⏳ **仍待决**（1 个设计裁决 + 1 条 API 断言） |

**计数**：仍待决 **4 条用例 / 3 个裁决点**（`X08`；( `G04-04` + `X19` )；`P12P13-05`）；已结案 **1 条**（`G04-01`）。

---

## 1. `X08` —— "慢响应重复点击"是否覆盖创建表单（口径裁决）

**问题一句话**：用例 `X08` 的"慢响应重复点击"没有限定范围；实测创建表单（Group/Script 等编辑器）在慢响应下按钮可用且发第 2 次 POST（已立缺陷），但用例同时列举的破坏性动作（删除/Run/Stop/批量升级/吊销）全部"取消 0 写、确认单请求"且均有真实证据 —— 该维度算 FAIL 还是按窄口径记为"另见 G04-01"？

**证据**
- 台账 `status=PARTIAL`，`remaining_dimensions` 原文仍写"停止/批量升级/吊销取消、慢响应重复点击、失败后重试、取消编辑不改 Revision/Audit **均未执行**"——**该描述整体过期**（核验结论，`status-upgrade-verification-20261016.md:60-79`）：
  - 吊销取消零写：`repair/qa-resume-20261009-backend/reenroll-revoke-cancel.json` → `post_cancel_revoke_post_requests:0`、`credential_unchanged:true`、节点仍 `online`。
  - 批量升级/维护取消零写：`repair/qa-resume-20261004/evidence/resume-20261004-safe_more/bulk-selection-keyboard.json` → `upgrade_cancel_zero_write:true`、`maintenance_cancel_zero_write:true`、`selection_preserved_on_cancel:true`。
  - 失败后重试：`…/resume-20261004-gap_more/enrollment-retry-pending.json` → `pending_disabled:true`、`successful_post_count:1`。
  - 取消编辑不改 Revision：`…/resume-20261004/resume-20261005-b-01-results.json`（`P08-08`，0 次 PUT、回读 `revision 1`）。
  - Stop 取消零写 + 审计落地：`SAFE-15`（`web/e2e/tests/safe-ui.spec.ts:447-498`）。
- 仍缺：**创建表单类**重复点击 → 真实反证 `repair/qa-resume-20261014-web/p-table/evidence/G04-01-pending-submit.json`：`checks[0]`（`/groups/new`，hold 4500ms）`pending_button.disabled=false`、holder 阶段 `APP_POST_EXTRA_WHILE_PENDING count=2`、`post_count=2`；`checks[1]`（`/scripts/new`）同型。
- 台账另一条待决（同源）：`OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED`（状态 `NEEDS_REVERIFICATION_POSSIBLE_REGRESSION`）。
- 源码佐证（只读）：`web/src/features/editors/index.tsx` 的 `save`（`:109/:428/:780/:1066`）无 in-flight 守卫，保存按钮 `:370/:714/:910/:1598` 无 pending 禁用；已修参照实现为 `web/src/features/tasks/index.tsx:633-651`（`savingRef` + `saving`）与 `:1361`（`disabled={saving} aria-busy={saving}`）。

**影响**
- 可见性：仅在**慢响应窗口**内重复点击时触发（正常网速下难以复现），不是常态路径。
- 是否阻塞能力：**不阻塞**任何功能；对象由服务端名称去重拦截（`internal/hub/definitions.go:414` script、`internal/hub/node.go:335` group），实测"未产生重复对象"。
- 只在边界触发：是（pending 窗口 + 人为二次点击）。

**可选处置**
- **A（广义口径）**：承认 `X08` 该维度被反证 → 随 `G04-01` 一并转 FAIL，修复后同批复测，同时补 1 条 `/api/audit` 断言。
- **B（窄口径）**：把 `remaining_dimensions` 改写为"仅创建表单类按钮语义（见 `G04-01`）"，补 1 条 `/api/audit` 只读断言后升 PASS，缺陷仍由 `G04-01` 承载。
- C：维持现状不动（不推荐——台账措辞与已核验证据矛盾，会持续误导后续批次）。

**推荐与理由**
**推荐 A（广义口径 → 转 FAIL，与 `G04-01` 同源闭环）**。理由：① 用例原文是无限定的"慢响应重复点击"，窄口径需要改写用例语义，属对既定期望的收窄，应由产品明确；② 无论选 A 还是 B，修复与回归的**实际工作量完全相同**（同一批前端改动、同一次复测），A 不需要额外成本却避免了"用口径把已证实的反证写掉"；③ A 还能顺带把 `OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED` 一并闭环（当前为 `NEEDS_REVERIFICATION`）。
若产品选择 B，则必须同时做两件事：改写 `remaining_dimensions` 措辞（去掉已过期的四项）**并**补一条 `/api/audit` 查询断言（`X08` 只剩这一个未直接断言项）。

**若不处置的后果**：`X08`（P0）长期停留 PARTIAL 且 `remaining_dimensions` 与证据互相矛盾；后续批次会按过期描述重复安排"已测过"的窗口；`OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED` 的定性悬空，缺陷清单与台账口径不一致。

---

## 2. `G04-04` + `X19` —— Enter 不提交、Escape 后焦点落 BODY（合并裁决点）

**问题一句话**：公共交互用例要求"Tab 顺序、Enter 提交、Escape 关闭弹窗符合预期"与"…Escape 确认/取消与**焦点返回**"；实测编辑器页面 Enter 不发请求（0 POST，属设计性缺失），确认框关闭后焦点落到 `<body>` 而非触发按钮 —— 两者分别按"设计"接受还是按缺陷立项修复？

**证据**
- Enter 不提交（0 POST，真实运行）：`repair/qa-resume-20261014-web/p-table/evidence/G04-04-form-keyboard.json`
  - `/groups/new`：`enter_submit.posts=0`、`post_lines=[]`、`after.url=/groups/new`、`alert=null`；对照 `save_button_click.posts=1` → 跳转 `/groups`（证明该表单只能"点按钮"提交）。
  - 同构证据：`evidence/G04-keyboard-a11y.json → G04-04_enter_submit`：`posts=0`、`groups=13`、`created=false`。
- Escape 关闭正常但焦点丢失（真实运行）：
  - `evidence/G04-04-02-focus-return-probe.json`：`dialog_open_focus={tag:BUTTON,text:"取消"}` → `after_escape={dialog:false,tag:"BODY"}`、`after_escape_plus1s` 仍为 `BODY`；`delete_requests=0`（取消路径无写，安全面正常）。
  - `evidence/G04-keyboard-a11y.json`：`G04-02_escape_cancel.after.focused_tag="BODY"`（`delete_requests:0`）；**`G04-02_confirm_delete.after.focused_tag` 同样是 `"BODY"`**（`delete_requests:1`）→ 焦点丢失**不限于取消**，确认删除后也丢。
- Tab 顺序与批量工具栏维度 **已有真实通过证据**（`G04-keyboard-a11y.json → G04-04_tab_new_group` 顺序合理；`X19` 批量工具栏 Arrow/Home/End/Escape 见 `SAFE-20` + `bulk-selection-keyboard.json`：`selection_preserved_on_cancel:true`、零写）→ 两用例各自只剩上述两条键盘缺口。

**影响**
- 可见性：键盘/无障碍（a11y）用户可直接感知；鼠标用户基本无感；**无任何数据副作用**（无额外写请求）。
- 是否阻塞能力：不阻塞。`docs/TEST_PLAN.md:638` 本身把"非核心可访问性问题"归为 **P3**。
- 只在边界触发：是（只用键盘导航的用户）。

**机制（只读核对，供估工）**
- Enter 不提交：编辑器页面不是 `<form>`，保存是 `<Button onClick={save}>`（`web/src/features/editors/index.tsx:910` 等 4 处）；全仓 `web/src` 中 `<form` 仅 9 处，键盘提交可用的场景是**表单化的对话框**，如纳管对话框 `web/src/features/catalog/index.tsx:407-409`（`onSubmit`）、登录 `web/src/features/auth/sign-in/index.tsx:144`。
- 焦点不返回：`ConfirmDialog`（`web/src/components/confirm-dialog.tsx:31-38`）使用受控 Radix `AlertDialog`，**没有 `AlertDialogTrigger`**（由下拉菜单项打开），Radix 无触发元素可回焦 → 落 `document.body`。修一次即覆盖全部 **8 处调用点**（`web/src/features/shared/ui.tsx:350,402`、`catalog/node-bulk-actions.tsx:141,164,174`、`tasks/index.tsx:1482`、`executions/index.tsx:458`、`components/sign-out-dialog.tsx:29`）。

**可选处置**
- **Enter 侧**：① 接受为设计 + 写入口径（编辑器=显式按钮提交）；② 立项修复（把编辑器保存控件包进 `<form onSubmit>`）。
- **焦点侧**：① 立项修复（在 `ConfirmDialog` 上做 `onCloseAutoFocus` / 记录触发元素并回焦）；② 记入已知限制并文档化（按 TEST_PLAN 的 P3 归类）。

**推荐与理由**
**拆分处置：Enter 侧"接受为设计 + 文档化"；焦点侧"立项修复"（低风险单组件修复）。** 理由：
- Enter 侧改 `<form>` 会**变更既有交互**：编辑器（尤其调度编辑器，需逐字段配置 + 预览）在多字段表单里按 Enter 立即提交容易造成意外保存/跳转，收益（少点一次鼠标）明显小于风险；且现网真正表单化的对话框 Enter 已可用，口径可写清"对话框表单支持 Enter 提交，编辑器页面为显式按钮提交"。这条属**产品可一句定**的设计确认。
- 焦点侧不改行为语义、无写风险，修复点在共享组件一处生效 8 个调用点，是可低成本达标的键盘可达性（WAI-ARIA APG 对话框模式要求关闭后焦点回到触发元素）；且证据显示**确认删除后焦点也丢**，属实际可用性缺陷而非纯审美。
- 落地后 `G04-04`、`X19` 的剩余缺口即全部闭合（Tab/批量工具栏维度已有证据），可随一次定向复测（1 批 2 条）收口。

**若不处置的后果**：两用例（P1）长期挂 PARTIAL 且缺口描述含糊；每次回归都要人工重判"Enter 不提交算不算问题"；8 个确认框的焦点丢失在无障碍复核时会被反复提出。

---

## 3. `P12P13-05` —— 过去时间 One-Time 允许保存；空 `run_at` 服务端拒绝口径

**问题一句话**：One-Time 选择**过去时间**时不显示内联错误、保存按钮可用（若提交会被服务端接受）；空 `run_at` 的"服务端是否拒绝"在本轮 E2E 从未断言 —— 前者按设计接受还是加拦截，后者口径如何写定？

**证据**
- 台账 `status=PARTIAL`，`remaining_dimensions`：「时区换算与空 `run_at` 的服务端拒绝未验证；过去时间未观察到拒绝（如实记录）。」
- 真实运行（UI 观测，未提交）：`repair/qa-resume-20261014-web/p-web/results.json → P12P13-05`
  - `empty_run_at: "未选择运行时间，无法预览。"`（有内联提示）
  - `past_run_at: "accepted (no inline error, save enabled)"`、`save_disabled=false`、`aria_invalid=0`
  - `future_run_at: "accepted"`、`timezone_conversion_verified:false`
  - 原始证据：`repair/qa-resume-20261014-web/p-web/evidence/P05B-04-X05-P12P13-schedule.json`（`onetime_form` / `onetime_past` / `onetime_future` / `writes: []` —— **未发生写**）
  - 观测记录：`OBS-20261014-PAST-ONETIME-SAVE-ENABLED`（`NEEDS_VERIFICATION_UI_VALIDATION_GAP`，未提交）
- **时区换算维度其实已被覆盖**（台账 remaining 偏保守）：`WORKFLOW-20261008:P12P13-05`（`repair/qa-resume-20261008-workflows/task-schedule-results-…json`）—— Asia/Shanghai 未来 one_time `run_at` 保存后真实 Agent `SUCCESS` 输出 marker。
- 只读代码核对（回答"服务端是否拒绝"）：
  - 客户端：空 `run_at` 被行内拦截且**不发请求**（`web/src/features/editors/index.tsx:133-137`）；过去时间**不拦截**（同一 `save` 只校验非空）。
  - 服务端：`internal/hub/validation.go:177-180` `one_time` 要求 `!RunAt.IsZero()`（错误文本 `run_at required`），经 `internal/hub/schedule.go:58` 在 Create 路径生效；API 映射为 **400**（`internal/hub/api/api.go:1457-1459`）；单测 `internal/hub/on_start_test.go:52-55` 断言该行为。
  - 过去时间的**产品语义是确定的**（`internal/hub/schedule.go:311-330`）：`misfire_policy=run_once`（UI 默认"补跑一次"）→ 立即补跑一次；`skip` → 标记已触发并不再触发（该调度变成"启用中但永不再触发"）。
- 相邻同族项（顺带可闭合，不占本组裁决）：`OBS-20261014-SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED`（`P12P13-04`）——客户端同样**行内拦截且不发请求**（`editors/index.tsx:127-132`），服务端拒绝 `<1`（`validation.go:174-176`）→ 实际是"按钮未禁用"的观感问题，零写，不是边界缺陷。

**影响**
- 可见性：过去时间→无提示；用户可能误存一个"立即补跑一次"或"永远不再触发"的调度。
- 是否阻塞能力：不阻塞；语义与 `misfire_policy` 一致，属已实现的产品语义而非错误。
- 只在边界触发：是（用户手填/误填过去时间）。

**可选处置**
- **A（推荐）**：接受为设计 + 文档化——在 `docs/PRODUCT.md` 明确"一次性调度的过去时间按 `misfire_policy` 处理（补跑一次=立即补跑；跳过=不再触发）"，UI 可选加一行柔性提示；同时补 **1 条 API 断言**（`POST /api/schedules` 空 `run_at` → 400 `run_at required`）把 remaining 清零。
- B：立项修复——过去时间加内联错误/禁用保存（但会**收窄**已实现语义：`skip` 策略下"建一个过去时间的死调度"变成不可创建，需产品确认是否愿意变更行为）。
- C：仅补断言、不做任何口径声明（remaining 仍留着"过去时间未观察到拒绝"，后续还会被当缺口）。

**推荐与理由**
**推荐 A**。理由：① 语义已与 `misfire_policy` 对齐且默认值是"补跑一次"，过去时间**能被解释为合法输入**，属设计选择而非缺陷；② 真正缺的是"服务端拒绝"的端到端证据——该结论**已由只读代码 + 单测确定**，补一次 API 调用即可断言（成本 1 次请求，无需环境授权争议）；③ 文档化能同时消解"无提示"带来的误解；④ 若产品倾向 B，则必须回到 §3 的语义变更确认（会改变 `skip` 策略下过去时间的行为），属可另行单列的小改动。

**若不处置的后果**：`P12P13-05` 继续 PARTIAL，`OBS-20261014-PAST-ONETIME-SAVE-ENABLED` 长期挂"待确认"；文档缺失导致"过去时间能否保存"在每轮复核里被重新提问。

---

## 4. 已结案项存档：`G04-01`（定性已定；以下仅供修复批次直接引用，不需再裁）

**结案依据（2026-10-17 核对）**：台账 `status=FAIL`、`baseline_status=FAIL`、P1；缺陷 `E2E-20261016-EDITOR-PENDING-NOT-DISABLED-DUPLICATE-POST`（P2，`source_change_authorized:false`，`CONFIRMED_TEST_FAILURE_REPAIR_SCOPE_REVIEW_PENDING`）已立；`status-upgrade-review-20261016.json` 记录 `G04-01: PARTIAL→FAIL`。

**修复范围可由证据直接确定（无需再裁）**
- 需修：`web/src/features/editors/index.tsx` 的 4 个编辑器 —— `Schedule`（save `:109`，按钮 `:370`）、`Script`（`:428`，`:714`）、`Group`（`:780`，`:910`）、`Application`（`:1066`，`:1598`）。
- 参照实现（已修）：`web/src/features/tasks/index.tsx:633-651`（`savingRef` 短路 + `saving` 状态）与 `:1361`（`disabled={saving} aria-busy={saving}`）。
- 建议一并加**服务端幂等/去重一致性**评估（见下"严重性放大假说"）。

**严重性放大假说（未复现，建议在修复复测时补 2 次断言确认）**
- 现有"唯一名兜住"的结论只对 **Group**（`internal/store/migrate.go:112-118` UNIQUE + `internal/hub/node.go:335`）、**Script**（`internal/hub/definitions.go:414`）、**Task**（`:428`）成立。
- **Schedule**（`internal/hub/schedule.go:39`）与 **Application**（`internal/hub/application.go:44`）**没有名称去重**；Application 的 `unit_name` 由名称派生（`internal/hub/application.go:48-49` `"nodesteer-" + Name + ".service"`）→ 双击保存可能产生 2 条同名应用/2 条同任务调度，进而出现同名 unit 冲突或重复调度。若成立，缺陷优先级应从 P2 上调。
- 因此修复复测应至少断言：4 个编辑器在 hold 期间的**真实 POST 次数 = 1**（当前 `G04-01-pending-submit.json` 的 `post_lines` 只有 2 行且时间戳几乎相同，更像"同一请求的请求行/响应行"；结构化字段 `APP_POST_EXTRA_WHILE_PENDING count=2` 与 `postSeen=2` 支持"确有 2 次"，建议复测时以 CDP 请求列表或服务端审计计数**独立复核**，不要沿用这两行日志）。

---

## 5. 一句话就能定的条目

| 用例 | 一句话确认即可 |
|---|---|
| `G04-04`（Enter 侧） | "编辑器页面为显式按钮提交，不要求 Enter 提交"——确认后写入口径即闭环。 |
| `G04-04` + `X19`（焦点侧） | "确认框关闭后焦点应回到触发按钮，按小改立项"——确认即可排入前端小批（单组件、8 处调用点、无写风险）。 |
| `P12P13-05` | "过去时间 One-Time 视为合法输入（按 `misfire_policy` 生效），仅要求文档化 + 补 1 条 400 断言"——确认即闭环。 |
| `X08` | "`X08` 的'慢响应重复点击'采用广义口径（含创建表单）"——确认即随 `G04-01` 转 FAIL 并同批复测。 |

（`G04-01` 无需确认：定性已结案，只剩"修复实施"的常规授权。）

---

## 6. 裁决落地后的动作清单（均需**另行授权**，本简报不执行）

| # | 动作 | 覆盖 | 备注 |
|---|---|---|---|
| 1 | 前端小批：4 个编辑器加 in-flight 守卫 + pending 禁用（对齐 tasks 既有实现） | `G04-01`（+`X08` 广义口径） | 无环境风险；建议同批复核 4 编辑器真实 POST 计数=1 |
| 2 | 前端小批：`ConfirmDialog` 焦点返回触发元素 | `G04-04`、`X19` | 单组件修复，覆盖 8 个调用点；无写风险 |
| 3 | 只读断言批（3 条）：`POST /api/schedules` 空 `run_at`→400；`/api/audit` 无"取消编辑"写行；可选复核 Interval 0/-1 的 0 写 | `P12P13-05`、`X08`、`P12P13-04` 相邻项 | 每条 1 次请求，可在隔离实例内完成 |
| 4 | 文档批：`docs/PRODUCT.md` 记录 ① 一次性调度过去时间的 `misfire_policy` 语义 ② 键盘/焦点口径（Enter 不提交为设计） | `P12P13-05`、`G04-04`、`X19` | 与 CR-2 文档同步批可合并（`pending-decisions` G1） |
| 5 | 台账口径更新：`X08` remaining 改写（按裁决结果）、`G04-04`/`X19`/`P12P13-05` 剩余维度措辞 | 4 条 | 状态变更仍应按项目惯例经独立核验后执行 |

> 提示：上述 1/2/3/5 属"改源码/改台账"，超出本简报授权；`docs/PRODUCT.md` 修改见 §7 范围说明。

---

## 7. 范围、证据质量与未做事项（如实记录）

- 本简报**未**执行：任何源码/配置/台账写入；任何远程访问、浏览器操作、测试运行。
- 只读核对的源码位置：`web/src/features/editors/index.tsx`、`web/src/features/tasks/index.tsx`、`web/src/components/confirm-dialog.tsx`、`web/src/components/ui/alert-dialog.tsx`、`web/src/features/catalog/index.tsx`、`internal/hub/{validation.go,schedule.go,api/api.go,definitions.go,node.go,application.go}`、`internal/store/migrate.go`、`docs/TEST_PLAN.md`、`docs/PRODUCT.md`（后者未检索到键盘/焦点类条款）。
- 已知证据瑕疵（供修复批次修正，不改变本次结论）：
  1. `G04-01-pending-submit.json` 的 `post_lines` 存在"同一请求的请求行/响应行"歧义（时间戳差 0.0001s），**真实请求数**建议用 CDP 请求列表或服务端审计计数独立复核（结论方向不变：源码已证明无 in-flight 守卫、按钮无 pending 禁用）。
  2. `p-table` 批次多处维度自述为"未提交/未点击"（过去时间 One-Time、间隔 0/-1），故 `P12P13-05` 的"过去时间可保存"目前是**UI 观测 + 代码级判定**，尚无端到端写证据；§6 动作 3 可补齐。
- 与 `pending-decisions-20261016.md` 的交叉引用：`G04-04`/`X19`/`P12P13-05` 对应其 F1 表的"待产品裁决"三行；`X08` 对应 F1 中"强建议转 FAIL"一行（已被独立核验改为"维持 PARTIAL + 口径待裁决"）；`G04-01` 对应 F1 第一行与 B/C 节缺陷记录，本次核对确认其已落入缺陷清单（C 节 `OBS-20261014-EDITOR-SAVE-PENDING-NOT-DISABLED` 与节点台账同源）。

---

## 8. 明确不在本简报范围（避免重复列项）

| 项 | 现状 | 归属 |
|---|---|---|
| `P22-10` | **已 PASS**（2026-10-16 07:48，受控注入证明 FAILED 侧可达，`qa-resume-20261016-webgap/results.json`） | 不在 G7（F1 项，已结案） |
| `X14` | **已 PASS**（2026-10-16 09:52，跨页选择保持/末页删除/筛选计数，`qa-resume-20261016-g1/case-result.json`） | 不在 G7（F1 项，已结案；G9 免执行） |
| `X16` | 仍 PARTIAL（遮蔽矩阵未逐项执行） | G8 安全复测组 |
| `SYS-40` | 仍 PARTIAL（per-attempt 审计粒度待产品口径） | F1/system 组，非 G7 |
| A 节 12 条（`A1`–`A12`） | 待决 | `pending-decisions-20261016.md` A 节，原样有效 |
| `CR-3` | 仍未决（四类悬挂行是否加终态/只读查询面） | G 节，非 G7 |
