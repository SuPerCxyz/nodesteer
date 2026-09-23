import { setLang } from '@/i18n'
import { Languages } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

/** 语言切换（顶栏与登录页共用）。 */
export function LanguageSwitch() {
  const { t, i18n } = useTranslation()
  const language = i18n.language === 'en' ? 'en' : 'zh'
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant='ghost'
          size='icon'
          className='size-8'
          aria-label={t('common.language')}
        >
          <Languages className='size-4' />
          <span className='sr-only'>{language === 'en' ? 'EN' : '中'}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align='end'>
        <DropdownMenuItem onClick={() => setLang('zh')}>中文</DropdownMenuItem>
        <DropdownMenuItem onClick={() => setLang('en')}>
          English
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
