# P17 可访问性收尾报告（2026-10-18）

- 批次：`qa-resume-20261018-a11y`（followup）
- 承接：P11 审计（`p11-a11y-report-20261018.md`）标注的未修项
- 环境：本地 Vite dev server（`127.0.0.1:5199`，代理 repair Hub API `192.168.100.209:18080`）；未部署、未改 Go、未改 `.e2e/**` 状态文件、未 Git、未装仓库依赖
- 范围：仅 `web/src/**`（+ 本报告与 `.e2e` 证据目录）
- 工具：axe-core 4.14.0（`/tmp/axe` 注入）；Chromium 完整构建（`channel: chromium`，headless shell 在本机反复 compositor 崩溃后改用）、Firefox 153.0
- 纪律：只读导航；表单仅「打开 → 校验 → 取消/Esc」；删除确认框只打开后取消；未创建/修改环境业务数据

---

## 0. 四项任务结论（先给结论）

| # | 任务 | 结论 |
|---|---|---|
| 1 | 仅靠 placeholder 命名的输入补可访问名 | ✅ 完成：20 处全部补 `aria-label`（视觉与 placeholder 文案零改动）；静态枚举 20 → 0，浏览器动态探针 22 条页面记录 → 0 条；新增 6 条 vitest 断言并完成 8 例反向验证 |
| 2 | `select-dropdown.tsx` 死代码 | ✅ 复核确认全仓零引用（rg + knip + git 历史），**已删除**；删除后 tsc / 全量测试通过；反向验证「恢复 → knip 再报 unused → 删除 → knip 干净」 |
| 3 | 深色主题全量扫描（25 页 × Chromium） | ✅ 完成：修复后 25/25 页 **0 critical / 0 serious / 0 全部违规**（before 亦为 0，无需新增修复）；浅色全量（Chromium + Firefox）同样 0 |
| 4 | 高对比度 / 200% 缩放抽查（关键 5 页） | ✅ 已扫描并如实记录：200% 缩放 5/5 页 0 违规、无横向溢出；强制色彩 4/5 页各 1 条 `color-contrast`，经逐项核验均为 **axe 在强制色彩模拟下的假阳性**（详见 §4），代码无 `forced-color-adjust` 覆盖，无实际可修项 |

---

## 1. Item 1：placeholder-only 输入补可访问名

### 1.1 待修清单与修复方式

P11 §7.2 列出的 20 处（行号为 P17 修复后现状），全部改为 `aria-label`（与 P11 既有模式一致，不改 DOM 结构、不改视觉、不改 placeholder 文案）：

| 文件 | 位置 | 新增可访问名（zh） |
|---|---|---|
| `features/catalog/index.tsx` | 712 / 719 | `标签键` / `标签值`（节点详情标签录入） |
| `features/editors/index.tsx` | 667 / 700 | `参数: 名称` / `参数: 默认值`（脚本参数） |
| `features/editors/index.tsx` | 758 / 766 | `环境变量: 键` / `环境变量: 值`（脚本环境变量） |
| `features/editors/index.tsx` | 1416 / 1424 | `发布包: 名称` / `发布包: 版本`（应用上传表单） |
| `features/editors/index.tsx` | 1544 | `参数`（应用启动参数） |
| `features/editors/index.tsx` | 1586 / 1595 | `环境变量: 键` / `环境变量: 值`（应用环境变量） |
| `features/tasks/index.tsx` | 1101 / 1133 | `参数名称` / `默认值`（任务参数） |
| `features/tasks/index.tsx` | 1256 / 1272 / 1285 | `本地条件值` / `路径或端口` / `用于评估的命令` |
| `features/tasks/index.tsx` | 1361 / 1387 | `任务 ID` / `远程条件值` |
| `features/executions/index.tsx` | 679 | `搜索日志...`（执行详情日志搜索） |
| `features/shared/ui.tsx` | 215 | `aria-label={placeholder}`（SearchToolbar 通用搜索框） |

