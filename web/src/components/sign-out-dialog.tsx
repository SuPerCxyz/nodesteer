import { useState } from 'react'
import { useNavigate, useLocation } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { ConfirmDialog } from '@/components/confirm-dialog'

interface SignOutDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function SignOutDialog({ open, onOpenChange }: SignOutDialogProps) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const { auth } = useAuthStore()
  const [pending, setPending] = useState(false)

  const handleSignOut = async () => {
    if (pending) return
    setPending(true)
    // 先请服务端吊销会话（POST /api/logout），再清本地状态并跳转；
    // 吊销失败也必须让本机退出，只提示用户会话可能仍在服务端有效。
    let revoked = true
    try {
      await api.post('/logout')
    } catch (error) {
      revoked = false
      toast.error(t('login.signOutRevokeFailed'), {
        description: error instanceof Error ? error.message : undefined,
      })
    }
    auth.reset()
    // Preserve current location for redirect after sign-in
    const currentPath = location.href
    setPending(false)
    navigate({
      to: '/sign-in',
      search: { redirect: currentPath },
      replace: true,
    })
    if (revoked) toast.success(t('login.signOutRevoked'))
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t('login.signOutTitle')}
      desc={t('login.signOutDescription')}
      cancelBtnText={t('common.cancel')}
      confirmText={t('nav.logout')}
      destructive
      isLoading={pending}
      handleConfirm={handleSignOut}
      className='sm:max-w-sm'
    />
  )
}
