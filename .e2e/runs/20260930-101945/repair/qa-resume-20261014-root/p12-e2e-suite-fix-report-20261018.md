# P12 Playwright E2E 套件资产缺陷修复报告（2026-10-18）

- 任务来源：`failures-classified.json`（qa-resume-20261018-playwright，23 条失败分类，其中 15 条最终失败）中的**套件自身资产缺陷**（非产品缺陷）。
- 修复边界：只改 `web/e2e/**`（6 个文件）；未改产品代码（`web/src/**`、Go）、未改 `.e2e/**` 既有状态文件、无 Git 操作、未装仓库依赖、未触碰 `:8080` 与 Agent 节点 SSH。
- 验证环境：repair 环境 `BASE_URL=http://192.168.100.209:18080`，凭据取自 `/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json`（0600，未明文落盘；运行产物已做密钥脱敏，扫描确认 0 处明文）。
- 结论：**原 15 条最终失败全部消除** —— 10 条转通过，5 条红线前置改为**显式 skip 并说明**（不再报错/静默失败）。全量 20 个 spec、124 条用例实跑结果：**116 通过 / 0 失败 / 8 跳过**（修复前为 106/15/3）。额外修复 4 项未列入清单但阻塞干净环境复用的套件缺陷（GAP-07、SAFE-01、`/api/audit` 405 契约、apiRequest 导航竞态）。

---

## 一、验证方法与前后数据

### 1.1 方法

- 独立 runner：`~/p12-verify/run-pw.py`（沿用 repair 运行器配置：admin 凭据、`NODESTEER_E2E_NODE_IDS=91f44866…`（native-1）、`LABEL_NODE_ID=611c02a5…`（native-2）、RBAC operator/viewer 账号、私有凭据目录 `~/p12-verify/private`；不设置红线相关的 ELF/inspector/cache 变量）。
- **修复前基线**（2026-10-18 15:50–15:57，未改代码）：对受影响用例逐条实跑复现，15/15 全部复现（与 `failures-classified.json` 一致）。
- **修复后复验**（16:00–16:59）：受影响 5 个文件全文件实跑，其余 15 个文件全量回归实跑。
- 原始结果：`~/p12-verify/raw/*-run.json`；用例证据：`~/p12-verify/evidence/*.json`；资源台账：`~/p12-verify/playwright-resources.jsonl`。

### 1.2 前后通过数对比（同一 repair 环境）

| 范围 | 修复前 | 修复后 |
|---|---|---|
| 全量套件（124 条，20 文件） | 首轮 98 通过 / 23 失败 / 3 跳过；多轮复跑后 106 / 15 / 3 | **116 通过 / 0 失败 / 8 跳过** |
| p23-detail-layout | P23-02 失败 | 4 / 0 / 0 |
| rbac | RBAC-08 失败 | 7 / 0 / 1（RBAC-05 设计内 skip） |
| full-feature-ui | FULL-06/08/09、ROLE-01/02 失败 | 8 / 0 / 3（红线 skip） |
| gap-ui | GAP-07*/14/15/16/17/20 失败 | 23 / 0 / 1（红线 skip） |
| safe-ui | SAFE-01/05/21/23/24 失败 | 23 / 0 / 1（红线 skip） |

\* GAP-07 在分类中经复跑已通过（非 15 条之一），本轮因数据前提再次失败，已一并修复为确定性用例。

### 1.3 原 15 条最终失败逐条结果

