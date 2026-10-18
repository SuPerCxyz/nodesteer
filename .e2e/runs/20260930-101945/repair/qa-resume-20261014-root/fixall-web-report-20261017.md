# Web 侧缺陷修复报告（fixall-web-20261017）

- 批次：`qa-resume-20261014-root` / Web 修复（用户授权「全部修复」）
- 执行者：OpenCode 子 Agent（资深前端工程师角色）
- 时间：2026-10-17（本地时间）
- 仓库：`/home/superc/code/nodesteer`，分支工作区 `web/**`
- 边界遵守：仅改 `web/**`；未改 Go 源码；未改 `.e2e/**` 状态文件（仅新增本报告）；未执行任何 Git 操作；未安装依赖
- **重要前置说明**：本会话开始时 `web/src/**` 已存在**未提交改动**（`git status` 显示 `web/src` 下 19 个已修改文件 + 若干未跟踪文件，来自此前批次）。本报告只描述**本次新增**的改动；未回退、未覆盖任何既有改动。

---

## 0. 结论速览

| # | 缺陷 | 判定 | 状态 |
|---|---|---|---|
| 1 | `WEB-NODE-SELECTION-DRIFT-20261017`（P1） | 确认缺陷（TanStack 默认按行索引做 selection 键） | 已修 + 回归测试 |
| 2 | `E2E-20261016-EDITOR-PENDING-NOT-DISABLED-DUPLICATE-POST`（P2） | 确认缺陷（4 个编辑器保存按钮 pending 期间未禁用） | 已修 + 回归测试 |
| 3 | `WEB-SKIP-TO-MAIN-DEAD-ANCHOR-20261017`（P3） | 确认缺陷（`#content` 在 DOM 中不存在） | 已修 + 回归测试 |
| 4 | `E2E-20261016-NARROW-VIEWPORT-LONG-VALUE-OVERFLOW`（P2） | 确认缺陷（长值撑破页面） | 已修 + 回归测试 |
| 5 | `OBS-20261010-UI-LOGOUT-NOT-REVOKED`（P1） | 确认缺陷（只清本地，不吊销服务端会话） | 已修 + 回归测试 |
| 6 | `OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST`（待复核） | **不是产品缺陷**：保存路径没有脏数据判定，必然发 PUT；batch07 的「无请求」可复现为 **sticky header 遮挡点击**（harness 点击投递问题） | 已复核，未改产品逻辑（仅顺带补 pending 禁用，见 #2） |

验证总表（真实执行结果，见 §7）：

| 检查 | 命令 | 结果 |
|---|---|---|
| 前端单测（vitest browser / Chromium） | `npx vitest run --browser.headless` | **34 文件 / 178 用例全部通过** |
| 类型检查（仓库等价） | `npx tsc -b` | **exit 0** |
| 类型检查（任务指定） | `npx tsc --noEmit` | exit 0（说明：根 `tsconfig.json` 是 solution 式 `files: [] + references`，该命令实际不检查子项目，等价检查是 `tsc -b`） |
| Lint | `npm run lint` | **0 error / 4 warning**（4 条均为改动前既有告警，数量与基线一致） |
| Playwright E2E（`web/e2e`） | 未运行 | 需连接共享 repair 权威 + 凭证，属另一批次范围（见 §8） |

---

## 1. WEB-NODE-SELECTION-DRIFT-20261017（P1）

### 根因
`web/src/features/shared/data-table.tsx` 调用 `useReactTable` 时未传 `getRowId`，TanStack Table 默认用**行索引**生成 `row.id`，而 `rowSelection` 状态键就是 `row.id`。因此：

- 同页数据重排后，勾选会跟着「位置」而不是跟着「节点」；
- 服务端翻页时，新一页同位置的行会被判为「已勾选」；
- 批量操作（`getFilteredSelectedRowModel().rows.map(row => row.original)`）因此可能命中**错误节点**。

文件第 124 行注释「选择按行 id 跨分页/排序保留」与实际实现不符（注释描述的是期望行为）。

### 改动
`web/src/features/shared/data-table.tsx`

- 新增模块级 `resolveRowId(row, index)`（:83-104）：优先取行对象 `id`（字符串/数字），无 `id` 时用 `WeakMap` 按**对象身份**分配稳定键（同一对象实例跨渲染稳定，不再退回索引），非对象行才退回索引；
- 新增可选 prop `getRowId?: (row: TData, index: number) => string`（:151）；
- 接入表格（:208-209）。

