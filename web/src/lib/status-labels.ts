import i18n from '@/i18n'

/**
 * 状态枚举词表：StatusBadge 与纯文本状态展示共用。
 * 新增后端状态枚举时在此补 zh/en 文案（缺陷 OBS-20261014-TRANSFER-STATUS-BADGE-NOT-LOCALIZED）。
 */
const statusLabels: Record<string, { zh: string; en: string }> = {
  online: { zh: '在线', en: 'Online' },
  offline: { zh: '离线', en: 'Offline' },
  maintenance: { zh: '维护', en: 'Maintenance' },
  busy: { zh: '忙碌', en: 'Busy' },
  degraded: { zh: '降级', en: 'Degraded' },
  healthy: { zh: '健康', en: 'Healthy' },
  unhealthy: { zh: '不健康', en: 'Unhealthy' },
  enabled: { zh: '已启用', en: 'Enabled' },
  disabled: { zh: '已禁用', en: 'Disabled' },
  stopped: { zh: '已停止', en: 'Stopped' },
  pending: { zh: '等待中', en: 'Pending' },
  running: { zh: '运行中', en: 'Running' },
  uploading: { zh: '上传中', en: 'Uploading' },
  delivering: { zh: '投递中', en: 'Delivering' },
  success: { zh: '成功', en: 'Success' },
  completed: { zh: '已完成', en: 'Completed' },
  failed: { zh: '失败', en: 'Failed' },
  error: { zh: '错误', en: 'Error' },
  timed_out: { zh: '超时', en: 'Timed out' },
  timeout: { zh: '超时', en: 'Timeout' },
  cancelled: { zh: '已取消', en: 'Cancelled' },
  canceled: { zh: '已取消', en: 'Canceled' },
  skipped: { zh: '已跳过', en: 'Skipped' },
  blocked: { zh: '已阻断', en: 'Blocked' },
  synced: { zh: '已同步', en: 'Synced' },
  outdated: { zh: '已过期', en: 'Outdated' },
  unknown: { zh: '未知', en: 'Unknown' },
}

/**
 * 状态枚举的本地化文案；未知值回退为原始枚举（下划线转空格）。
 * 容忍缺字段（undefined/null/空串）：返回 '-'，避免部分数据把整页渲染打崩。
 */
export function statusLabel(status?: string | null): string {
  if (typeof status !== 'string' || status.trim() === '') return '-'
  const key = status.toLowerCase().replace(/[-\s]/g, '_')
  return (
    statusLabels[key]?.[i18n.language === 'en' ? 'en' : 'zh'] ||
    status.replace(/_/g, ' ')
  )
}