| # | 用例 | 修复前 | 修复后 | 说明 |
|---|---|---|---|---|
| 1 | FULL-06 文件中继双目标 | 失败 | **通过** | 目标节点改为环境驱动（实跑 native-2+native-3 双目标中继成功） |
| 2 | FULL-08 App Deploy/Unit/SHA | 失败 | 跳过（红线） | 客户机 SSH + ELF 前置缺失 → 显式 skip |
| 3 | FULL-09 App Task Deploy | 失败 | 跳过（红线） | 同上 |
| 4 | ROLE-01 角色 Audit 只读 | 失败 | **通过** | 角色账号运行时发现/自建 + /api/audit 405 契约同步 |
| 5 | ROLE-02 Operator 部署只读 | 失败 | 跳过（红线） | 账号硬编码已修；ELF/检查器前置缺失 → 显式 skip |
| 6 | GAP-14 Audit 逐事件 | 失败 | **通过** | 搜索响应 + 重渲染稳定后再断言行数 |
| 7 | GAP-15 物理离线窗口 | 失败 | 跳过（红线） | 节点改为环境驱动；Root 编排前置缺失 → 显式 skip |
| 8 | GAP-16 11 表空态 | 失败 | **通过** | 空态断言对齐产品实际文案 |
| 9 | GAP-17 Viewer 审计不可写 | 失败 | **通过** | Viewer 环境驱动 + 自建审计夹具（去除对 GAP-14 的级联） |
| 10 | GAP-20 Script 边界 | 失败 | **通过** | 夹具改为上限内 + 262144/262145 边界契约断言 |
| 11 | SAFE-01 11 表分页 | 失败 | **通过** | Radix Select 视口漂移修复（追加修复） |
| 12 | SAFE-05 Schedule 校验 | 失败 | **通过** | 同步「间隔<1 禁用保存 + 内联报错」 |
| 13 | SAFE-21 用户生命周期 | 失败 | **通过** | `/api/me` snake_case 契约同步 |
| 14 | SAFE-24 自身降级收敛 | 失败 | **通过** | 自建账号夹具，去除 SAFE-21 级联与旧前缀 |
| 15 | SAFE-23 Script 缓存 | 失败 | 跳过（红线） | Agent 本地缓存检查器需 SSH → 显式 skip |

---

## 二、修复清单（问题 / 改动文件:行 / 验证方式 / 实跑结果）

### 1. P23-02、RBAC-08 依赖既有任务（中）

- 问题：两用例直接取 `/api/tasks[0]`；repair 环境任务列表为空 → P23-02 打开 `/tasks/undefined`、RBAC-08 找不到「立即运行」链接。
- 改动：
  - `web/e2e/tests/p23-detail-layout.spec.ts:38,59`：P23-01/P23-02 改为 `createIsolatedTask` 自建任务夹具（自有前缀），`finally` 清理；`:80` P23-03 无执行历史时显式 `test.skip` 说明。
  - `web/e2e/tests/rbac.spec.ts:91`：RBAC-08 先以 admin 自建任务夹具，`finally` 重新登录 admin 后清理。
- 验证：修复前基线 P23-02/RBAC-08 失败复现；修复后单跑与全文件跑。
- 实跑：P23 全文件 4/4 通过；RBAC 全文件 7 通过 1 设计内跳过；夹具任务均已删除（`/api/tasks` 0 残留）。

### 2. GAP-14 竞态假失败（中）

- 问题：`expect(await tbody tr count()).toBe(3)` 在服务端搜索重渲染前取值，读到未过滤的 10 行（未过滤页已含该 ID，前一行断言立即通过）。
- 改动：`web/e2e/tests/gap-ui.spec.ts:553-560`：先 `waitForResponse`（`q=<精确ID>`、GET、200），再 `expect.poll(tbody tr count)` 等到过滤结果稳定，最后断言包含 ID。
- 实跑：GAP-14 通过；证据 `evidence/crud-login-audit.json` 记录 5 类对象逐事件行数=期望值。

### 3. GAP-20 夹具越界（中）

- 问题：夹具 294954 字节 > 产品 256 KiB 上限（`internal/hub/validation.go` `maxScriptContentBytes=256*1024`）→ 400。
- 改动：`web/e2e/tests/gap-ui.spec.ts:782-796`：夹具精确构造为 **262144 字节**；新增边界契约断言：恰好 262144 → 200、262145 → 400 且错误含 `script content too large`/`262144`。
- 实跑：GAP-20 通过；证据 `evidence/script-disabled-reference-boundaries.json`：`content_limit_contract={max_bytes:262144, at_limit_http:200, over_limit_http:400, over_limit_error:"script content too large: 262145 bytes (max 262144)"}`。

