import { Logo } from '@/assets/logo'
import { LanguageSwitch } from '@/components/language-switch'

export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className='relative container flex h-svh max-w-none items-center justify-center'>
      <div className='absolute end-4 top-4'>
        <LanguageSwitch />
      </div>
      <div className='mx-auto flex w-full max-w-md flex-col justify-center space-y-2 py-10 sm:p-12'>
        <div className='mb-6 flex items-center justify-center gap-2'>
          <Logo className='size-8 text-primary' />
          <h1 className='text-2xl font-semibold tracking-tight'>NodeSteer</h1>
        </div>
        {children}
      </div>
    </div>
  )
}
