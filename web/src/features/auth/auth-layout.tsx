import { Logo } from '@/assets/logo'

export function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className='container grid h-svh max-w-none items-center justify-center'>
      <div className='mx-auto flex w-full max-w-sm flex-col justify-center space-y-2 py-8 sm:p-8'>
        <div className='mb-6 flex items-center justify-center gap-2'>
          <Logo className='size-6 text-primary' />
          <h1 className='text-xl font-semibold tracking-tight'>NodeSteer</h1>
        </div>
        {children}
      </div>
    </div>
  )
}