### 4. SAFE-05 期望陈旧（低）

- 问题：产品已按缺陷修复 `OBS-20261014-SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED`：间隔<1 时保存禁用 + 内联实时报错；用例仍点击保存并等错误提示 → 15s 超时。
- 改动：`web/e2e/tests/safe-ui.spec.ts:152-156`：填入 `0` 后断言保存按钮 `disabled` 且页面含「间隔必须大于 0 秒」（`web/src/features/editors/index.tsx:297-299,433`）。
- 实跑：SAFE-05 通过。

### 5. SAFE-21 契约陈旧（低）

- 问题：`/api/me` 已为 snake_case（`docs/PRODUCT.md:1392` 契约），用例仍断言 `{Username,Role}`。
- 改动：`web/e2e/tests/safe-ui.spec.ts:656-658`：改为 `{username, role}`。
- 实跑：SAFE-21 通过（三角色登录、密码新旧边界全链路）；证据 `evidence/user-lifecycle-ui.json`。

### 6. SAFE-24 级联依赖（低）

- 问题：依赖 SAFE-21 写出的 `final:true` 记录 + 硬编码旧前缀 `qa-20260930-101945-web-user-`。
- 改动：`web/e2e/tests/safe-ui.spec.ts:748-783`：自建 `administrator` 账号夹具（自有前缀 + 一次性随机口令，写入私有凭据库；无删除 API，结束时保留为 viewer），不依赖执行顺序；`/api/me` 断言同步 snake_case。
- 实跑：SAFE-24 通过；证据 `evidence/self-role-downgrade.json`：旧会话 `me.role=viewer`、`/api/users` 与写探针 403、按钮消失。

### 7. 环境硬编码 6 处（低）

| 位置 | 原硬编码 | 修复 |
|---|---|---|
| `full-feature-ui.spec.ts:286-291` | `hostname=~/agent-2/i` 选第二中继目标 | `NODESTEER_E2E_TRANSFER_TARGET_IDS` 优先，默认 `authorizedNodeIds()`（NODE_IDS+LABEL_NODE_ID），不足 2 个时显式 skip |
| `full-feature-ui.spec.ts:440,631` | 单元名 `qa-20260930-101945-web-*` | `TEST_PREFIX` 驱动 |
| `full-feature-ui.spec.ts:555` | 任务名前缀 `qa-20260930-101945-web-` | `TEST_PREFIX` |
| `full-feature-ui.spec.ts:586,626` | `QA_ROLE_USER_ID='8ac0ef0f-…'` | `ensureRoleAccount()`：env（`NODESTEER_E2E_ROLE_USERNAME/PASSWORD/ID`）→ 私有库运行时发现 → 自建夹具 |
| `gap-ui.spec.ts:565-570` | 节点 `d77e2440-…` | `NODESTEER_E2E_OFFLINE_NODE_ID` → 默认 `NODESTEER_E2E_LABEL_NODE_ID` |
| `gap-ui.spec.ts:674-689` | Viewer `8ac0ef0f-…` + 改其口令 | 环境驱动 `NODESTEER_E2E_VIEWER_USERNAME + NODESTEER_E2E_RBAC_PASSWORD`，否则私有库发现/自建；不再改他人口令 |
| `safe-ui.spec.ts:751` | 旧前缀断言 | 见第 6 条，改为自建账号 |
| `fixtures.ts:206-213,220-260` | 新增公共能力 | `authorizedNodeIds()`、`ensureRoleAccount()` |

- 实跑：FULL-06 通过（native-2+native-3 双目标，任务/中继/校验任务/失败重试取消全链路）；ROLE-01 通过（运行时发现上一轮 QA viewer 账号并完成 operator→viewer 收敛）；GAP-17 通过（env Viewer）；GAP-15 显式 skip。

