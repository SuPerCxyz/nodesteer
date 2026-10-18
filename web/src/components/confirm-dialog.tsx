import * as React from 'react'
import { cn } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'

type ConfirmDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: React.ReactNode
  disabled?: boolean
  desc: React.JSX.Element | string
  cancelBtnText?: string
  confirmText?: React.ReactNode
  destructive?: boolean
  isLoading?: boolean
  className?: string
  children?: React.ReactNode
  /** 关闭后显式还焦点的目标（无 trigger 的 Radix 弹窗默认会把焦点丢给 body） */
  returnFocusTo?: React.RefObject<HTMLElement | null>
} & (
  | { form: string; handleConfirm?: undefined }
  | { form?: undefined; handleConfirm: () => void }
)

export function ConfirmDialog(props: ConfirmDialogProps) {
  const {
    title,
    desc,
    children,
    className,
    confirmText,
    cancelBtnText,
    destructive,
    isLoading,
    disabled = false,
    form,
    handleConfirm,
    returnFocusTo,
    ...actions
  } = props
  // Radix 弹窗在无 trigger 时关闭后会 preventDefault 焦点恢复，把焦点丢给 body；
  // 这里记录打开前的焦点元素，关闭后显式恢复，保证键盘用户回到触发按钮。
  const previouslyFocusedRef = React.useRef<HTMLElement | null>(null)
  return (
    <AlertDialog {...actions}>
      <AlertDialogContent
        className={cn(className && className)}
        onOpenAutoFocus={() => {
          previouslyFocusedRef.current =
            document.activeElement instanceof HTMLElement
              ? document.activeElement
              : null
        }}
        onCloseAutoFocus={(event) => {
          const target = returnFocusTo?.current ?? previouslyFocusedRef.current
          if (target && target.isConnected) {
            event.preventDefault()
            target.focus()
          }
        }}
      >
        <AlertDialogHeader className='text-start'>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div>{desc}</div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {children}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isLoading}>
            {cancelBtnText ?? 'Cancel'}
          </AlertDialogCancel>
          <Button
            type={form ? 'submit' : 'button'}
            form={form}
            onClick={handleConfirm}
            variant={destructive ? 'destructive' : 'default'}
            disabled={disabled || isLoading}
          >
            {confirmText ?? 'Continue'}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
