# Web 侧剩余缺陷修复报告（web-rest-fix-20261017）

- 批次：`qa-resume-20261014-root` / Web 剩余缺陷修复（用户已授权「全部修复」）
- 执行者：OpenCode 子 Agent（资深前端工程师角色）
- 时间：2026-10-17（本地时间）
- 仓库：`/home/superc/code/nodesteer`，工作区 `/home/superc/code/nodesteer/web`
- 边界遵守：仅改 `web/**`；未改 Go；未改 `.e2e/**` 状态文件（仅新增本报告）；未执行任何 Git 操作；未安装依赖
- **前置说明**：本会话开始时 `web/src/**` 已存在此前批次的未提交改动（`git status` 显示 `web/src` 下 19 个已修改文件 + 若干未跟踪文件）。本报告只描述**本次新增**的改动，未回退、未覆盖任何既有改动。本次新增测试基线对比如下：修复前 34 文件 / 178 用例 → 修复后 37 文件 / 186 用例（新增 3 个测试文件、8 条用例）。

---

## 0. 结论速览

| # | 缺陷 | 判定 | 状态 |
|---|---|---|---|
| 1 | `G03-10` + `TABLE-TASKS-ERROR`（P1，FAIL）：Tasks 初始 Loading 反馈缺失，pending 期间按空数组显示「没有结果。」 | 确认缺陷（Loading / Empty / No Results 语义混淆） | 已修 + 回归测试 + 反向验证 |
| 2 | `P03-10`（P1，FAIL）：Dashboard 应用状态请求失败被空数组吞掉，无 Error/Retry 且健康比错误下降 | 确认缺陷（`catch(() => [])` 吞错 + 假 0 值） | 已修 + 回归测试 + 反向验证 |
| 3 | `P14P15-13`（PARTIAL）：应用详情 state 卡片未渲染 version / updated_at | 确认缺陷（API 已返回，UI 未渲染） | 已修 + 回归测试 + 反向验证 |

验证总表（真实执行结果，见 §5）：

| 检查 | 命令 | 结果 |
|---|---|---|
| 前端单测（vitest browser / Chromium） | `cd web && npx vitest run --browser.headless` | **37 文件 / 186 用例全部通过** |
| 类型检查 | `cd web && npx tsc -b` | **exit 0** |
| Lint | `cd web && npm run lint` | **0 error / 4 warning**（4 条均为改动前既有告警，与上一批基线一致） |
| Playwright E2E（`web/e2e`） | 未运行 | 需连接共享 repair 权威与凭证，属另一批次范围（见 §7） |

---

## 1. G03-10 / TABLE-TASKS-ERROR：Tasks 列表 Loading / Empty / No Results 三态混淆

### 根因

`web/src/features/tasks/index.tsx` 的 Tasks 列表只有 Error 与「其余」两个分支，pending 时把 `query.data || []` 交给 `DataTable`；`web/src/features/shared/data-table.tsx` 在 0 行时无条件渲染 `common.noResults`（「没有结果。」）。因此：

- 首次 GET `/api/tasks` 被挂起/未返回时，页面直接显示「没有结果。」（`TABLE-TASKS-ERROR` 证据：held 2000ms / 20 poll，0 skeleton / 0 loadingText）；
- 请求成功但列表为空（真空）时，也显示「没有结果。」，与「筛选无命中」语义混淆（`G03-10`）。

同源观察：`OBS-20261012-TASKS-INITIAL-LOADING-FEEDBACK`。

### 改动文件与行

| 文件 | 行 | 内容 |
|---|---|---|
| `web/src/features/tasks/index.tsx` | 272-289 | 三态分支：Error → Loading → 有数据 → 空态 |
| `web/src/features/tasks/index.tsx` | 53-66 | 引入 `EmptyState` |
| `web/src/features/shared/data-table.tsx` | 17 / 115 / 139 / 329-341 | 新增 `isLoading` prop，无数据且加载中时渲染加载占位而不是「没有结果」 |

### 关键 diff

```diff
--- a/web/src/features/tasks/index.tsx
+++ b/web/src/features/tasks/index.tsx
@@
       <Main className='flex flex-1 flex-col gap-6'>
         {query.isError ? (
           <ErrorState error={query.error} onRetry={() => query.refetch()} />
+        ) : query.isLoading ? (
+          <LoadingState />
+        ) : query.data?.length ? (
+          <DataTable
+            data={query.data}
+            columns={columns}
+            storageKey='tasks'
+            searchPlaceholder={t('tasks.searchPlaceholder')}
+          />
         ) : (
-          <DataTable
-            data={query.data || []}
-            columns={columns}
-            storageKey='tasks'
-            searchPlaceholder={t('tasks.searchPlaceholder')}
-          />
+          <Card>
+            <EmptyState message={t('common.noData')} />
+          </Card>
         )}
       </Main>
```

