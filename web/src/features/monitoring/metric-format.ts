import { historyPointValid } from './history'
import type { History } from './types'

export type PercentTone = 'success' | 'warning' | 'danger' | 'neutral'

/**
 * 节点资源使用率统一分级：<75% 绿色，75%–90% 黄色，>90% 红色。
 * 边界值归属高一级（75/90 均为黄色），避免 75.0%、90.0% 显示成绿色。
 */
export function percentTone(value?: number | null): PercentTone {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'neutral'
  if (value > 90) return 'danger'
  if (value >= 75) return 'warning'
  return 'success'
}

export function formatPercent(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(1)}%`
    : null
}

export function formatLoad(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toFixed(2)
    : null
}

export function formatBps(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  if (value < 1024) return `${value.toFixed(0)} B/s`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB/s`
  return `${(value / (1024 * 1024)).toFixed(1)} MB/s`
}

export function formatRate(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(2)}/s`
    : null
}

/** 最新有效样本；没有可验证点时返回 null，避免把空数据当成 0。 */
export function latestPointValue(points: History['points']) {
  for (let index = points.length - 1; index >= 0; index--) {
    const point = points[index]
    if (point.value !== null && historyPointValid(point)) return point.value
  }
  return null
}

export function formatBytes(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  const index =
    value > 0
      ? Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)))
      : 0
  return `${(value / 1024 ** Math.max(0, index)).toFixed(index ? 1 : 0)} ${units[Math.max(0, index)]}`
}