```diff
+const identityKeys = new WeakMap<object, string>()
+let identitySeq = 0
+
+/** 行选择键：优先取行对象的稳定 id（字符串/数字），否则按对象身份兜底 */
+function resolveRowId(row: unknown, index: number): string {
+  if (row && typeof row === 'object') {
+    const candidate = (row as { id?: unknown }).id
+    if (typeof candidate === 'string' && candidate) return candidate
+    if (typeof candidate === 'number' && Number.isFinite(candidate))
+      return String(candidate)
+    const existing = identityKeys.get(row)
+    if (existing) return existing
+    const generated = `identity-${++identitySeq}`
+    identityKeys.set(row, generated)
+    return generated
+  }
+  return `index-${index}`
+}
+
     onRowSelectionChange: setRowSelection,
     enableRowSelection,
+    // 显式提供 getRowId：TanStack 默认按行索引生成 selection 键，跨页/排序会漂移到别的行
+    getRowId: getRowId ?? resolveRowId,
```

现状核查（各复用表实际数据结构的 `id` 字段）：`Node.id`、`Task.id`、`Execution.id`、`Transfer.id`（`web/src/lib/api.ts:129/252/370`）均为稳定字符串 `id`，默认实现直接命中；`getRowId` 作为可配置项保留给未来字段名不同的表。当前启用多选的只有节点列表（`web/src/features/catalog/index.tsx:301`，客户端分页，`data` 为全量，选择跨页保留语义成立）。批量操作取值方式**未改动**，已是 `row.original`（`catalog/index.tsx:303-308`）。

### 验证
`web/src/features/shared/data-table.test.tsx` 新增 `describe('DataTable 行选择按行 id 而非行位置')`（:117-238），3 个用例：

1. 同页数据重排（node-a 从位置 0 → 位置 1）后：位置 0 未勾选、位置 1（node-a）仍勾选，批量回调收到 `['a']`；
2. 翻页（pageA → pageB，同位置不同行）：新页无勾选、工具条不出现、批量回调收到 `[]`；返回原页后 node-a 仍勾选；
3. 传入自定义 `getRowId` 时按自定义键工作。

真实结果：

- 有修复：`data-table.test.tsx` 9 passed；
- **反向验证**：把 `getRowId: getRowId ?? resolveRowId` 临时改回 `getRowId: getRowId`（即退回索引键）后重跑，**2 个用例失败**（`not.toBeChecked()` 超时 —— 位置 0 被误判为已勾选），随后已恢复。

### 对外可见的行为变化
- 勾选不再随行位置漂移；同页重排/翻页返回后按节点 id 保留选择。
- 服务端分页表格（当前未启用多选）若将来启用多选，其他页的选择不会被错误应用到当前页行上（只会影响 `rowSelection` 命中范围，不影响现有页面行为）。
- 新增可选 prop `getRowId`（对现有调用方零影响）。

---

## 2. E2E-20261016-EDITOR-PENDING-NOT-DISABLED-DUPLICATE-POST（P2）

### 根因
`web/src/features/editors/index.tsx` 中 **Schedule / Script / Group / Application** 四个编辑器的 `save()` 没有 pending 门闩，保存按钮也没有 `disabled`：请求在途期间按钮仍可点击 → 发出第二次 POST/PUT。任务编辑器（`web/src/features/tasks/index.tsx:632-654、1361-1364`）已有 `savingRef + saving + disabled + aria-busy + Loader2` 参照实现，本批照做。

证据（既有）：`.e2e/runs/.../qa-resume-20261014-web/p-table/evidence/G04-01-pending-submit.json` —— POST 被 hold 期间第二次点击，`post_count: 2`、`pending_button.disabled: false`。

### 改动
`web/src/features/editors/index.tsx`（四处同构，均在 `save()` 入口加门闩、`finally` 复位）

| 编辑器 | 状态/门闩 | 保存按钮 |
|---|---|---|
| ScheduleEditor | :110-111（`saving`/`savingRef`），:113 `if (savingRef.current) return`，:143 置位，:152 `finally` 复位 | :379-381 |
| ScriptEditor | :441-442、:444、:450、:459 | :739-741 |
| GroupEditor | :810-811、:813、:819、:828 | :948-950 |
| ApplicationEditor | :1108-1109、:1111、:1117、:1142 | :1651-1653 |

