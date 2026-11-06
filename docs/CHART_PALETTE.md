# 图表调色板

权威配置：

- `web/src/config/chart-palette.ts`：普通 `series-1..16`，只引用 `--chart-series-*`
- `web/src/config/chart-alert.ts`：告警/错误独立色
- `web/src/styles/azure-theme.css`：默认 **Azure Pop（A）** 的浅色/深色变量
- `web/src/styles/spectrum-theme.css`：`spectrum`（E）回滚快照
- `web/src/styles/theme.css`：`new`（上一版品牌蓝）基线
- `web/src/styles/legacy-theme.css` + `chart-palette.legacy.ts`：更早的 `legacy` 快照
- `web/src/config/palette-mode.ts`：唯一回滚开关，默认 `'azure'`

## 调色板模式

`palette-mode.ts` 默认为 `'azure'`，在 `<html data-palette='azure'>` 时由
`azure-theme.css` 覆盖浅色和 `.dark` 变量。其余模式只切换同一个常量并重建：

```ts
export const PALETTE_MODE: PaletteMode = 'azure' // 'spectrum' | 'new' | 'legacy'
```

| 模式 | 含义 | 浅色主色 | 深色主色 |
|---|---|---|---|
| `azure`（默认） | Azure Pop（A） | `#2563EB` | `#60A5FA` |
| `spectrum` | Spectrum Light（E） | `#3B6FF5` | `#7AA2FF` |
| `new` | 上一版品牌蓝（`theme.css`） | `#005FFB` | `#5B9BFF` |
| `legacy` | 更早旧色（`legacy-theme.css`） | `#1E293B` | `#E2E8F0` |

不提供设置页 UI。浏览器临时验证：

```js
document.documentElement.dataset.palette = 'spectrum'
document.documentElement.dataset.palette = 'new'
document.documentElement.dataset.palette = 'legacy'
```

## Azure Pop（A）普通系列

| # | 浅色 from → to | 深色 from → to |
|---|---|---|
| 1 | `#2056CA → #2563EB` | `#7DB5FB → #60A5FA` |
| 2 | `#087D9A → #0891B2` | `#4ADBF1 → #22D3EE` |
| 3 | `#0C7F77 → #0D9488` | `#53DCCB → #2DD4BF` |
| 4 | `#0D8861 → #0E9F6E` | `#59DBAB → #34D399` |
| 5 | `#0372AC → #0284C7` | `#5CC9F9 → #38BDF8` |
| 6 | `#6933CC → #7C3AED` | `#B7A0FB → #A78BFA` |
| 7 | `#433EC5 → #4F46E5` | `#98A1F9 → #818CF8` |
| 8 | `#8E4AD4 → #A855F7` | `#CB9AFD → #C084FC` |
| 9 | `#148C43 → #16A34A` | `#6BE497 → #4ADE80` |
| 10 | `#135267 → #155E75` | `#82ECFA → #67E8F9` |
| 11 | `#A223B6 → #C026D3` | `#EC91FA → #E879F9` |
| 12 | `#953376 → #B03A86` | `#F79FD4 → #F58ACB` |
| 13 | `#4E6178 → #5C7089` | `#B8C7DB → #A9BBD3` |
| 14 | `#346092 → #3C6FA8` | `#A3C8F3 → #8FBCF0` |
| 15 | `#286D67 → #2E7F75` | `#89DDD0 → #6FD6C6` |
| 16 | `#75508A → #8A5C9E` | `#D6BDE6 → #CDAEE0` |

告警梯度：warning `#754E05 → #8A5A00`（深色 `#FCCB4B → #FBBF24`）、danger
`#9D1A1C → #B91C1C`（深色 `#F98B8B → #F87171`）、info `#045B8C → #0369A1`
（深色 `#94DBFD → #7DD3FC`）。普通系列与全部 alert token 分离。

