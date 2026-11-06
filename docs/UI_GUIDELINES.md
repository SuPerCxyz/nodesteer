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

- 普通系列：`series-1..16`，仅为冷色/中性色，定义在 `chart-palette.ts`，颜色通过 CSS 变量引用，业务代码不含 hex。
- 告警系列：仅通过 `chart-alert.ts` 的 `getChartAlertStyle('danger'|'warning'|'info')` 取得，禁止普通哈希分配。
- 未知指标：回退 `series-13` 中性灰。
- 普通系列不得使用 alert token；Swap、CPU iowait、磁盘写等普通指标不得显示为红/橙/黄告警色。
- 渐变 stroke/area、图例、tooltip、明暗主题保持现有行为。

## 对比度门槛

- 正文/徽标文字：≥ 4.5:1。
- 非文本控件边界、进度条、状态点：≥ 3:1。
- `--input`、`--ring` 必须可见；ring 不透明。
- `muted-foreground`、danger/warning 前景色需在浅/深主题分别验证。
- Tooltip 使用独立 `--tooltip`/`--tooltip-foreground`，不复用 popover 的对比度假设。

## Legacy 回滚

唯一开关：`web/src/config/palette-mode.ts`

```ts
export const PALETTE_MODE: 'new' | 'legacy' = 'new'
```

改为 `'legacy'` 并重建即可。`legacy-theme.css` 与 `chart-palette.legacy.ts` 保存当前旧配色快照；不提供设置页开关。也可在浏览器临时设置 `document.documentElement.dataset.palette = 'legacy'` 验证。