```diff
--- a/web/src/features/shared/data-table.tsx
+++ b/web/src/features/shared/data-table.tsx
@@
-            ) : (
+            ) : isLoading ? (
+              <TableRow>
+                <TableCell
+                  colSpan={table.getVisibleLeafColumns().length}
+                  className='h-24 text-center'
+                  aria-busy='true'
+                >
+                  <span className='inline-flex items-center gap-2 text-sm text-muted-foreground'>
+                    <Loader2 className='size-4 animate-spin' />
+                    {t('common.loading')}
+                  </span>
+                </TableCell>
+              </TableRow>
+            ) : (
               <TableRow>
                 <TableCell
                   colSpan={table.getVisibleLeafColumns().length}
                   className='h-24 text-center'
                 >
                   {t('common.noResults')}
                 </TableCell>
               </TableRow>
             )}
```

### 其它复用 DataTable 的表的回归核对（未改动，逐一确认已有三态）

| 表 | 位置 | 现状 |
|---|---|---|
| executions | `web/src/features/executions/index.tsx:270-276, 319-324` | Error → Loading（spinner）→ total=0 时 EmptyState → DataTable；DataTable 不会在 pending 时渲染 |
| transfers | `web/src/features/transfers/index.tsx:388-406` | Error → Loading → 有数据 DataTable → EmptyState；同上 |
| catalog（nodes/scripts/apps/artifacts 通用 TableSection） | `web/src/features/catalog/index.tsx:2338-2372` | Error → Loading（Skeleton）→ 有数据/有筛选 DataTable → `common.noData` EmptyState；同上 |

`DataTable` 新增的 `isLoading` 默认 `false`，对既有调用方是纯增量，不改变默认行为（37 文件 / 186 用例全绿，含 catalog/executions/transfers 既有测试）。

### 新增测试（能失败的断言）

`web/src/features/tasks/tasks-list-state.test.tsx`（新文件，3 用例）：

1. **初始请求未完成时显示 Loading，不显示「没有结果」；空结果只显示空态**
   - 挂起 GET `/tasks` → 断言 `加载中...` 可见、`没有结果。` 不可见；
   - resolve `[]` → 断言 `暂无数据` 可见、`没有结果。` 仍不可见。
2. **请求完成后为空列表显示空态，而不是「没有结果」**（mock 直接返回 `[]`）
3. **请求失败显示错误与重试入口，重试成功后恢复列表**（首次 reject → `role=alert` 文案 + 「重试」按钮；点击后 alert 消失、`qa-task` 行可见、请求次数 > 1）

`web/src/features/shared/data-table.test.tsx` 新增 `DataTable 加载态与空态区分`（2 用例）：

1. `isLoading` 且无数据 → `加载中...` 可见、`没有结果。` 不可见；
2. 非 loading 的空数据 → `没有结果。` 可见、`加载中...` 不可见（防止过度修复把真空态也变成加载态）。

### 反向验证（把修复撤掉看测试是否失败）

- 临时还原 `tasks/index.tsx` 为 `query.isError ? ... : DataTable data={query.data || []}` → 运行本文件：**2 failed / 1 passed**，失败点分别为 `Cannot find element with locator: page.getByText('加载中...')` 与 `page.getByText('暂无数据')`（与缺陷证据一致）；
- 临时移除 `data-table.tsx` 的 `isLoading` 分支 → 运行 data-table 测试：**1 failed / 10 passed**，失败点为 `Cannot find element with locator: page.getByText('加载中...')`。
- 两次均已恢复修复代码并复跑全绿。

### 对外可见行为变化

1. `/tasks` 首次加载（无缓存）不再显示「没有结果。」，改为加载指示「加载中...」（带旋转图标）；
2. `/tasks` 真空列表（GET 200 且 0 条）不再显示「没有结果。」，改为空态卡片「暂无数据」；
3. `/tasks` 搜索/筛选无命中仍显示「没有结果。」（表格内），语义与真空态分开；
4. 复用 `DataTable` 的其它表：默认行为不变；仅当调用方显式传 `isLoading` 时，空数据 + 加载中才显示加载占位。

---

## 2. P03-10：Dashboard 应用状态请求失败被吞、健康比错误下降

### 根因

`web/src/features/overview/index.tsx` 的 `loadDashboard()` 对每个 `/applications/{id}/state` 请求使用 `.catch(() => [])`：失败被吞成空数组，导致