关键 diff（以调度编辑器为例，其余三处同形）：

```diff
   const setForm = (next: Partial<Schedule>) => setDraft(next)
+  // 保存期间禁用按钮：避免重复点击发出第二次 POST/PUT（参照任务编辑器）
+  const [saving, setSaving] = useState(false)
+  const savingRef = useRef(false)
   const save = async () => {
+    if (savingRef.current) return
     setError('')
     ...（原有校验不变，校验失败仍提前 return）...
     const payload = buildSchedulePayload(form)
+    savingRef.current = true
+    setSaving(true)
     try {
       ...
     } catch (cause) {
       setError(cause instanceof Error ? cause.message : t('common.noData'))
+    } finally {
+      savingRef.current = false
+      setSaving(false)
     }
   }
...
-              <Button onClick={save}>{t('common.save')}</Button>
+              <Button onClick={save} disabled={saving} aria-busy={saving}>
+                {saving ? <Loader2 className='animate-spin' /> : null}
+                {t('common.save')}
+              </Button>
```

校验顺序保持原样：**先校验、后置位门闩**（与任务编辑器一致），校验失败不会把按钮卡在禁用态。错误展示仍是各编辑器原有的内联 `ErrorState`（未改成 toast，避免超出缺陷范围）。

### 验证
新增 `web/src/features/editors/save-pending.test.tsx`（4 个用例，覆盖 4 个编辑器）：mock 的保存请求返回永不 resolve 的 Promise，点击保存后断言

- `expect(save).toBeDisabled()`、`toHaveAttribute('aria-busy', 'true')`；
- 对已禁用按钮直接调用 DOM `click()`（浏览器不会派发事件）后，`put`/`post` 仍为 **1 次**；
- 请求体断言（`/schedules/sch-1`、`/scripts/sc-1`、`/groups/gr-1`、`/applications/app-1` + 关键字段）。

真实结果：4 passed。**反向验证**：把调度编辑器按钮临时改回 `<Button onClick={save}>` 后，该用例失败（`toBeDisabled()` 超时），随后恢复。

### 对外可见的行为变化
- 四个编辑器的「保存」在请求在途时变为禁用态，并显示旋转图标 + `aria-busy=true`；重复点击不再发第二次请求。
- 请求失败后按钮恢复可用（`finally` 复位），错误仍以原有内联样式展示。

---

## 3. WEB-SKIP-TO-MAIN-DEAD-ANCHOR-20261017（P3）

### 根因
`web/src/components/skip-to-main.tsx:5` 指向 `#content`，但 DOM 中没有任何元素带该 id（布局里 `SidebarInset` 是裸 div，页面内容用 `Main` 组件，也没有 id）。键盘用户激活跳转链接后：不滚动、焦点不动，跳转链接实际无效。

### 改动
`web/src/components/layout/authenticated-layout.tsx:120-123`（选择 `SidebarInset` 而不是 `Main`：`Main` 并非所有已认证页面都渲染，例如 `features/users`；`SidebarInset` 是已认证布局里恒存在的正文容器）

```diff
         <SidebarInset
+          // 跳转链接 #content 的落点：可聚焦并随之后续 Tab 顺序进入正文
+          id='content'
+          tabIndex={-1}
           className={cn(
```

`tabIndex={-1}` 让激活锚点后焦点真正落到容器上（而不是仅设置「顺序焦点导航起点」），后续 Tab 从正文开始。

### 验证
新增 `web/src/components/layout/authenticated-layout.test.tsx`（mock `/api/me` 与会话 store，渲染真实 `AuthenticatedLayout`）：

- 断言 `document.getElementById('content')` 存在、`tabindex="-1"`、包含正文节点；
- 用 `userEvent.click` 激活「Skip to Main」链接后断言 `document.activeElement === target`（焦点确实移动）。

真实结果：1 passed。

### 对外可见的行为变化
- 键盘用户激活「Skip to Main」后页面滚动到正文并把焦点移入正文容器；容器获得焦点时浏览器可能绘制焦点环（未用 CSS 隐藏，保留可感知焦点）。

---