### 8. GAP-16 空态字面量（低）

- 问题：断言「空数据集必须含『暂无数据』且不含『没有结果。』」，与产品实际不符：audit 走共享 DataTable（`web/src/features/shared/data-table.test.tsx:131` 明确要求空数据显示「没有结果。」）、executions/transfers 使用领域化空态。
- 改动：`web/e2e/tests/gap-ui.spec.ts:620-641`：按模块对齐产品实际空态文案（audit=「没有结果。」、executions=「没有匹配的执行记录」、transfers=「暂无文件传输」、其余=「暂无数据」），并注释原因；Dashboard 空态断言同步为领域文案（`features/overview/index.tsx:252,351`）。
- 实跑：GAP-16 通过；证据 `evidence/all-tables-error-retry-mock.json` 记录 11 表逐一比对。

### 9.（追加）SAFE-01 Radix Select 可操作性（HARNESS_ENV，确定性）

- 问题：长表格页底部的「每页行数」下拉，选项被判「视口外」（Playwright 滚动使 popper 漂移）→ 15s 超时（3/3 复现）。
- 改动：`web/e2e/tests/safe-ui.spec.ts:73-90`：展开前将触发器 `scrollIntoView({block:'center'})`，选项点击带 5s 有界重试（Escape 后重开）。
- 实跑：SAFE-01 通过，11 个模块 `page_sizes_and_persist` 全为 true（`evidence/tables-safe.json`）。

### 10.（追加）`/api/audit` 方法契约陈旧（ROLE-01/02 阻塞项）

- 问题：`verifyReadonlyAudit` 期望 PUT/DELETE `/api/audit` 返回 200+读数组；当前产品（`docs/PRODUCT.md:1385`、`internal/hub/api/api.go:2087`）为 **405 + `Allow: GET`**（方法守卫先于鉴权）。此前 ROLE-01/02 未跑到此处，故未在分类中出现。
- 改动：`web/e2e/tests/full-feature-ui.spec.ts:46-66`：断言 405 且 `Allow: GET`，审计行经 GET 复核不变；`gap-ui.spec.ts:698-711` GAP-17 探针记录 `allow` 头并同步证据文案。
- 实跑：ROLE-01 通过；GAP-17 通过；证据 `evidence/viewer-audit-immutable.json`：`probes=[{PUT,405,allow:GET},{DELETE,405,allow:GET}]`、`exact_audit_unchanged=true`。

### 11.（追加）GAP-07 跨页搜索数据前提（HARNESS_ENV）

- 问题：依赖环境里恰好存在「第 2 页唯一 resource_id」的审计行；干净环境/当前数据下不存在 → 抛错。
- 改动：`web/e2e/tests/gap-ui.spec.ts:285-330`：自建跨页夹具（1 个 marker 脚本 + 10 个一次性脚本把 marker 挤出第 1 页），断言 marker 不在第 1 页后验证服务端跨页搜索；`finally` 清理 11 个脚本。
- 实跑：GAP-07 通过；证据 `evidence/audit-cross-page-search.json`：`marker_script_id=7077f379…`、`second_page_hit=true`、`global_hit=true`。

### 12.（追加）apiRequest 硬导航竞态（FULL-02 假失败）

- 问题：修复后回归实跑中 FULL-02 出现一次 `page.evaluate: Execution context was destroyed`（页面硬导航与轮询取值竞态），属套件时序缺陷。
- 改动：`web/e2e/tests/fixtures.ts:131-170`：`apiRequest` 返回 `headers`（供 405 契约断言）；对**幂等 GET** 在「执行上下文被销毁」时有界重试（POST/PUT/DELETE 不重试，避免重复写）。
- 实跑：FULL-02 及全量回归均通过。

---

## 三、红线受限项（显式 skip，未执行）