- 失败应用的 health 计算为 `unknown`，健康汇总从真实值（如 `1/2`）降为 `0/2`（假 0 值）；
- 页面没有任何 Error/Retry 反馈（整个 dashboard query 成功，`query.isError` 为 false）。

证据：`.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch21/evidence/P03-10-app-state-fault.json`（fault 时 `alerts: 0`、`retry_buttons: 0`、`healthy_count_summary: "0/2"`，移除 fault 后 `1/2`）。

### 改动文件与行

| 文件 | 行 | 内容 |
|---|---|---|
| `web/src/features/overview/index.tsx` | 43-121 | `DashboardData` 增加 `applicationHealthAvailable` / `applicationHealthError`；新增 `ApplicationStatesResult`；`loadDashboard` 不再吞错，逐应用保留 `{ok, states}` / `{ok:false, error}` |
| `web/src/features/overview/index.tsx` | 322-330 | 健康比汇总：不可用时显示「健康状态不可用」，不显示 0/N |
| `web/src/features/overview/index.tsx` | 338-348 | 应用状态卡片顶部渲染 `ErrorState`（role=alert）+ 「重试」→ `query.refetch()` |
| `web/src/locales/zh.ts` | 190 | 新增 `dashboard.appsHealthUnavailable: '健康状态不可用'` |
| `web/src/locales/en.ts` | 193 | 新增 `dashboard.appsHealthUnavailable: 'App health unavailable'` |

### 关键 diff

```diff
--- a/web/src/features/overview/index.tsx
+++ b/web/src/features/overview/index.tsx
@@
   applications: Application[]
   applicationHealth: Record<string, string>
+  /** 应用状态接口是否全部成功；任一失败时健康比显示为不可用，避免显示假 0 值 */
+  applicationHealthAvailable: boolean
+  /** 首个应用状态请求错误，用于错误态文案与重试 */
+  applicationHealthError: unknown
   schedules: Schedule[]
@@
-  const applicationStates = await Promise.all(
-    applications.map((application) =>
-      api
-        .get<ApplicationNodeState[]>(`/applications/${application.id}/state`)
-        .catch(() => [])
-    )
-  )
+  const applicationStateResults = await Promise.all(
+    applications.map(
+      async (application): Promise<ApplicationStatesResult> => {
+        try {
+          return {
+            ok: true,
+            states: await api.get<ApplicationNodeState[]>(
+              `/applications/${application.id}/state`
+            ),
+          }
+        } catch (error) {
+          return { ok: false, error }
+        }
+      }
+    )
+  )
+  const applicationHealthAvailable = applicationStateResults.every(
+    (result) => result.ok
+  )
+  const applicationHealthError =
+    applicationStateResults.find((result) => !result.ok)?.error ?? null
   const applicationHealth = Object.fromEntries(
     applications.map((application, index) => {
-      const states = applicationStates[index]
-      const health = states.some((state) => state.health === 'healthy')
-        ? 'healthy'
-        : states.some((state) => state.health === 'unhealthy')
-          ? 'unhealthy'
-          : 'unknown'
+      const result = applicationStateResults[index]
+      // 状态请求失败的应用健康未知，不得当作不健康（否则健康比错误下降）
+      const health = !result.ok
+        ? 'unknown'
+        : result.states.some((state) => state.health === 'healthy')
+          ? 'healthy'
+          : result.states.some((state) => state.health === 'unhealthy')
+            ? 'unhealthy'
+            : 'unknown'
       return [application.id, health]
     })
   )
```

```diff
--- a/web/src/features/overview/index.tsx
+++ b/web/src/features/overview/index.tsx
@@
                   <span className='text-xs text-muted-foreground'>
-                    {data.healthyApplications}/{data.applications.length}{' '}
-                    {t('dashboard.appsHealthy').toLowerCase()}
+                    {data.applicationHealthAvailable
+                      ? `${data.healthyApplications}/${data.applications.length} ${t('dashboard.appsHealthy').toLowerCase()}`
+                      : t('dashboard.appsHealthUnavailable')}
                   </span>
@@
               <CardContent className='px-5'>
+                {!data.applicationHealthAvailable ? (
+                  <div className='mb-3'>
+                    <ErrorState
+                      error={data.applicationHealthError}
+                      onRetry={() => query.refetch()}
+                    />
+                  </div>
+                ) : null}
                 {data.applications.length === 0 ? (
```

### 设计说明（为什么不做整页 ErrorState）