## 4. E2E-20261016-NARROW-VIEWPORT-LONG-VALUE-OVERFLOW（P2）

### 根因
窄屏（480px）下长值把**页面级**横向滚动撑破（`docScrollW > innerW`）：

1. **调度编辑器**：任务选择框是 Radix `SelectTrigger`，基类为 `w-fit` + `whitespace-nowrap` 且无 `max-width`，容器又是 `grid`/flex，长任务名把触发器撑到 898px，进而顶破页面（X20-attribution.json：`trigger_w: 898`、`docScrollW: 939`、`innerW: 480`）。
2. **脚本编辑器**：参数/环境变量行的长值（长参数名、长默认值、长环境变量）是不可收缩的 flex item（`min-width: auto`，内容又是不可断行的长 token），把所在 grid 轨道撑宽；单列 grid 的所有卡片随之变宽，整页横向溢出（X20-attribution2.json：`docScrollW: 676 > innerW: 480`，卡片 660、名称 label/input 610 —— 即被参数卡片撑宽的轨道传递到首张卡片）。

### 改动

`web/src/features/editors/index.tsx`

- 编辑器内 10 个表单 `SelectTrigger` 统一改为 `className='w-full min-w-0'`（:189、222、317、334、355、537、623、885、1311、1570；部署操作那个 `w-40` 的选择框保持原样）。与任务编辑器现有做法一致（`tasks/index.tsx` 用 `w-full`）。`min-w-0` 让触发器的最小贡献为 0，轨道可收缩；触发器内 `SelectValue` 已有 `line-clamp-1`（`overflow:hidden`），长文本在容器内截断。
- 参数行、环境变量行（脚本编辑器 :652、:656、:715）与应用编辑器的参数/环境行（:1487、:1537）加 `min-w-0 break-all`，长 token 在行内换行而不是撑宽容器：

```diff
-                <span className='font-mono'>{item.name}</span>
+                {/* 长参数名/默认值在窄屏内换行，不撑破页面 */}
+                <span className='min-w-0 break-all font-mono'>{item.name}</span>
-                <span className='text-muted-foreground'>{item.type}</span>
+                <span className='shrink-0 text-muted-foreground'>{item.type}</span>
-                <span className='text-muted-foreground'>
+                <span className='min-w-0 break-all text-muted-foreground'>
```

`web/src/components/ui/textarea.tsx:9-10`：基类加 `min-w-0`（`Input` 已有同样处理），避免 `field-sizing-content` 让 textarea 按内容撑宽顶破容器（防御性加固；见下方验证说明）。

### 验证
新增 `web/src/features/editors/narrow-viewport.test.tsx`（视口 480×800，加载真实样式表，用 `SidebarInset` 复现真实布局）：

1. 调度编辑器 `/schedules/sch-1`（任务名 63 字超长中文+英文）：选择框宽度 ≤ `main` 宽度，且 `documentElement.scrollWidth <= clientWidth`；
2. 脚本编辑器 `/scripts/sc-1`（内容 320 字符单行 + 126 字符参数名 + 200 字符默认值 + 88 字符环境变量 key）：页面无横向溢出，且这三段长文本所在元素宽度均 ≤ `main` 宽度。

真实结果：2 passed。**反向验证**（逐项定位驱动元素）：

- 去掉参数/环境行 `min-w-0 break-all` → 脚本用例失败：`expected 2546 to be less than or equal to 480`（证明这两行是实际驱动元素）；
- 仅去掉 `Textarea` 的 `min-w-0` → 脚本用例**仍通过**（当前 Chromium 下 `field-sizing-content` 未在 `w-full` 场景撑宽）。即：Textarea 改动是加固，不是本次溢出的必需项，报告如实标注；
- 去掉 `SelectTrigger` 的 `w-full min-w-0`（10 处还原为 `w-fit`）→ 调度用例失败：`expected 660.546875 to be less than or equal to 480`（触发器被长任务名撑到 660px > 容器 480px）。

### 对外可见的行为变化
- 调度/脚本/分组/应用编辑器中的下拉选择框现在**撑满所在列**（原先按内容宽度自适应），与任务编辑器一致；长值在框内以省略号截断。
- 长参数名/参数默认值/环境变量/应用参数在窄屏内**换行**显示（原先撑破页面）。
- 共享 `Textarea` 不再因内容变宽（正常场景无可见差异）。

