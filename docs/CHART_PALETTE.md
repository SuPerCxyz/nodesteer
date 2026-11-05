# 图表调色板

唯一权威配置：`web/src/config/chart-palette.ts`

## 配置

- `CHART_PALETTES`：16 组固定双色渐变，包含 `id`、`name`、`dark.from/to`、`light.from/to`。
- `CHART_STYLE_TOKENS`：线宽 `1.8`、面积透明度、深色光晕 `4px / 0.06`，以及图例、tooltip、hover 使用 series 颜色的约定。

## 取色

```ts
import {
  getChartSeriesStyle,
  assignChartPaletteIds,
  type ChartPaletteId,
} from '@/config/chart-palette'

const style = getChartSeriesStyle('cpu.user', 'dark')
// style.paletteId / style.from / style.to / style.lineWidth / style.area / style.glow

const explicit = getChartSeriesStyle('cpu.user', 'light', {
  paletteId: 'blue-violet',
})
```

未指定 `paletteId` 时按 `seriesId` 做 FNV-1a 稳定哈希，映射到 16 组色板。同一 `seriesId` 在刷新、排序、图例隐藏和时间范围变化后颜色不变。

## 同一图表分配

```ts
const assignments = assignChartPaletteIds(
  ['cpu.total', 'cpu.user', 'cpu.system'],
  { 'cpu.total': 'cyan-blue' }
)
```

- 显式 `paletteId` 优先。
- 自动分配按排序后的 `seriesId` 线性探测，避免同图重复。
- 超过 16 条时按确定规则复用色板，不生成第 17 色。
- 业务系列建议显式指定 `paletteId`，并让 `seriesId` 保持稳定（例如 metric 名，而不是本地化标题或时间范围）。

## 组件接入

`ChartSeries` 支持可选 `paletteId` 和 `seriesId`；未指定 `seriesId` 时使用 `key`。

```tsx
<MultiSeriesChart
  series={[
    {
      key: 'cpu.user',
      seriesId: 'cpu.user',
      paletteId: 'blue-violet',
      name: 'User',
      points,
    },
  ]}
/>
```

`MultiSeriesChart` 统一从配置生成 stroke 渐变、fill 渐变、图例、tooltip 和 hover 圆点颜色；渐变 ID 使用 `useId` 加主题后缀，多实例不会冲突。