canonical 期望是「Dashboard API 任一请求失败时 Error/Retry，不显示假数据」，同时本次修复要求「健康比不因失败而下降（显示未知/不可用）」。因此采用**局部错误态**：失败的数据源（应用状态）在对应卡片内显示错误与重试，健康比显示「不可用」，其余卡片不受影响；失败应用的徽标显示「未知」而非「不健康」。这样既满足「Error/Retry + 不显示假数据」，又保留健康比以「未知/不可用」呈现。

### 新增测试（能失败的断言）

`web/src/features/overview/dashboard-app-state.test.tsx`（新文件，2 用例）：

1. **应用状态失败显示错误与重试，健康比显示不可用而不是 0/N**
   - 2 个应用，其中 1 个 `/state` 首次 reject（`app state unavailable`）；
   - 断言 `role=alert` 含错误文案、「重试」按钮可见；
   - 断言 `main` 文本**不含 `0/2`**、含「健康状态不可用」、含「未知」、不含「不健康」；
   - 点击重试 → alert 消失、显示真实 `2/2 健康`、`/state` 请求次数 > 1。
2. **全部状态成功时仍显示真实健康比，不误报不可用**（1 healthy + 1 unhealthy → `1/2 健康`，无 alert、无重试按钮）。

### 反向验证

临时把 `loadDashboard` 还原为 `.catch(() => [])` 吞错版本 → 运行本文件：**1 failed / 1 passed**，失败点 `Cannot find element with locator: page.getByRole('alert')`（与 `P03-10` 证据「alerts: 0 / retry_buttons: 0」一致）；已恢复修复代码并复跑全绿。

### 对外可见行为变化

1. Dashboard「托管应用状态」卡片：应用状态接口失败时新增红色错误条（含具体错误文案）与「重试」按钮；
2. 健康比汇总在该失败场景下由假 `0/N 健康` 改为「健康状态不可用」；
3. 状态请求失败的应用徽标为「未知」（保持不把未知当不健康）；
4. 状态请求全部成功时，健康比与既有展示完全一致（回归用例 2 覆盖）。

---

## 3. P14P15-13：应用详情 state 卡片缺 version / updated_at

### 根因

`web/src/features/editors/index.tsx` 的「健康检查」卡片每行只渲染 `节点 + health 徽标 + (error || operation)`；API `GET /applications/{id}/state` 已返回 `version` 与 `updated_at`（`ApplicationNodeState`，`web/src/lib/api.ts:352-360`），但 UI 未渲染。证据：`appui-review-20261016.json` → `partials_kept["P14P15-13"] = "state 卡片未渲染 version/updated_at"`。

### 改动文件与行

| 文件 | 行 | 内容 |
|---|---|---|
| `web/src/features/editors/index.tsx` | 1763-1766 | 每个节点 state 行补渲染 `版本 {version} · <TimeValue value={updated_at} />` |

### 关键 diff

```diff
--- a/web/src/features/editors/index.tsx
+++ b/web/src/features/editors/index.tsx
@@
                         <span className='text-xs text-muted-foreground'>
                           {state.error || state.operation}
                         </span>
+                        <span className='text-xs text-muted-foreground'>
+                          {t('common.version')} {state.version || '-'} ·{' '}
+                          <TimeValue value={state.updated_at} />
+                        </span>
                       </div>
                     ))}
```

### 新增测试（能失败的断言）

`web/src/features/editors/application-state-fields.test.tsx`（新文件，1 用例）：mock `/applications/{id}/state` 返回 `version: '1.1'`、`updated_at: '2026-10-16T07:59:53.146Z'`，断言：

- 「健康检查」卡片文本含「版本 1.1」；
- 卡片内存在 `<time>` 元素且 `title` 含 `2026`（`TimeValue` 的完整时间）；
- 既有 `operation`（deploy）反馈保留。

### 反向验证

临时移除该 span → 运行本文件：**1 failed**，断言输出 `expected '健康检查状态节点健康deploy' to contain '版本 1.1'`（正好是 P14P15-13 记录的修复前卡片文本）；已恢复修复代码并复跑全绿。

### 对外可见行为变化

1. 应用详情「健康检查」卡片每个节点行新增一行元信息：`版本 <version> · <相对时间>`（悬浮显示完整本地时间）；
2. `version` 缺省时显示 `版本 -`；`updated_at` 为 Go 零值时 `TimeValue` 显示 `-`（既有约定）；
3. 节点名、健康徽标、operation/error 行不变（既有 E2E 对 `deploy`/`SUCCESS`/徽标的断言不受影响）。

---

## 4. 改动清单（本次新增，仅 `web/**`）

