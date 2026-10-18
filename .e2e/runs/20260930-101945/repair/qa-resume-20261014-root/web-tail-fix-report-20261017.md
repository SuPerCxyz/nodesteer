# Web 尾部修复批（T3）交接报告

> 说明：T3 子 Agent 会话在收尾阶段因 **API key 过期**中断（2026-10-18 00:38），未留下自述报告。
> 本文件由主 Agent 依据**工作树实际改动 + 测试结果**重建，并记录主 Agent 接手的修补。

## 一、T3 实际落地的改动（已由主 Agent 逐项核验）

| # | 缺口 | 实现位置（核验点） | 状态 |
|---|---|---|---|
| 1 | `P09P11-12` + `G5-STALE-PAYLOAD-FIELDS`：任务保存 payload 剔除无关字段 | `web/src/lib/task-payload.ts`（`switch(form.type)` 按类型只带 `command`/`script_id`/`application_id`；`normalizeTarget` 按目标类型只带对应字段）+ `task-payload.test.ts` | ✅ 已实现 |
| 2 | `EXECUTION-SCRIPT-BACKLINK-GAP`：执行详情脚本回链 | `web/src/features/executions/index.tsx:539-546`（`exec.script_id` → `/scripts/{id}` 链接）+ `execution-detail-script-link.test.tsx` | ✅ 已实现 |
| 3 | `TRANSFER-STATUS-BADGE-NOT-LOCALIZED` | `web/src/lib/status-labels.ts`（新增 `uploading/delivering/…` 等 zh/en 词表，28 条） | ✅ 已实现 |
| 4 | `STATUSBADGE-MAINTENANCE-DISABLED-NO-ZH` | 同上（`maintenance/disabled` 中文） | ✅ 已实现 |
| 5 | `INVALID-TASK-DEEPLINK-NO-ERROR` | 任务编辑器 404 分支 + `schedule-invalid-task-deeplink.test.tsx` | ✅ 已实现 |
| 6 | `NAV-ROLE-UNFILTERED` | `app-sidebar.tsx` / `sidebar-data.ts` 角色过滤 + `sidebar-role-filter.test.tsx` | ✅ 已实现 |
| 7 | `CROSSPAGE-LINK-GAPS`（5 处） | ① 审计资源 ID：`catalog/index.tsx:2059-2085`（`auditResourcePath` + `<a href>`，无路由时回退纯文本）②③④ 详情路由：`/artifacts/{id}`、`/users/{id}`、`/transfers/{id}`（`:1024-1028` + 详情页）⑤ 节点详情回链：任务 `:831`、调度 `:864`、应用 `:895`；配套 3 个测试文件 | ✅ 已实现 |
| 8 | `G5-AND-CONDITION-NO-SUMMARY-NO-CLEAR` | `tasks/index.tsx`（`conditionItemText`/`conditionSummaryText` + `data-testid='condition-summary'` + 清除按钮）+ zh/en 文案 + `task-condition-summary.test.tsx` | ✅ 已实现（**崩溃由主 Agent 修，见下**） |
| 9 | `PAGESIZE-PERSISTED-OUT-OF-RANGE-BLANK` | `web/src/lib/table-view.ts:39-43`（按 `PAGE_SIZE_OPTIONS` 白名单 + 有限数校验，越界回退） | ✅ 已实现 |
| 10 | `SCHEDULE-INTERVAL-ZERO` / `PAST-ONETIME`（前端） | `editors/index.tsx` 内联错误 + 保存禁用 + `schedule-validation.test.tsx` | ✅ 已实现 |
| 11 | `P17P18-04-SYSTEM-TRIGGER-NO-SOURCE` | `executions/index.tsx:116-124`：筛选枚举改为「仅预置真实可达类型（manual/schedule）+ 观测数据」；**保留** `system` 标签映射做前向兼容（与 T1 判定一致） | ✅ 已实现 |

**统计**：T3 触及 `web/src` 下 28 个既有文件 + 新增 27 个测试文件（含 `__screenshots__`）。

## 二、主 Agent 接手修补（T3 中断后的必要修复）

| 问题 | 根因 | 修补 |
|---|---|---|
| **任务编辑器整页崩溃**（`task-condition-summary.test.tsx` 2 用例失败） | T3 将 `{node.status}` 改为 `statusLabel(node.status)`，而 `statusLabel` 不容忍 `undefined` → 节点缺 `status` 字段（如部分数据/接口裁剪）时 `toLowerCase` 抛错，React 卸载整棵树 | `web/src/lib/status-labels.ts`：签名放宽为 `string \| null \| undefined`，空值返回 `'-'`；`web/src/features/shared/ui.tsx` `StatusBadge` 同步防御（`(status ?? '')`） |
| **`/api/me` 键名变更的 Web 消费方未同步**（T2 标注的必须项，不同步会导致登录态解析失败） | Go 侧已改 `token/user_id/username/role/expires` | `web/src/lib/api.ts` `SessionInfo` 接口、`web/src/stores/auth-store.ts` `sessionToUser`、`web/src/features/catalog/index.tsx:2165`、2 个测试文件的构造数据同步为 snake_case |

## 三、验证（主 Agent 真实执行）

| 检查 | 结果 |
|---|---|
| `npx tsc -b` | exit 0 |
| `npm run lint` | 0 error / 3 warning（均为改动前既有告警） |
| `npx vitest run --browser.headless` | **47 文件 / 222 用例全部通过** |
| 定向复跑 `task-condition-summary.test.tsx` | 修补前 2 失败 → 修补后 **2 通过** |
| 残留 PascalCase 扫描 | 无残留 |

## 四、未做项与说明

- **Playwright E2E（`web/e2e`）未运行**：指向共享 repair 权威，由最终部署验证批覆盖。
- **运行时验证**：本批结论基于真实 Chromium 的 vitest browser 验证；端到端 UI 行为在最终部署批复验。
- **未做 Git 操作**；未改 Go 源码、配置、`.e2e/**` 状态文件。
- **harness 事件**：T3 会话中断由 API key 过期导致（平台层，非任务失败）；工作树改动完整保留并已核验，无半成品残留（唯一半成品即上述 `statusLabel` 崩溃，已修）。
