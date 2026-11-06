# UI 配色与语义规范

## Token 分层

1. **Primitives**：`web/src/styles/theme.css` 中的原始品牌/中性/状态色只允许出现在 token 文件。
2. **Semantic**：`--background`、`--foreground`、`--surface*`、`--primary`、`--muted-foreground`、`--border`、`--input`、`--ring`、`--overlay`、`--tooltip`、`--code-*`、`--track` 等表达主题意图。
3. **Domain**：`--status-*`、`--chart-series-1..16-*`、`--chart-alert-*` 表达监控/告警业务语义。
4. **Tailwind bridge**：`@theme inline` 将 semantic/domain token 映射为 `bg-*`、`text-*`、`border-*` 等工具类。业务组件只能使用这些语义类。

禁止项：

- 业务代码出现 `#RGB`/`#RRGGBB`、`rgb()`、`oklch()` 字面量。
- 业务代码使用 `bg-red-*`、`text-amber-*`、`bg-slate-*` 等 Tailwind 原始调色板类。
- 普通图表通过哈希或自定义 hex 进入告警色。
- 用颜色作为唯一状态信息（必须同时有文字/图标/aria）。

## 状态与级别映射

| 语义 | 状态/级别 | Token |
|---|---|---|
| success | online, healthy, enabled, success, completed, synced, resolved | `--success` |
| info | pending, uploading, delivering, running, busy, info | `--info` |
| warning | maintenance, stale, degraded, blocked, outdated, warning | `--warning` |
| danger | failed, error, timed_out, timeout, critical, firing, unhealthy | `--danger` |
| neutral | offline, disabled, stopped, skipped, canceled, silenced, unknown, 空值 | `--neutral` / surface |

`critical/firing` 不得使用灰色；`resolved` 使用 success；`silenced/unknown` 使用 neutral。

## 图表色板

- 默认调色板为 **Azure Pop（A）**，由 `azure-theme.css` 通过 `[data-palette='azure']` 覆盖浅色和 `.dark` 变量；`spectrum`/`new`/`legacy` 作为回滚快照保留。
- 普通系列：`series-1..16`，定义在 `chart-palette.ts`，颜色只通过 `var(--chart-series-N-from/to)` 引用，业务代码不含 hex。
- 告警系列：仅通过 `chart-alert.ts` 的 `getChartAlertStyle('danger'|'warning'|'info')` 取得，禁止普通哈希分配。
- 未知指标：回退 `series-13` 中性灰。
- 普通系列不得引用 `--chart-alert-*`；Swap、CPU iowait、磁盘写等普通指标不得显示为红/橙/黄告警色。
- Swap 固定 `series-7` 靛蓝亮色；CPU iowait 为 `series-11`，磁盘写为 `series-12`。
- 渐变 stroke/area、图例、tooltip、明暗主题保持现有行为；tooltip 使用 `--tooltip`/`--tooltip-foreground`/`--tooltip-muted`/`--tooltip-border`。

## 对比度门槛

- 正文/徽标文字：≥ 4.5:1。
- 非文本控件边界、进度条、状态点：≥ 3:1。
- `--input`、`--ring` 必须可见；ring 不透明。
- `muted-foreground`、danger/warning 前景色需在浅/深主题分别验证。
- Tooltip 使用独立 `--tooltip`/`--tooltip-foreground`/`--tooltip-muted`/`--tooltip-border`，不复用 popover 的对比度假设。
- 使用率进度条：75–90% 为 `--usage-warning`（A 下 `#FDE68A`），>90% 为 `--danger`；`--usage-warning-strong` 为 `#FCD34D`，明暗主题相同。

## 调色板模式与回滚

唯一开关：`web/src/config/palette-mode.ts`

```ts
export type PaletteMode = 'azure' | 'spectrum' | 'new' | 'legacy'
export const PALETTE_MODE: PaletteMode = 'azure'
```

- `azure`（默认）：Azure Pop（A），`azure-theme.css`。浅色 `--primary: #2563EB`、`--background: #F4F8FF`、`--card: #FFFFFF`；深色 `--primary: #60A5FA`、`--background: #08111F`、`--card: #0F1B2C`。
- `spectrum`：Spectrum Light（E），`spectrum-theme.css`。浅色主色 `#3B6FF5`、深色 `#7AA2FF`。
- `new`：上一版品牌蓝快照（`theme.css`），浅色主色 `#005FFB`、深色 `#5B9BFF`。
- `legacy`：更早旧色快照（`legacy-theme.css`），浅色主色 `#1E293B`、深色 `#E2E8F0`。