---

## 5. OBS-20261010-UI-LOGOUT-NOT-REVOKED（P1）

### 根因
`web/src/components/sign-out-dialog.tsx` 的 `handleSignOut` 只做 `auth.reset()`（清 zustand + localStorage token）再跳转 `/sign-in`，从不调用服务端注销接口；而服务端 `internal/hub/api/api.go:447-459` 的 `/api/logout` 已收紧为 **POST-only**（非 POST 返回 405），因此旧 token 在服务端仍然有效。

### 改动
`web/src/components/sign-out-dialog.tsx`（:17-44）：先 `await api.post('/logout')`，成功后再清本地状态并跳转；失败时给出可理解反馈（toast）但**仍清本地并跳转**（不允许吊销失败把用户卡在已登录界面）。`ConfirmDialog` 传 `isLoading={pending}`，请求在途时禁用确认/取消，避免重复注销。

```diff
-  const handleSignOut = () => {
+  const handleSignOut = async () => {
+    if (pending) return
+    setPending(true)
+    // 先请服务端吊销会话（POST /api/logout），再清本地状态并跳转；
+    // 吊销失败也必须让本机退出，只提示用户会话可能仍在服务端有效。
+    let revoked = true
+    try {
+      await api.post('/logout')
+    } catch (error) {
+      revoked = false
+      toast.error(t('login.signOutRevokeFailed'), {
+        description: error instanceof Error ? error.message : undefined,
+      })
+    }
     auth.reset()
     const currentPath = location.href
+    setPending(false)
     navigate({ to: '/sign-in', search: { redirect: currentPath }, replace: true })
+    if (revoked) toast.success(t('login.signOutRevoked'))
   }
...
       destructive
+      isLoading={pending}
       handleConfirm={handleSignOut}
```

`web/src/locales/zh.ts:167-168` / `web/src/locales/en.ts:170-172` 新增两条文案：`login.signOutRevoked`、`login.signOutRevokeFailed`。
（两个退出入口 `nav-user.tsx:122` 与 `profile-dropdown.tsx:72` 都复用同一个 `SignOutDialog`，无需重复修改。）

### 验证
`web/src/components/sign-out-dialog.test.tsx` 重写为 4 个用例（mock `api.post` / `auth.reset` / `navigate` / `sonner`）：

1. **调用顺序**：点击「退出登录」后 `calls == ['post', 'reset', 'navigate']`，`post` 参数为 `'/logout'`，跳转参数与原来一致，且未报错；
2. **吊销失败**：`post` reject（HTTP 500）时仍 `['post','reset','navigate']`，并调用 `toast.error(login.signOutRevokeFailed)`；
3. **在途禁用**：`post` 挂起时确认按钮 `toBeDisabled()`，对已禁用按钮再 `click()` 后 `post` 仍为 1 次、`reset` 未调用；
4. 点「取消」不发请求、不重置、不跳转。

真实结果：4 passed。

### 对外可见的行为变化
- 退出登录现在会真正吊销服务端会话（`POST /api/logout`，带当前 Bearer token）。
- 成功后提示「已退出登录」；失败提示「服务端会话未能注销，已在本机退出登录。」（附服务端错误信息），两种情况都会退出到登录页。
- 退出确认框在请求在途时按钮禁用。

---

## 6. OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST（待复核）→ 复核判定：**非产品缺陷**

### 复核方法（只读 + 可复现实验）

1. **读代码（提交条件/校验/脏数据判定）**：`web/src/features/editors/index.tsx` ApplicationEditor 的 `save()`（改前 :1066-1095）**没有任何脏数据判定**；唯一提前返回是 `if (!form.name)`（置内联 `ErrorState`，不发请求）。`保存` 按钮的 `onClick={save}` 无条件绑定（`canWrite` 为真时渲染）。
2. **写复现测试**：新增 `web/src/features/editors/application-save-request.test.tsx`，用「与 API 创建路径一致」的应用定义（`name/version/artifact_id/binary_path/unit_name`，即 batch07 的 `POST /api/applications` 形态）渲染编辑页，**不做任何修改**直接点保存：

   - 用例 1 通过：`put` 被调用 1 次，参数 `('/applications/app-api', {name:'qa-api-created-app', version:'1.0.0', artifact_id:'art-1', binary_path:'/usr/local/bin/b07', unit_name:'b07-app.service', ...})`，未产生多余 assign 请求 → **「未改动则不发请求」不是本实现的行为**；
   - 用例 2 通过：清空名称后点保存 → 不发任何请求，显示内联 `alert`（`common.name`）→ 唯一提前返回条件确认。
