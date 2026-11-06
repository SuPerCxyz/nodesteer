# 图表调色板

权威配置：

- `web/src/config/chart-palette.ts`：普通 `series-1..16`
- `web/src/config/chart-alert.ts`：告警/错误独立色
- `web/src/styles/theme.css`：CSS 变量实际值
- `web/src/config/chart-palette.legacy.ts` + `legacy-theme.css`：回滚快照

## 普通系列

`CHART_PALETTES` 固定为 `series-1..16`，只包含冷色和中性色。颜色值通过 `var(--chart-series-N-from/to)` 引用，业务代码不写 hex。

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

- Swap、CPU iowait：`series-11` 蓝灰
- CPU softirq：`series-9` 青灰
- 磁盘写：`series-12` 石墨灰
- 内存 free：`series-11`
- 文件系统 total：`series-13`
- 温度：`series-15` 低饱和陶土
- 风扇：`series-16` 低饱和天青
- 网络错误/丢包：`chart-alert`

`MultiSeriesChart` 继续统一生成渐变 stroke、fill、图例、tooltip 和 hover 圆点。