图表框架：`--chart-axis` 浅色 `#5B6B80` / 深色 `#98A9C1`；`--chart-grid` 浅色
`rgba(15,27,43,.075)` / 深色 `rgba(232,240,250,.09)`；`--chart-grid-strong` 浅色
`rgba(15,27,43,.16)` / 深色 `rgba(232,240,250,.20)`。

## Spectrum Light（E）回滚快照

| # | 浅色 from → to | 深色 from → to |
|---|---|---|
| 1 | `#2F58C2 → #3B6FF5` | `#9AB8FF → #7AA2FF` |
| 2 | `#0A7686 → #0B96A6` | `#65D7E5 → #35CBDD` |
| 3 | `#603FC9 → #7C4DFF` | `#BCA7FB → #A78BFA` |
| 4 | `#B24B80 → #E85D9E` | `#FFAACE → #FF8FBF` |
| 5 | `#128163 → #16A578` | `#79E7C0 → #4FE0AC` |
| 6 | `#476FBD → #5B8DEF` | `#AAC6FF → #8FB4FF` |
| 7 | `#0C7A73 → #0E9B8D` | `#6DE2D4 → #3FD9C7` |
| 8 | `#A44572 → #D6558C` | `#FFBDD9 → #FFA8CD` |
| 9 | `#8145C3 → #A855F7` | `#CFA2FD → #C084FC` |
| 10 | `#4D52BF → #6366F1` | `#B3B5FF → #9B9DFF` |
| 11 | `#485970 → #5C7089` | `#BECBDE → #A9BBD3` |
| 12 | `#87306D → #B03A86` | `#F7A6D7 → #F58ACB` |
| 13 | `#555563 → #6E6A78` | `#CDC8D4 → #BDB7C6` |
| 14 | `#2F5887 → #3C6FA8` | `#AACCF4 → #8FBCF0` |
| 15 | `#566E37 → #6F8C3F` | `#CBE2A2 → #BBD985` |
| 16 | `#256560 → #2E7F75` | `#92E0D4 → #6FD6C6` |

告警梯度：warning `#B25B11 → #E8730C`（深色 `#FFB27C → #FF9A52`）、danger
`#A72620 → #D92D20`（深色 `#FF8F8F → #FF6B6B`）、info `#2F58C2 → #3B6FF5`
（深色 `#9AB8FF → #7AA2FF`）。E 规格中 info 与 `series-1` 同色，表示品牌信息色；
危险/警告色与普通系列严格分离。

## 普通系列

`CHART_PALETTES` 固定为 `series-1..16`，覆盖蓝/青/紫/粉/绿/中性色。颜色值通过 `var(--chart-series-N-from/to)` 引用，业务代码不写 hex，且不得引用 `--chart-alert-*`。

```ts
const style = getChartSeriesStyle('cpu.user', 'dark', {
  paletteId: 'series-1',
})
```

- `assignChartPaletteIds` 为同一图表稳定分配 series id。
- 未指定 `paletteId` 时回退到 `series-13` 中性灰，未知指标不会落入告警色。
- 业务系列应显式指定 paletteId，并使用稳定的 `seriesId`。
- 容器身份只允许 `series-1..16`。

## 告警系列

```ts
import { getChartAlertStyle } from '@/config/chart-alert'

const danger = getChartAlertStyle('danger', 'dark')
```

仅阈值、错误、丢包、危险状态使用 `danger/warning/info`。普通系列禁止引用 `--chart-alert-*`，也不提供哈希入口。

## 映射约定

- Swap：`series-7` 靛蓝亮色（不跟随 iowait，不使用灰色）
- CPU iowait：`series-11`（A 下为洋红，非告警色）
- CPU softirq：`series-9`
- 磁盘写：`series-12`
- 内存 free：`series-11`
- 文件系统 total：`series-13` 中性灰
- 温度：`series-15`
- 风扇：`series-16`
- 网络错误/丢包：`chart-alert`

`MultiSeriesChart` 继续统一生成渐变 stroke、fill、图例、tooltip 和 hover 圆点。
