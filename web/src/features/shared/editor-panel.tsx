import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/confirm-dialog'

/** 切换对象与关闭面板共用草稿保护，失败时不重置基线。 */
export function useEditorPanel(value: unknown) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [baseline, setBaseline] = useState('')
  const [pending, setPending] = useState<(() => void) | null>(null)
  const dirty = open && JSON.stringify(value) !== baseline
  const change = (next: unknown, apply: () => void) => {
    const run = () => {
      apply()
      setBaseline(JSON.stringify(next))
      setOpen(true)
      window.setTimeout(() => {
        const panel = document.querySelector<HTMLElement>('[data-editor-panel]')
        panel?.scrollIntoView({ block: 'start', behavior: 'smooth' })
        panel?.focus({ preventScroll: true })
      }, 0)
    }
    if (dirty) setPending(() => run)
    else run()
  }
  const close = () => {
    if (dirty) setPending(() => () => setOpen(false))
    else setOpen(false)
  }
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  return {
    open,
    change,
    close,
    saved: (next: unknown) => setBaseline(JSON.stringify(next)),
    removed: () => {
      setOpen(false)
      setBaseline('')
    },
    confirmation: (
      <ConfirmDialog
        open={!!pending}
        onOpenChange={(next) => {
          if (!next) setPending(null)
        }}
        title={t('webFix.discardTitle')}
        desc={t('webFix.discardDescription')}
        confirmText={t('webFix.discard')}
        cancelBtnText={t('common.cancel')}
        handleConfirm={() => {
          const run = pending
          setPending(null)
          run?.()
        }}
      />
    ),
  }
}

export function EditorPanel({
  title,
  onClose,
  busy,
  children,
}: {
  title: string
  onClose: () => void
  busy?: boolean
  children: ReactNode
}) {
  const { t } = useTranslation()
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    ref.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
    ref.current?.focus({ preventScroll: true })
  }, [])
  return (
    <section
      ref={ref}
      data-editor-panel
      tabIndex={-1}
      aria-label={title}
      className='scroll-mt-24 space-y-4 rounded-lg border p-4 focus:outline-none focus-visible:ring-2 focus-visible:ring-ring'
    >
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <h2 className='font-semibold'>{title}</h2>
        <Button variant='outline' size='sm' onClick={onClose} disabled={busy}>
          {t('common.cancel')}
        </Button>
      </div>
      <fieldset disabled={busy} className='min-w-0 space-y-4'>
        {children}
      </fieldset>
    </section>
  )
}