新增 i18n key（`locales/zh.ts` + `locales/en.ts` 各 7 条）：`common.key`、`common.value`、`nodes.labelKey`、`nodes.labelValue`、`tasks.conditionTaskId`、`tasks.localConditionValue`、`tasks.remoteConditionValue`。

命名策略说明：与 placeholder 文案直接对应的输入沿用同文案；同页可能重名的（脚本参数「名称」vs 表单「名称」、应用上传「名称/版本」vs 表单「名称/版本」、本地/远程条件值）使用「区块前缀: 字段」复合名，与 P11 对 `参数类型: <参数名>`、`角色: <用户名>` 的处理方式一致。

### 1.2 before → after 计数

**静态枚举**（全 `web/src/**/*.tsx`，排除测试；判定 = 有 placeholder 且无 aria-label/aria-labelledby/id，且未确认被包裹 label）：

| | 候选数 | 实际待修 |
|---|---:|---:|
| before | 22 | **20**（2 个 `transfers/index.tsx:298/327` 由包裹 `<label>` 命名，误报排除） |
| after | 2 | **0**（仅剩同 2 个 label 包裹输入） |

**浏览器动态探针**（登录后访问 25 页面场景 + 3 个条件输入展开态，逐控件计算「除 placeholder 外是否还有 aria-label / aria-labelledby / 包裹 label / label[for] / title」）：

| 页面状态 | before | after |
|---|---:|---:|
| node-detail | 2 | 0 |
| task-editor-new | 4 | 0 |
| task-editor-conditions（指标 disk_usage + 属性 last_execution） | 6 | 0 |
| task-editor-conditions-command（指标 command_result） | 5 | 0 |
| script-detail | 4 | 0 |
| execution-detail | 1 | 0 |
| 其余 22 个页面状态（含 application-detail，环境无应用数据报错） | 0 | 0 |
| **合计（含跨状态重复计数）** | **22** | **0** |

before 探针共确认 14 个**唯一**渲染输入（节点标签 2 + 任务 7 + 脚本 4 + 执行搜索 1）；另 5 个应用编辑器输入（`application-detail` 页）与 1 个 SearchToolbar 不在渲染路径（见 §8 未做项说明）。

### 1.3 新增测试（vitest 浏览器模式）

- `features/editors/editor-a11y-labels.test.tsx`：+3 条（脚本参数/环境变量、任务参数/条件（含动态切换指标与属性后断言 path/command/task_id）、应用上传/参数/环境变量）
- `features/shared/ui.test.tsx`：+1 条（SearchToolbar 以 placeholder 文案为名，且断言 `aria-label` 属性存在）
- `features/catalog/node-detail-backlinks.test.tsx`：+1 条（标签键/值输入）
- `features/executions/execution-detail-script-link.test.tsx`：+1 条（日志搜索输入）

---

## 2. Item 2：`select-dropdown.tsx` 死代码复核与删除

### 2.1 无引用证据（删除前，完整记录见 `followup/select-dropdown-deadcode-evidence.txt`）

1. `rg -n "select-dropdown" -S .`（排除 node_modules/dist/.git）→ **无匹配**
2. `rg -n "SelectDropdown" web/` → 仅该文件自身 3 行（定义处），无任何 import/调用
3. `npx knip --include files`（独立死代码检测）→ `src/components/select-dropdown.tsx` 列为 unused file
4. `git log` 该文件 → 仅初始提交 `5727cce feat: add Cadentra Hub-Agent platform`，从未被引用改动
5. 文件：62 行，sha256 `a804cfa1…ab571`；副本存证 `followup/deleted-select-dropdown.tsx.txt`

**结论：确为无调用方死代码，按授权删除**（该组件内 `SelectTrigger` 无可访问名的问题随文件删除一并消除）。

### 2.2 删除后确认

- `rg` 全仓 0 引用；`npx tsc -b` exit 0（无编译引用）；全量 vitest 49 files / 249 tests 通过
- `npx knip --include files` 不再命中该文件（0 匹配）

---

## 3. Item 3：深色主题全量扫描（25 页 × Chromium）