改为 `'spectrum'`、`'new'` 或 `'legacy'` 并重建即可。不提供设置页开关；也可在浏览器临时设置
`document.documentElement.dataset.palette = 'azure' | 'spectrum' | 'new' | 'legacy'` 验证。
浅色 `--text-faint` 为 `#5F7084`，`--chart-axis` 为 `#5B6B80`。

## 等宽字体子集

`web/src/assets/fonts/maple-mono-nl-cn-medium-subset.woff2` 由上游
`MapleMonoNL-NF-CN-Medium.ttf` 用 `pyftsubset` 按前端实际用字生成，避免随包携带完整
20 MB 字体。子集包含：

- `web/src/**/*.{ts,tsx,css,html}` 与 `web/src/locales/*` 中出现的非 ASCII 字符；
- ASCII 可打印字符、常用中英文标点、箭头、数学符号、单位符号（`GiB`、`MB/s`、`%`、`°` 等）；
- 不包含 Nerd Font 私有区图标；图标统一使用图标组件。

字体只通过 `--font-mono` 用于代码块、日志、指标和数值等等宽场景，fallback 为
`ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`。默认界面字体仍是
`system`，不强制全站正文使用；`font-maple` 是用户可选的整站字体，保持按需加载。
`font-display: swap`，不做无条件 preload。

新增或修改中文 UI 文案后重新生成：

```bash
cd web
# 依赖 fonttools + brotli；不要全局安装时可用临时 venv
python3 -m venv /tmp/font-subset-venv
/tmp/font-subset-venv/bin/pip install fonttools brotli
PATH=/tmp/font-subset-venv/bin:$PATH \
  ./scripts/build-font-subset.sh ~/tmp/MapleMonoNL-NF-CN-Medium.ttf
```

脚本会刷新字符清单 `web/scripts/font-subset-chars.txt` 并覆盖 woff2。生成的字体放在
`src/assets/` 下，Vite 会加内容 hash 并走既有的 immutable 缓存。当前规模：944 个字符
（其中 CJK 776 个），约 211 KiB；完整源字体约 20 MB。后端返回的动态中文（节点名、
任务名、错误详情等）若不在子集中会按 fallback 渲染，这是预期行为。

## 页头主操作（header-action）

页头右上角只保留一个主操作，统一使用 `Button variant='header-action'`。品牌蓝
`primary` 仍用于表单内提交等非页头主操作，不全局替换。

| Token | 浅色主题 | 深色主题 | 用途 |
|---|---|---|---|
| `--header-action` | `#0b1220`（近黑） | `#1e293b`（深灰） | 按钮底色 |
| `--header-action-foreground` | `#ffffff` | `#f8fafc` | 按钮文字 |
| `--header-action-hover` | `#1e293b` | `#334155` | hover 底色 |
| `--header-action-border` | `transparent` | `#64748b` | 深色主题可见边框 |
| `--header-action-ring` | `#005ffb` | `#5b9bff` | 焦点环 |

- Tailwind bridge：`bg-header-action`、`text-header-action-foreground`、
  `hover:bg-header-action-hover`、`border-header-action-border`、
  `ring-header-action-ring`。
- 近黑底白字正文对比度 ≥ 4.5:1；深色主题必须保留 1px 可见边框，焦点环在明暗主题均可见。
- `legacy-theme.css` 同步维护同名 token；legacy 回滚时页头主操作仍为黑色。
- 危险操作（删除、停止、取消、撤销凭据）继续使用 `variant='destructive'` 红色，并保持既有位置；不得为了统一页头主操作改成 `header-action` 或隐藏为普通菜单项。次要操作继续使用 `variant='outline'`。

## Card 包裹规范

- 列表页：工具栏、表格/列表、空态与分页放在同一张 Card 内；有数据与空态不得切换成不同容器。
- 加载、错误态使用 `StateCard`（`data-card='state'`，保留 `data-slot='card'`），与列表 Card 保持一致的
  `px-4 sm:px-6` 内边距和 `min-h-32` 最小高度。
- 列表 Card 使用 `ListCard` / `ListCardBody`（或等价的 `gap-0 py-0` Card），空态
  `min-h-32`，分页使用 `border-t` 与 `px-4 sm:px-6`。
- `DataTable` 嵌入 Card 时使用 `inset`，去掉表格自带外边框和重复内边距，避免双层边框。
- 现有 Tasks / Executions 工作区 Card 不再重复包裹 Card；编辑面板 `EditorPanel` 内不再
  嵌套 Card，观察/触发历史使用 `SectionCard` 或统一小节。
- 页头已有创建入口时，空态不再重复创建 CTA；仅无页头入口的页面可在空态提供恢复操作。