以下 5 条需要触碰 Agent 节点 SSH / 物理离线编排，超出本轮授权红线，已从「报错失败」改为**显式 skip + 原因**（与套件既有 `RBAC-05`/`E2E-11`/`P21-02` 的 skip 约定一致）：

| 用例 | skip 条件 | 原因 |
|---|---|---|
| FULL-08 | 缺少 `NODESTEER_E2E_APP_ELF / GUEST_INSPECT / APP_DIR` | 需客户机 SSH 落 systemd unit 并核验文件 SHA |
| FULL-09 | 同上 | 同上 |
| ROLE-02 | 同上 | 账号硬编码已修复；ELF/检查器前置缺失 |
| SAFE-23 | 缺少 `NODESTEER_E2E_SCRIPT_CACHE_INSPECT` | 需 SSH 读取 Agent 本地 `data_dir/state.db` 脚本缓存 |
| GAP-15 | 未设置 `NODESTEER_E2E_OFFLINE_ORCHESTRATOR=1`（或节点未提供） | 需 Root 侧物理停止/恢复 Agent daemon 的离线窗口编排 |

> 说明：这 5 条在提供对应环境变量/编排的授权环境中仍会正常执行（代码路径保留，仅前置缺失时跳过）。

---

## 四、未做项与剩余风险

- **未执行**：上表 5 条红线用例的运行时验证（环境前置缺失，原因如上）；`web/e2e` 全套件未做多浏览器项目验证（配置仅 chromium，维持现状）。
- **未修改（观察项）**：GAP-18 的「托管应用状态 0/1」仍隐含「环境无遗留应用」；本轮实跑通过（applications 为空）。分类建议的「相对增量断言」未纳入本轮清单，保留为后续可选改进。
- **行为变化提示**：5 条红线用例由「失败」变为「跳过」，套件在缺少客户机权限的环境中不再报红；如需保持失败语义以便门禁拦截，可去掉对应 `test.skip` 前置（改动点见第三节表格）。
- 其余 15 个 spec 文件在 `fixtures.ts` 变更后已全量回归通过，无已知回归。
- 无产品缺陷判定：所有修复均为套件侧期望/夹具/时序问题；产品代码零改动。

---

## 五、夹具、清理与保密

- 自建夹具全部使用自有前缀 `qa-pw-20261018-`，测后清理：实跑后 `/api/tasks`、`/api/applications`、`/api/schedules` 均 0 残留，`/api/scripts` 无 qa-pw 残留（仅 2 个更早轮次遗留脚本）。
- 用户账号无删除 API：本轮新增 3 个 QA 账号（`qa-pw-20261018-user-2a61a578`、`qa-pw-20261018-user-86a66362`、`qa-pw-20261018-self-role-user-beb80b77`），一次性随机口令记录于 `~/p12-verify/private/ui-users.jsonl`（目录 0700、文件 0600），账号一律收敛为 viewer；ROLE-01 复用私有库中既有 QA viewer 账号，未新增。
- 凭据零明文：运行器读取 0600 凭据文件并在产物落盘前脱敏；对 `~/p12-verify` 的 raw/evidence/log 全量扫描，未发现 admin/注册令牌/RBAC/自建账号口令。

## 六、证据位置

- 原始结果（按文件）：`~/p12-verify/raw/*-run.json`（含每文件 pass/fail/skip 与用例级标题）。
- 用例证据：`~/p12-verify/evidence/*.json`（本报告引用的关键文件：`script-disabled-reference-boundaries.json`、`audit-cross-page-search.json`、`viewer-audit-immutable.json`、`tables-safe.json`、`self-role-downgrade.json`、`user-lifecycle-ui.json`、`all-tables-error-retry-mock.json`）。
- 运行日志：`~/p12-verify/baseline.log`（修复前复现）、`~/p12-verify/others.log`（其余文件回归）。
- 改动 diff：`git diff -- web/e2e/`（6 文件，+310/−163；未提交，无 Git 操作）。