| 文件 | 类型 | 说明 |
|---|---|---|
| `web/src/features/tasks/index.tsx` | 修改 | Tasks 三态分支 + `EmptyState` 引入 |
| `web/src/features/shared/data-table.tsx` | 修改 | `isLoading` prop + 加载占位行 + `Loader2` 引入 |
| `web/src/features/overview/index.tsx` | 修改 | 应用状态不吞错、可用性标记、错误/重试、健康比不可用 |
| `web/src/features/editors/index.tsx` | 修改 | state 行补 version / updated_at |
| `web/src/locales/zh.ts` / `web/src/locales/en.ts` | 修改 | 新增 `dashboard.appsHealthUnavailable` |
| `web/src/features/tasks/tasks-list-state.test.tsx` | 新增 | Tasks 三态回归测试（3 用例） |
| `web/src/features/overview/dashboard-app-state.test.tsx` | 新增 | Dashboard 应用状态失败/成功测试（2 用例） |
| `web/src/features/editors/application-state-fields.test.tsx` | 新增 | state 卡片字段测试（1 用例） |
| `web/src/features/shared/data-table.test.tsx` | 修改 | 新增加载态与空态区分（2 用例） |

---

## 5. 验证记录（真实执行）

### 5.1 类型检查

```text
$ cd web && npx tsc -b
（无输出）
exit 0
```

### 5.2 Lint

```text
$ cd web && npm run lint
✖ 4 problems (0 errors, 4 warnings)
```

4 条告警均为改动前既有、与本次改动无关：

- `src/components/data-table/pagination.tsx:20` react-refresh/only-export-components
- `src/features/shared/data-table.tsx:190` react-hooks/incompatible-library（`useReactTable`，行号随既有改动位移）
- `src/features/shared/ui.tsx:545,551` react-refresh/only-export-components

### 5.3 单测（vitest browser / headless Chromium）

```text
$ cd web && npx vitest run --browser.headless
 Test Files  37 passed (37)
      Tests  186 passed (186)
   Duration  10.12s
```

新增/受影响文件定向复跑：

```text
$ npx vitest run --browser.headless \
    src/features/shared/data-table.test.tsx \
    src/features/tasks/tasks-list-state.test.tsx \
    src/features/overview/dashboard-app-state.test.tsx \
    src/features/editors/application-state-fields.test.tsx
 Test Files  4 passed (4)
      Tests  17 passed (17)
```

### 5.4 反向验证汇总（撤掉修复 → 测试失败 → 恢复 → 全绿）

| 缺陷 | 撤掉内容 | 结果 |
|---|---|---|
| 1 | `tasks/index.tsx` 三态分支还原 | 2 failed / 1 passed（找不到「加载中...」「暂无数据」） |
| 1 | `data-table.tsx` `isLoading` 分支移除 | 1 failed / 10 passed（找不到「加载中...」） |
| 2 | `loadDashboard` 还原 `.catch(() => [])` | 1 failed / 1 passed（找不到 `role=alert`） |
| 3 | `editors/index.tsx` 移除 version/updated_at 行 | 1 failed（卡片文本不含「版本 1.1」） |

恢复后已复跑：`tsc -b` exit 0、4 文件 17 用例通过、全量 37 文件 186 用例通过。

---

## 6. 未做项 / 剩余风险

1. **Playwright E2E 未运行**：`web/e2e/**` 需连接共享 repair 权威与真实凭证（另一批次范围）；本次以 vitest browser 用例覆盖缺陷行为断言，未声称 E2E 通过。特别地：
   - `web/e2e/tests/gap-ui.spec.ts` 的 `GAP-16` 对 `tasks` 模块要求「空数据集显示『暂无数据』且不含『没有结果。』」，本次修复后该模块语义满足（真空 → `暂无数据`）；
   - 该 spec 的 Loading 观察（held GET）对应「加载中...」指示，可由后续真实链路复测确认。
2. **未执行任何 Git 操作**：未 commit / add / stash，工作区改动（含本报告）全部留在本地。
3. **未改 Go / `.e2e` 状态文件 / 未安装依赖**（遵守边界）。
4. **剩余风险（低）**：
   - Dashboard 局部错误态依赖 `refetchInterval: 15000` 自动重试 + 手动「重试」按钮；极端情况下 15s 内自动恢复，属预期行为；
   - `DataTable.isLoading` 为可选增量能力，当前只有 Tasks 通过页面级分支切换三态，其它表沿用既有分支（未引入回归，全量测试为证）；
   - `state.version` 缺省显示 `-`，若后端后续返回空字符串同样显示 `-`（与 `TimeValue` 的零值约定一致）。