- 深色注入方式：`vite-ui-theme=dark` cookie（与 P11 相同），页面加载前生效
- 页面集：P11 的 25 个标准页面场景（登录 ×2、Dashboard、节点 ×2、任务 ×2、脚本 ×2、调度 ×3、应用、传输、审计、用户 ×2、设置、分组、执行 ×2、发布包 ×2、传输详情、删除确认框）

| 运行 | 页面数 | critical | serious | moderate | minor | 合计 |
|---|---:|---:|---:|---:|---:|---:|
| before（深色） | 25 | 0 | 0 | 0 | 0 | **0** |
| after（深色） | 25 | 0 | 0 | 0 | 0 | **0** |

浅色全量复扫（after）：Chromium 25/25 页 0 违规、Firefox 25/25 页 0 违规；before（浅色）亦为 0。**深色无需新增修复**，P11 的深色修复（徽标底色、错误态）在本批次全量复扫中保持 0 critical/serious。

> 覆盖差异说明：P11 深色曾覆盖 26 页（多 `application-detail`）。当前 repair 环境的 `applications` 列表已被并发批次清空（`GET /api/applications` 返回 `[]`），该页无法再次渲染扫描；本批次深色为 25 个标准页面全量，另 2 个当时错误页（`application-detail-delete-dialog`）本就不在标准集内。

---

## 4. Item 4：强制色彩 / 200% 缩放抽查（关键 5 页）

页面：dashboard、nodes-list、task-editor-new、script-detail、settings。

| 模式 | 页面 | axe 违规数 | 横向溢出 |
|---|---|---:|---|
| `forced-colors: active` | dashboard | 0 | 无 |
| `forced-colors: active` | nodes-list | 1（serious `color-contrast`） | 无 |
| `forced-colors: active` | task-editor-new | 1（同上） | 无 |
| `forced-colors: active` | script-detail | 1（同上） | 无 |
| `forced-colors: active` | settings | 1（同上） | 无 |
| 200% 缩放（720×450 视口近似） | 5 页全部 | **0** | 无（`scrollWidth == clientWidth`） |

**强制色彩 4 条违规的逐项核验结论：均为 axe 在强制色彩模拟下的假阳性，非真实用户问题。** 证据（以 nodes-list 为例）：

- axe 报告：`fg #f8fafc / bg #ffffff = 1.04:1`，目标为主按钮 `button[data-slot="button"]`
- 同页面同时刻实测：`getComputedStyle(button).color = rgb(0,0,0)`、`backgroundColor = rgb(255,255,255)`（强制色彩系统色，**21:1**），截图为白底黑字强制色彩渲染
- 根因：axe 4.14 经 Typed OM 取值 `computedStyleMap().get('color')` 返回作者色 `oklch(0.984 0.003 247.858)`（≈#f8fafc），未取到强制色彩替换值（`getComputedStyle` 同刻返回正确系统色）
- 代码侧核验：`rg forced-color` 无任何 `forced-color-adjust` 覆盖，作者样式未对抗强制色彩；强制色彩模式下配色由用户代理接管（WCAG 1.4.3 不适用），**无可修项，未做代码改动**

---

## 5. 修复后复扫 before/after 计数（汇总）

| 扫描 | before | after |
|---|---|---|
| 浅色 / Chromium（25 页） | 0 critical, 0 serious, 0 moderate, 0 minor | 0 / 0 / 0 / 0 |
| 浅色 / Firefox（25 页） | （P11 已 0，本批未跑 before） | 0 / 0 / 0 / 0 |
| 深色 / Chromium（25 页） | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| placeholder-only 输入（静态） | 20 | 0 |
| placeholder-only 输入（动态探针） | 22 条页面记录 | 0 条 |
| 死代码文件 `select-dropdown.tsx` | 存在（1 个无名 SelectTrigger） | 已删除，0 引用 |

（P11 基线对照：浅色两浏览器合计 critical 50 → 0、serious 204 → 0、moderate 822 → 0、minor 12 → 0；本批次在修复后状态上保持全零。）

---

## 6. 命令真实结果

