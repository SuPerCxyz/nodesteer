import { useTranslation } from 'react-i18next'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

export interface ExecutionStrategy {
  timeout?: number
  retry?: number
  run_user?: string
  offline_policy?: string
}

/**
 * 共享执行策略字段：timeout / retry / run_user / offline_policy。
 * 调度计划与任务试运行复用，策略来源清晰（Schedule 或本次 Run 请求）。
 */
export function ScheduleStrategyFields({
  value,
  onChange,
  disabled = false,
}: {
  value: ExecutionStrategy
  onChange: (value: ExecutionStrategy) => void
  disabled?: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className='grid gap-4 sm:grid-cols-2'>
      <label className='grid gap-2 text-sm font-medium'>
        {t('strategy.timeoutSec')}
        <Input
          type='number'
          min={0}
          value={value.timeout ?? 300}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...value, timeout: Number(event.target.value) })
          }
        />
      </label>
      <label className='grid gap-2 text-sm font-medium'>
        {t('strategy.retry')}
        <Input
          type='number'
          min={0}
          value={value.retry ?? 0}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...value, retry: Number(event.target.value) })
          }
        />
      </label>
      <label className='grid gap-2 text-sm font-medium'>
        {t('strategy.runUser')}
        <Input
          className='font-mono text-xs'
          value={value.run_user || ''}
          disabled={disabled}
          onChange={(event) =>
            onChange({ ...value, run_user: event.target.value })
          }
        />
      </label>
      <label className='grid gap-2 text-sm font-medium'>
        {t('strategy.offlinePolicy')}
        <Select
          value={value.offline_policy || 'hub_online_required'}
          disabled={disabled}
          onValueChange={(offline_policy) =>
            onChange({ ...value, offline_policy })
          }
        >
          <SelectTrigger className='w-full'>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='hub_online_required'>
              {t('strategy.offlinePolicies.hub_online_required')}
            </SelectItem>
            <SelectItem value='allow_offline'>
              {t('strategy.offlinePolicies.allow_offline')}
            </SelectItem>
          </SelectContent>
        </Select>
      </label>
    </div>
  )
}