3. **交叉证据**：同一条 UI 保存路径在 `qa-resume-20261016-appui` 已被端到端验证通过（`P14P15-06` UI 保存 → 抓到 `POST /api/applications` + `assign`；`P14P15-07` 编辑保存 → 抓到 `PUT /api/applications/<id>`，revision 1→2）。
4. **复现 batch07 的「点击不生效」**：用 agent-browser 0.32.4 做最小实验（sticky header 遮挡场景，脚本见 §6.1）：

   - 页面滚动到 分配节点 卡片后，`保存` 按钮位于视口上方（`saveTop = -325`）：`click @ref` 会自动把元素滚进视口并**成功触发**（`clicks: 1`）；
   - 当按钮处于「已进入视口但被 sticky header 盖住」的位置（`saveTop = 20`，header 高 80px、`z-50`）时，agent-browser 直接拒绝点击并明确报告：
     `✗ Element '@e3' is covered by <header#hdr> at its click point, so the input would land on that element instead.`（旧版本无此保护时会按坐标投递，落在 header 上）；
   - batch07 现场截图 `.../batch07/evidence/UI-03-app-assign-native3.png` 与该场景一致：页面已滚到「分配节点」，第一张卡片的底部（保存/取消行）正好落在视口最上沿、紧贴 sticky header（截图顶部可见被截断的卡片边缘与被压住的按钮）；观测到的「按钮 enabled、3 次点击、无请求、无 toast、无跳转、revision 不变」与「点击落到 header」完全吻合（runner 的 `click_ref` 返回值当时未被记录）。

### 判定

- **不是产品缺陷**：UI 保存路径必然发出 `PUT`（未改动也发），不存在脏数据门禁；`OBS-B07-02` 的「无请求」不能由应用代码解释。
- **不是「未改动则不发请求」的合理行为**（该行为在本实现中不存在）。
- 结论：**harness 点击投递问题（sticky header 遮挡 → 点击落到 header）**，属 `.e2e` 工具侧；产品侧无需修复。当前版本 agent-browser 已能检测并拒绝此类点击，建议后续批次在 runner 里检查 `click` 返回值/先 `scrollintoview`，避免同类误报。
- 顺带收益：本批为应用编辑器的保存按钮补了 pending 禁用（见 §2），与该观测无因果关系。

### 6.1 复现脚本（可重跑，不依赖任何服务）

```html
<!-- /tmp/opencode/sticky-test.html（本会话创建） -->
<header id="hdr" style="position:sticky;top:0;height:80px;background:#0af;z-index:50">编辑托管应用</header>
<div style="height:1800px;padding:12px">
  <div style="height:520px;border:1px solid #ccc"><div style="padding:12px">
    <button id="save" onclick="window.__clicks=(window.__clicks||0)+1">保存</button>
  </div></div>
  <div style="height:900px;border:1px solid #ccc;margin-top:16px;padding:12px"><h3>分配节点</h3></div>
</div>
```

```bash
agent-browser --session stickyprobe open file:///tmp/opencode/sticky-test.html
agent-browser --session stickyprobe eval 'window.scrollTo(0, 85)'      # saveTop = 20，按钮被 sticky header 盖住
agent-browser --session stickyprobe snapshot -i                        # 保存 → ref e3
agent-browser --session stickyprobe click '@e3'
# ✗ Element '@e3' is covered by <header#hdr> at its click point ...
# 旧版本无此保护 → 点击落在 header → 无请求/无报错/无跳转（与 batch07 观测一致）
```

---

## 7. 验证记录（真实命令与结果）