| 命令 | 结果 | 证据 |
|---|---|---|
| `cd web && npx tsc -b` | **exit 0**（无输出） | `followup/final-tsc-lint.log` |
| `cd web && npm run lint` | **0 errors / 3 warnings**（均为既有警告：data-table React Compiler ×1、shared/ui react-refresh ×2；未新增） | 同上 |
| `cd web && npx vitest run --browser.headless` | **49 files / 249 tests 全部通过，exit 0**（11.2s；249 = P11 基线 243 + 本批新增 6） | `followup/final-vitest-full.log` |
| axe 复扫（浅色 Chromium + Firefox / 深色 Chromium） | 各 25/25 页 0 违规 | `followup/followup-axe-after-*.json` |
| 强制色彩 / 200% 缩放抽查 | 见 §4 | `followup/followup-contrast-zoom.json` |

**稳定性备注（如实记录）**：本主机 `chrome-headless-shell` 反复出现 compositor 崩溃（`traps: Compositor … int3`），导致首次全量 vitest 尝试出现随机文件加载失败（4 文件）与一次挂起；改用完整 Chromium 构建做扫描后，标准命令最终一次通过（日志：`final-vitest-first-attempt-flaky.log` 记录失败尝试，`final-vitest-full.log` 记录通过结果）。该现象与本批代码改动无关（失败文件每次随机、tsc 与逐文件测试均通过）。

---

## 7. 反向验证（撤掉修复 → 断言必须失败）

### 7.1 Item 1（8 例，逐项撤销 `aria-label` 后对应 vitest 断言均失败）

| # | 撤销项 | 对应断言 | 结果 |
|---|---|---|---|
| 1 | `scripts.paramName` 参数名 | 脚本编辑器参数/环境变量名 | ✅ 按预期失败 |
| 2 | `scripts.environment` 键 | 同上 | ✅ |
| 3 | `apps.artifact` 版本（上传表单） | 应用编辑器上传/参数/环境变量名 | ✅ |
| 4 | `tasks.localConditionValue` | 任务编辑器参数/条件名 | ✅ |
| 5 | `tasks.conditionTaskId` | 同上 | ✅ |
| 6 | `nodes.labelKey` | 节点详情标签键/值名 | ✅ |
| 7 | `common.searchLogs`（执行日志搜索） | 日志搜索输入名 | ✅（首轮未失败：该输入 aria-label 文案与 placeholder 相同，role 名称查询命中 placeholder 回退；已加强断言为同时校验 `aria-label` 属性，复验按预期失败） |
| 8 | SearchToolbar `aria-label={placeholder}` | 搜索输入名 | ✅（同上，已加强断言复验） |

完整日志：`followup/reverse-verification.log`（含 7/8 两例首轮 UNEXPECTED 与加强后的 PASS 记录，未隐藏）。

### 7.2 Item 2（死代码删除的反向确认）

1. 临时恢复 `select-dropdown.tsx` → `npx knip --include files` **再次报告该文件为 unused file**（1 命中）
2. 再次删除 → knip **0 命中**；`tsc -b` exit 0；全量 vitest 通过

---

## 8. 未做项与限制（如实说明）

1. **`application-detail` 页（应用编辑器 5 个输入）无实环境动态验证**：repair 环境的 `applications` 已被并发批次清空，页面无法渲染；该 5 处由静态枚举 + 新增 vitest 用例（点击「上传新发布包」展开上传表单后断言）覆盖，未做浏览器 axe 复扫。
2. **SearchToolbar 无生产调用方**：与 `select-dropdown` 同类的未使用组件（`rg SearchToolbar` 仅定义处）。本次按清单为其补名并加测试，**未删除**（不在本批授权删除范围）；建议后续批次统一清理（knip 同时报告的既有未使用文件还有 brand-icons、team-switcher 等，均未触碰）。
3. **Firefox 深色未扫描**：与 P11 一致（本批任务明确深色为 Chromium）；浅色 Firefox 已全量 0。
4. **人工屏幕阅读器验证未执行**：仍仅 axe + 可访问名断言覆盖（axe 只能覆盖约 1/3 的 WCAG 问题），NVDA/VoiceOver 实测建议后续补充。
5. **强制色彩 4 条 `color-contrast` 未“修复”**：核验为 axe 假阳性（§4），非代码问题；保留原始结果并说明，不强行改代码凑 0。
6. **未部署**：改动在 `web/src` 源码，`:18080` 环境仍运行旧构建；需另行构建部署后才能在环境上复验（本批无部署授权）。
7. **证据数据质量备注**：P11 证据目录的 `scan-summary.txt` 中「AFTER dark」段落记录为 2 条 moderate（landmark-one-main/page-has-heading-one，25 页），与同目录最终的 `axe-after-dark.json`（27 条记录、0 违规）及 `after-dark-final.log`（全 0）不一致——前者是 16:25 的中间运行快照，后者是 16:57 的最终重跑；P11 报告正文采用最终值（26 页 0 违规）。本批次未修改 P11 证据文件，仅在此备注。

