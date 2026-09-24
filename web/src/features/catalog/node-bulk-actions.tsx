import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api, type Node } from '@/lib/api'
import { useCanRun, useCanWrite } from '@/lib/permissions'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/confirm-dialog'
import {
  applyNodeStatus,
  splitUpgradeTargets,
  summarizeUpgradeResults,
} from './node-bulk'

type PendingAction = 'none' | 'upgrade' | 'maintenance' | 'resume'

/**
 * 节点多选后的批量操作工具条内容（由 DataTableBulkActions 承载）。
 * 升级仅对 native 形态开放；暂停/恢复对所有形态可用。
 */
export function NodeBulkActions({
  nodes,
  onClear,
}: {
  nodes: Node[]
  onClear: () => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canRun = useCanRun()
  const canWrite = useCanWrite()
  const [pending, setPending] = useState<PendingAction>('none')
  const [busy, setBusy] = useState(false)

  const targets = splitUpgradeTargets(nodes)
  const paused = nodes.filter(
    (node) => node.status === 'maintenance' || node.status === 'disabled'
  )
  const active = nodes.filter(
    (node) => node.status !== 'maintenance' && node.status !== 'disabled'
  )
  const close = () => setPending('none')

  const upgrade = async () => {
    setBusy(true)
    try {
      const results = await api.upgradeNodes(targets.nodeIds)
      const summary = summarizeUpgradeResults(results)
      const description = [
        targets.skippedDocker
          ? t('nodes.bulkUpgradeSkipDocker', { count: targets.skippedDocker })
          : '',
        summary.reasons.length
          ? t('nodes.bulkUpgradeReasons', {
              reasons: summary.reasons.join('、'),
            })
          : '',
        t('nodes.bulkUpgradeHint'),
      ]
        .filter(Boolean)
        .join('\n')
      toast.success(
        t('nodes.bulkUpgradeResult', {
          dispatched: summary.dispatched,
          deduplicated: summary.deduplicated,
          skipped: summary.skipped,
          failed: summary.failed,
        }),
        description ? { description } : undefined
      )
      await client.invalidateQueries({ queryKey: ['nodes'] })
      close()
      onClear()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('common.noData'))
    } finally {
      setBusy(false)
    }
  }

  const changeStatus = async (status: string, selected: Node[]) => {
    setBusy(true)
    try {
      const result = await applyNodeStatus(
        selected.map((node) => node.id),
        status,
        (nodeId, next) => api.post(`/nodes/${nodeId}`, { status: next })
      )
      const title = t('nodes.bulkStatusResult', {
        ok: result.ok,
        fail: result.fail,
      })
      const description = result.reasons.length
        ? t('nodes.bulkStatusReasons', { reasons: result.reasons.join('、') })
        : ''
      if (result.fail > 0) toast.error(title, { description })
      else toast.success(title)
      await client.invalidateQueries({ queryKey: ['nodes'] })
      close()
      onClear()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('common.noData'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {canRun && targets.nodeIds.length > 0 ? (
        <Button
          variant='outline'
          size='sm'
          onClick={() => setPending('upgrade')}
          disabled={busy}
        >
          {t('nodes.bulkUpgrade')} ({targets.nodeIds.length})
        </Button>
      ) : null}
      {canWrite && active.length > 0 ? (
        <Button
          variant='outline'
          size='sm'
          onClick={() => setPending('maintenance')}
          disabled={busy}
        >
          {t('nodes.bulkMaintenance')} ({active.length})
        </Button>
      ) : null}
      {canWrite && paused.length > 0 ? (
        <Button
          variant='outline'
          size='sm'
          onClick={() => setPending('resume')}
          disabled={busy}
        >
          {t('nodes.bulkResume')} ({paused.length})
        </Button>
      ) : null}

      <ConfirmDialog
        open={pending === 'upgrade'}
        onOpenChange={(open) => (open ? setPending('upgrade') : close())}
        title={t('nodes.bulkUpgradeTitle')}
        desc={
          <div className='grid gap-2 text-sm'>
            <p>
              {t('nodes.bulkUpgradeDesc', { count: targets.nodeIds.length })}
            </p>
            {targets.skippedDocker > 0 ? (
              <p className='text-amber-600 dark:text-amber-500'>
                {t('nodes.bulkUpgradeSkipDocker', {
                  count: targets.skippedDocker,
                })}
              </p>
            ) : null}
          </div>
        }
        confirmText={t('nodes.bulkUpgradeConfirm')}
        cancelBtnText={t('common.cancel')}
        isLoading={busy}
        handleConfirm={upgrade}
      />
      <ConfirmDialog
        open={pending === 'maintenance'}
        onOpenChange={(open) => (open ? setPending('maintenance') : close())}
        title={t('nodes.bulkMaintenance')}
        desc={t('nodes.bulkMaintenanceDesc', { count: active.length })}
        confirmText={t('nodes.bulkStatusConfirm')}
        cancelBtnText={t('common.cancel')}
        isLoading={busy}
        handleConfirm={() => changeStatus('maintenance', active)}
      />
      <ConfirmDialog
        open={pending === 'resume'}
        onOpenChange={(open) => (open ? setPending('resume') : close())}
        title={t('nodes.bulkResume')}
        desc={t('nodes.bulkResumeDesc', { count: paused.length })}
        confirmText={t('nodes.bulkStatusConfirm')}
        cancelBtnText={t('common.cancel')}
        isLoading={busy}
        handleConfirm={() => changeStatus('online', paused)}
      />
    </>
  )
}
