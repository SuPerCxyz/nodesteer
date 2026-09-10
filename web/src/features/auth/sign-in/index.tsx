import { useState } from 'react'
import { z } from 'zod'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useQuery } from '@tanstack/react-query'
import { Loader2, LogIn } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { sessionToUser, useAuthStore } from '@/stores/auth-store'
import { api, type SessionInfo } from '@/lib/api'
import { safeRedirect } from '@/lib/navigation'
import { Button } from '@/components/ui/button'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'
import { PasswordInput } from '@/components/password-input'
import { AuthLayout } from '../auth-layout'

const formSchema = z.object({
  username: z.string().min(1),
  password: z.string().min(1),
})

export function SignIn() {
  const { t } = useTranslation()
  const { auth } = useAuthStore()
  const [error, setError] = useState('')
  const oidc = useQuery({
    queryKey: ['oidc-state'],
    queryFn: () => api.get<{ enabled: boolean }>('/oidc/state'),
  })
  const oidcEnabled = oidc.data?.enabled === true
  const oidcFailed = new URLSearchParams(window.location.search).get('error') === 'oidc_failed'
  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: { username: '', password: '' },
  })

  async function onSubmit(values: z.infer<typeof formSchema>) {
    setError('')
    try {
      const result = await api.post<{ token: string; user: SessionInfo }>(
        '/login',
        values
      )
      auth.setAccessToken(result.token)
      auth.setUser(sessionToUser(result.user))
      toast.success(t('login.success'))
      const redirect = new URLSearchParams(window.location.search).get(
        'redirect'
      )
      window.location.assign(safeRedirect(redirect))
    } catch (err) {
      setError(err instanceof Error ? err.message : t('login.failed'))
    }
  }

  return (
    <AuthLayout>
      <div className='mb-6 space-y-2 text-center'>
        <h2 className='text-2xl font-semibold tracking-tight'>
          {t('common.login')}
        </h2>
        <p className='text-sm text-muted-foreground'>{t('login.subtitle')}</p>
      </div>
      {oidcEnabled ? (
        <div className='grid gap-4'>
          {oidcFailed && (
            <p role='alert' className='text-sm text-destructive'>
              {t('login.oidcFailed')}
            </p>
          )}
          <p className='text-sm text-muted-foreground'>{t('login.sso')}</p>
          <Button
            type='button'
            onClick={() => window.location.assign('/api/oidc/login')}
          >
            <LogIn />
            {t('login.sso')}
          </Button>
        </div>
      ) : (
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className='grid gap-4'>
          <FormField
            control={form.control}
            name='username'
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t('login.username')}</FormLabel>
                <FormControl>
                  <Input autoComplete='username' {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name='password'
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t('login.password')}</FormLabel>
                <FormControl>
                  <PasswordInput autoComplete='current-password' {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          {error && (
            <p role='alert' className='text-sm text-destructive'>
              {error}
            </p>
          )}
          <Button type='submit' disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? (
              <Loader2 className='animate-spin' />
            ) : (
              <LogIn />
            )}
            {form.formState.isSubmitting
              ? t('common.loggingIn')
              : t('common.login')}
          </Button>
          </form>
        </Form>
      )}
    </AuthLayout>
  )
}
