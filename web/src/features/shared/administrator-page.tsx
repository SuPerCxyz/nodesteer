import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useCanWrite } from '@/lib/permissions'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { ErrorState } from './ui'

/** 在查询组件挂载前拒绝已知无权会话；角色变化会卸载整个页面查询。 */
export function AdministratorPage({
  titleKey,
  children,
}: {
  titleKey: string
  children: ReactNode
}) {
  const { t } = useTranslation()
  const allowed = useCanWrite()
  if (allowed) return children
  return (
    <>
      <NodeSteerHeader title={t(titleKey)} />
      <Main>
        <ErrorState error={t('errors.permissionDenied')} />
      </Main>
    </>
  )
}
