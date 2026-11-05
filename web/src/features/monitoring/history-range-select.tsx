import { useState } from 'react'
import { Pencil } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  resolveHistoryWindow,
  type HistoryRange,
  type HistoryRangeError,
} from './history-range'

export function HistoryRangeSelect({
  id,
  value,
  onChange,
  customStart,
  customEnd,
  onCustomStartChange,
  onCustomEndChange,
}: {
  id: string
  value: HistoryRange
  onChange: (range: HistoryRange) => void
  customStart: string
  customEnd: string
  onCustomStartChange: (value: string) => void
  onCustomEndChange: (value: string) => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [draftStart, setDraftStart] = useState(customStart)
  const [draftEnd, setDraftEnd] = useState(customEnd)
  const [draftError, setDraftError] = useState<HistoryRangeError>()

  const openCustomDialog = () => {
    setDraftStart(customStart)
    setDraftEnd(customEnd)
    setDraftError(undefined)
    setOpen(true)
  }

  const handleValueChange = (next: string) => {
    if (next === 'custom') {
      openCustomDialog()
      return
    }
    onChange(next as HistoryRange)
  }

  const applyCustom = () => {
    const window = resolveHistoryWindow('custom', draftStart, draftEnd)
    if (!window.valid) {
      setDraftError(window.error)
      return
    }
    onCustomStartChange(draftStart)
    onCustomEndChange(draftEnd)
    onChange('custom')
    setOpen(false)
  }

  const errorText =
    draftError === 'max'
      ? t('monitoring.maxRange30Days')
      : t('monitoring.invalidTimeRange')

  return (
    <>
      <div className='flex items-center gap-1'>
        <Select value={value} onValueChange={handleValueChange}>
          <SelectTrigger
            id={id}
            aria-label={t('monitoring.timeRange')}
            className='min-w-36'
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='1h'>{t('monitoring.lastHour')}</SelectItem>
            <SelectItem value='3h'>{t('monitoring.last3Hours')}</SelectItem>
            <SelectItem value='6h'>{t('monitoring.last6Hours')}</SelectItem>
            <SelectItem value='12h'>{t('monitoring.last12Hours')}</SelectItem>
            <SelectItem value='24h'>{t('monitoring.last24Hours')}</SelectItem>
            <SelectItem value='7d'>{t('monitoring.last7Days')}</SelectItem>
            <SelectItem value='custom'>
              {t('monitoring.customRange')}
            </SelectItem>
          </SelectContent>
        </Select>
        <Button
          type='button'
          variant='ghost'
          size='icon'
          className={value === 'custom' ? 'size-9' : 'invisible size-9'}
          onClick={openCustomDialog}
          aria-label={t('monitoring.editCustomRange')}
        >
          <Pencil className='size-4' aria-hidden='true' />
        </Button>
      </div>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setDraftError(undefined)
        }}
      >
        <DialogContent className='sm:max-w-md'>
          <DialogHeader>
            <DialogTitle>{t('monitoring.customRange')}</DialogTitle>
            <DialogDescription>
              {t('monitoring.customRangeHint')}
            </DialogDescription>
          </DialogHeader>
          <div className='grid gap-3'>
            <div className='grid gap-1.5'>
              <Label htmlFor={`${id}-start`}>{t('monitoring.startTime')}</Label>
              <Input
                id={`${id}-start`}
                type='datetime-local'
                value={draftStart}
                onChange={(event) => setDraftStart(event.target.value)}
              />
            </div>
            <div className='grid gap-1.5'>
              <Label htmlFor={`${id}-end`}>{t('monitoring.endTime')}</Label>
              <Input
                id={`${id}-end`}
                type='datetime-local'
                value={draftEnd}
                onChange={(event) => setDraftEnd(event.target.value)}
              />
            </div>
            {draftError && (
              <p role='alert' className='text-xs text-destructive'>
                {errorText}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type='button'
              variant='outline'
              onClick={() => setOpen(false)}
            >
              {t('common.cancel')}
            </Button>
            <Button type='button' onClick={applyCustom}>
              {t('common.apply')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