```text
$ cd web && npx vitest run --browser.headless
 Test Files  34 passed (34)
      Tests  178 passed (178)
   Duration  15.00s        # Chromium（@vitest/browser-playwright）

$ npx tsc -b
 exit=0                    # 仓库等价类型检查（npm run build 的第一段）

$ npx tsc --noEmit
 exit=0                    # 根 tsconfig 为 solution 式，实际不检查子项目；以此为准的是上面的 tsc -b

$ npm run lint
 ✖ 4 problems (0 errors, 4 warnings)
   # 4 条均为改动前既有告警：components/data-table/pagination.tsx(1)、features/shared/data-table.tsx(1, react-hooks/incompatible-library)、
   # features/shared/ui.tsx(2)；本次改动未新增告警（新增的 resolveRowId 已改为非导出以避免 react-refresh 告警）
```

新增/修改的测试文件：

| 文件 | 覆盖缺陷 | 用例数 |
|---|---|---|
| `web/src/features/shared/data-table.test.tsx`（改） | #1 | +3（共 9） |
| `web/src/features/editors/save-pending.test.tsx`（新） | #2 | 4 |
| `web/src/components/layout/authenticated-layout.test.tsx`（新） | #3 | 1 |
| `web/src/features/editors/narrow-viewport.test.tsx`（新） | #4 | 2 |
| `web/src/components/sign-out-dialog.test.tsx`（改） | #5 | 4 |
| `web/src/features/editors/application-save-request.test.tsx`（新） | #6 复核 | 2 |

反向验证（确认测试真能抓到缺陷，跑完已全部恢复）：见各节「验证」中的说明，共 4 处（#1 去掉 `getRowId` → 2 失败；#2 去掉 `disabled` → 1 失败；#4 去掉行内 `min-w-0 break-all` → 1 失败、去掉触发器 `w-full min-w-0` → 1 失败）。

其它：`web/vite.config.ts` 的 `optimizeDeps.include` 补入 `@radix-ui/react-direction`、`react-top-loading-bar`（新测试渲染完整布局时会触发 Vite 依赖重优化并重载测试页面，属测试稳定性修复，不影响产品行为）。

---

## 8. 未做项与理由

1. **未运行 `web/e2e`（Playwright E2E 套件）**：该套件指向共享 repair 权威（`http://192.168.100.209:18080`）并需要 `.e2e` 凭证，会写入共享环境；属另一批次范围。本批全部结论基于 **vitest browser（真实 Chromium + 真实样式 + 真实输入事件）** 的组件/布局级验证。
2. **未做全栈自建 E2E（本地 hub + 真实浏览器）**：会覆盖 `web/dist` 构建产物（hub 通过 `embed.go` 内嵌），存在与其它批次互相干扰的风险，故未执行。
3. **未修 `.e2e` 工具侧问题**（第 6 条的点击投递）：按边界不属于 `web/**`，仅给出复现脚本与建议。
4. **未处理的同源风险（已观察、未在证据范围内，故未改）**：
   - Radix `SelectContent` 弹出层没有 `max-w-(--radix-select-content-available-width)`；窄屏下打开含超长选项的下拉时弹出层是否溢出未验证（X20 证据只覆盖闭合态触发器）。
   - `WEB-SKIP-TO-MAIN` 的跳转链接文案仍是英文硬编码「Skip to Main」（本地化缺口，未在缺陷范围内）。
   - 应用编辑器「分配节点」长主机名、执行日志长行等其它窄屏长值场景未逐一验证。
5. **未执行**：Git 任何操作、依赖安装、Go 源码改动、`.e2e` 状态文件改动。

## 9. 风险与备注

- `web/src/**` 在本会话前已有未提交改动（19 个已修改 + 若干未跟踪文件）。本批改动与其共存、未回退；若后续需要精确区分，可按本报告的文件:行清单核对（#1 `data-table.tsx:83-104,151,208-209`；#2/#4 `editors/index.tsx:110-152,379-381,441-459,739-741,810-828,948-950,1108-1142,1651-1653,189/222/317/334/355/537/623/885/1311/1570,652/656/715/1487/1537`；#3 `authenticated-layout.tsx:120-123`；#4 `ui/textarea.tsx:9-10`；#5 `sign-out-dialog.tsx:17-44,56` + locales）。
- 缺陷 1 的修复改变了多选的语义基础（按 id 而非位置），若有隐藏依赖「按位置选中」的用法（未发现），行为会变化；已在 §1 列出。
- 缺陷 5 的行为变化包含新增成功 toast；若产品不希望退出时出现提示，可去掉 `toast.success(t('login.signOutRevoked'))`（一行）。