---

## 9. 本次 `web/src` 变更清单（34 行新增、0 行删除、7 文件 + 1 文件删除）

```
src/features/catalog/index.tsx   +2   （标签键/值 aria-label）
src/features/editors/index.tsx   +9   （脚本参数×2、脚本环境变量×2、应用上传×2、应用参数×1、应用环境变量×2）
src/features/tasks/index.tsx     +7   （参数名称/默认值、本地条件值/路径/命令、任务 ID、远程条件值）
src/features/executions/index.tsx +1  （日志搜索）
src/features/shared/ui.tsx       +1   （SearchToolbar）
src/locales/zh.ts                +7   （common.key/value、nodes.labelKey/labelValue、tasks.conditionTaskId/localConditionValue/remoteConditionValue）
src/locales/en.ts                +7   （同上英文）
src/components/select-dropdown.tsx    整文件删除（62 行，死代码）
```

完整 patch：`followup/p17-web-src-changes.diff`；测试文件改动：4 个既有测试文件 +6 条用例（见 §1.3）。

---

## 10. 证据清单

```
.e2e/runs/20260930-101945/repair/qa-resume-20261018-a11y/followup/
├── followup-summary.txt                    本批汇总数据
├── followup-axe-before-light.json          修复前浅色（Chromium，25 页，0 违规）
├── followup-axe-before-dark.json           修复前深色（Chromium，25 页，0 违规）
├── followup-axe-after-light.json           修复后浅色（Chromium，25 页，0 违规）
├── followup-axe-after-light-firefox.json   修复后浅色（Firefox，25 页，0 违规）
├── followup-axe-after-dark.json            修复后深色（Chromium，25 页，0 违规）
├── followup-placeholder-before.json        placeholder-only 探针（before，22 条）
├── followup-placeholder-after.json         placeholder-only 探针（after，0 条）
├── placeholder-probe-before.log / placeholder-probe-after.log
├── followup-contrast-zoom.json             强制色彩 + 200% 缩放（5 页 ×2 模式）
├── contrast-zoom-scan.log
├── scan-before.log / scan-after.log        axe 扫描控制台记录
├── reverse-verification.log                反向验证（item 1 ×8 + item 2）
├── select-dropdown-deadcode-evidence.txt   删除前无引用证据
├── deleted-select-dropdown.tsx.txt         被删文件副本
├── p17-web-src-changes.diff                本次 web/src 变更 patch（+34/-0）
├── final-tsc-lint.log                      tsc exit 0 / lint 0 error 3 warning
├── final-vitest-full.log                   全量 vitest 通过（49/249）
├── final-vitest-first-attempt-flaky.log    首次全量尝试（主机浏览器崩溃导致的失败尝试，如实留档）
├── before-light/ before-dark/              修复前截图（各 25 张）
├── after-light/ after-light-firefox/ after-dark/  修复后截图（各 25 张）
└── contrast-zoom/                          强制色彩/缩放截图（10 张）
```

工具与脚本（未入仓）：`~/.cache/a11y-20261018/`（`followup-scan.mjs`、`followup-probe.mjs`、`followup-contrast-zoom.mjs`、axe 4.14.0）。

报告时间：2026-10-18（P17 followup）。
