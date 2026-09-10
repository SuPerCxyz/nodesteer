import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  api,
  getToken,
  type Application,
  type ApplicationNodeState,
  type Artifact,
  type Execution,
  type Group,
  type Node,
  type Parameter,
  type Schedule,
  type Script,
  type Task,
} from '@/lib/api'
import { useCanRun, useCanWrite } from '@/lib/permissions'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Textarea } from '@/components/ui/textarea'
import { CadentraHeader } from '@/components/layout/cadentra-header'
import { Main } from '@/components/layout/main'
import { ErrorState, StatusBadge } from '@/features/shared/ui'

function zonedDateTimeValue(value: string, timezone: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone || 'UTC',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(date)
  } catch {
    return ''
  }
  const get = (type: string) => parts.find((part) => part.type === type)?.value || ''
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`
}

function zonedDateTimeToISO(value: string, timezone: string) {
  const [datePart, timePart] = value.split('T')
  const [year, month, day] = datePart.split('-').map(Number)
  const [hour, minute] = timePart.split(':').map(Number)
  const guess = new Date(Date.UTC(year, month - 1, day, hour, minute))
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone || 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
    }).formatToParts(guess)
  } catch {
    return guess.toISOString()
  }
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value || 0)
  const asZonedUTC = Date.UTC(get('year'), get('month') - 1, get('day'), get('hour'), get('minute'), get('second'))
  return new Date(guess.getTime() - (asZonedUTC - guess.getTime())).toISOString()
}

function EditorShell({
  title,
  description,
  children,
}: {
  title: string
  description: string
  children: React.ReactNode
}) {
  return (
    <>
      <CadentraHeader title={title} description={description} />
      <Main className='flex flex-1 flex-col gap-6'>
        <div className='w-full max-w-4xl'>{children}</div>
      </Main>
    </>
  )
}

export function ScheduleEditor() {
  const { t } = useTranslation()
  const canWrite = useCanWrite()
  const id = window.location.pathname.split('/')[2] || ''
  const editing = id !== 'new'
  const tasks = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<Task[]>('/tasks'),
  })
  const current = useQuery({
    queryKey: ['schedule', id],
    queryFn: () => api.get<Schedule>(`/schedules/${id}`),
    enabled: editing,
  })
  const [error, setError] = useState('')
  const defaultForm: Partial<Schedule> = {
    task_id: '',
    type: 'cron',
    expression: '',
    interval_sec: 60,
    run_at: '',
    timezone: 'UTC',
    execution_owner: 'agent',
    offline_policy: 'allow_offline',
    misfire_policy: 'run_once',
    enabled: true,
  }
  const [draft, setDraft] = useState<Partial<Schedule> | null>(null)
  const form = draft || current.data || defaultForm
  const setForm = (next: Partial<Schedule>) => setDraft(next)
  const save = async () => {
    setError('')
    if (!form.task_id) {
      setError(t('schedules.taskRequired'))
      return
    }
    if (form.type === 'cron' && !form.expression?.trim()) {
      setError(t('schedules.cronRequired'))
      return
    }
    if (
      form.type === 'interval' &&
      (!Number.isFinite(form.interval_sec) || (form.interval_sec || 0) < 1)
    ) {
      setError(t('schedules.intervalInvalid'))
      return
    }
    if (form.type === 'one_time' && !form.run_at) {
      setError(t('schedules.runAtRequired'))
      return
    }
    const payload = { ...form }
    if (payload.type !== 'one_time') delete payload.run_at
    try {
      if (editing) await api.put(`/schedules/${id}`, payload)
      else await api.post('/schedules', payload)
      window.location.assign('/schedules')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('common.noData'))
    }
  }
  return (
    <EditorShell
      title={editing ? t('schedules.editTitle') : t('schedules.newTitle')}
      description={t('schedules.formDescription')}
    >
      <Card className='max-w-4xl'>
        <CardContent className='grid gap-5 pt-6'>
          {error && <ErrorState error={error} />}
          <label className='grid gap-2 text-sm font-medium'>
            {t('schedules.task')}
            <Select
              value={form.task_id || ''}
              onValueChange={(value) => setForm({ ...form, task_id: value })}
            >
              <SelectTrigger>
                <SelectValue placeholder={t('schedules.selectTask')} />
              </SelectTrigger>
              <SelectContent>
                {(tasks.data || []).map((task) => (
                  <SelectItem key={task.id} value={task.id}>
                    {task.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className='grid gap-2 text-sm font-medium'>
            {t('common.type')}
            <Select
              value={form.type || 'cron'}
              onValueChange={(value) => setForm({ ...form, type: value })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='cron'>{t('schedules.cron')}</SelectItem>
                <SelectItem value='interval'>
                  {t('schedules.interval')}
                </SelectItem>
                <SelectItem value='one_time'>
                  {t('schedules.oneTime')}
                </SelectItem>
              </SelectContent>
            </Select>
          </label>
          {form.type === 'cron' && (
            <label className='grid gap-2 text-sm font-medium'>
              {t('schedules.cronExpression')}
              <Input
                className='font-mono text-xs'
                value={form.expression || ''}
                onChange={(event) =>
                  setForm({ ...form, expression: event.target.value })
                }
              />
            </label>
          )}
          {form.type === 'interval' && (
            <label className='grid gap-2 text-sm font-medium'>
              {t('schedules.intervalSec')}
              <Input
                type='number'
                min={1}
                value={form.interval_sec || 0}
                onChange={(event) =>
                  setForm({ ...form, interval_sec: Number(event.target.value) })
                }
              />
            </label>
          )}
          {form.type === 'one_time' && (
            <label className='grid gap-2 text-sm font-medium'>
              {t('schedules.runAt')}
              <Input
                type='datetime-local'
                value={form.run_at ? zonedDateTimeValue(form.run_at, form.timezone || 'UTC') : ''}
                onChange={(event) =>
                  setForm({
                    ...form,
                    run_at: event.target.value
                      ? zonedDateTimeToISO(event.target.value, form.timezone || 'UTC')
                      : '',
                  })
                }
              />
            </label>
          )}
          <label className='grid gap-2 text-sm font-medium'>
            {t('schedules.timezone')}
            <Input
              className='font-mono text-xs'
              value={form.timezone || ''}
              onChange={(event) =>
                setForm({ ...form, timezone: event.target.value })
              }
            />
          </label>
          <div className='grid gap-4 sm:grid-cols-3'>
            <label className='grid gap-2 text-sm font-medium'>
              {t('schedules.executionOwner')}
              <Select
                value={form.execution_owner || 'agent'}
                onValueChange={(value) =>
                  setForm({ ...form, execution_owner: value })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value='agent'>{t('schedules.agent')}</SelectItem>
                  <SelectItem value='hub'>{t('schedules.hub')}</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className='grid gap-2 text-sm font-medium'>
              {t('schedules.offlinePolicy')}
              <Select
                value={form.offline_policy || 'allow_offline'}
                onValueChange={(value) =>
                  setForm({ ...form, offline_policy: value })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value='allow_offline'>
                    {t('tasks.allowOffline')}
                  </SelectItem>
                  <SelectItem value='hub_online_required'>
                    {t('tasks.hubOnlineRequired')}
                  </SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className='grid gap-2 text-sm font-medium'>
              {t('schedules.misfirePolicy')}
              <Select
                value={form.misfire_policy || 'run_once'}
                onValueChange={(value) =>
                  setForm({ ...form, misfire_policy: value })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value='run_once'>
                    {t('schedules.runOnce')}
                  </SelectItem>
                  <SelectItem value='skip'>{t('schedules.skip')}</SelectItem>
                </SelectContent>
              </Select>
            </label>
          </div>
          <label className='flex items-center gap-2 text-sm font-medium'>
            <Checkbox
              checked={form.enabled !== false}
              onCheckedChange={(checked) =>
                setForm({ ...form, enabled: checked === true })
              }
            />
            {t('common.enabled')}
          </label>
          <Separator />
          <div className='flex items-center gap-2'>
            {canWrite ? (
              <Button onClick={save}>{t('common.save')}</Button>
            ) : null}
            <Button asChild variant='outline'>
              <Link to='/schedules'>{t('common.cancel')}</Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </EditorShell>
  )
}

export function ScriptEditor() {
  const { t } = useTranslation()
  const canWrite = useCanWrite()
  const id = window.location.pathname.split('/')[2] || ''
  const editing = id !== 'new'
  const current = useQuery({
    queryKey: ['script', id],
    queryFn: () => api.get<Script>(`/scripts/${id}`),
    enabled: editing,
  })
  const revisions = useQuery({
    queryKey: ['script-revisions', id],
    queryFn: () =>
      api.get<
        {
          revision: number
          content: string
          sha256: string
          changed_at: string
        }[]
      >(`/scripts/${id}/revisions`),
    enabled: editing,
  })
  const defaultForm: Partial<Script> = {
    name: '',
    description: '',
    interpreter: 'shell',
    content: '#!/bin/sh\necho hello',
    working_dir: '',
    run_user: '',
    timeout: 300,
    enabled: true,
    parameters: [],
    environment: {},
  }
  const [draft, setDraft] = useState<Partial<Script> | null>(null)
  const form = draft || current.data || defaultForm
  const setForm = (next: Partial<Script>) => setDraft(next)
  const [parameter, setParameter] = useState<Parameter>({
    name: '',
    type: 'string',
    required: false,
    default: '',
  })
  const [environment, setEnvironment] = useState({ key: '', value: '' })
  const [error, setError] = useState('')
  const save = async () => {
    setError('')
    if (!form.name) {
      setError(t('common.name'))
      return
    }
    try {
      if (editing) await api.put(`/scripts/${id}`, form)
      else await api.post('/scripts', form)
      window.location.assign('/scripts')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('common.noData'))
    }
  }
  const addParameter = () => {
    if (!parameter.name) return
    setForm({ ...form, parameters: [...(form.parameters || []), parameter] })
    setParameter({ name: '', type: 'string', required: false, default: '' })
  }
  const addEnvironment = () => {
    if (!environment.key) return
    setForm({
      ...form,
      environment: {
        ...(form.environment || {}),
        [environment.key]: environment.value,
      },
    })
    setEnvironment({ key: '', value: '' })
  }
  return (
    <EditorShell
      title={editing ? t('scripts.editTitle') : t('scripts.newTitle')}
      description={t('scripts.formDescription')}
    >
      <div className='grid gap-6'>
        <Card>
          <CardHeader>
            <CardTitle className='text-base'>{t('scripts.title')}</CardTitle>
          </CardHeader>
          <CardContent className='grid gap-5'>
            {error && <ErrorState error={error} />}
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.name')}
              <Input
                required
                value={form.name || ''}
                onChange={(event) =>
                  setForm({ ...form, name: event.target.value })
                }
              />
            </label>
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.description')}
              <Input
                value={form.description || ''}
                onChange={(event) =>
                  setForm({ ...form, description: event.target.value })
                }
              />
            </label>
            <div className='grid gap-4 sm:grid-cols-2'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('scripts.interpreter')}
                <Select
                  value={form.interpreter || 'shell'}
                  onValueChange={(value) =>
                    setForm({ ...form, interpreter: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='shell'>Shell</SelectItem>
                    <SelectItem value='bash'>Bash</SelectItem>
                    <SelectItem value='python'>Python</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('scripts.timeout')}
                <Input
                  type='number'
                  min={0}
                  value={form.timeout ?? 0}
                  onChange={(event) =>
                    setForm({ ...form, timeout: Number(event.target.value) })
                  }
                />
              </label>
            </div>
            <div className='grid gap-2 text-sm font-medium'>
              <span>{t('scripts.content')}</span>
              <Textarea
                className='min-h-72 font-mono text-xs leading-6'
                value={form.content || ''}
                onChange={(event) =>
                  setForm({ ...form, content: event.target.value })
                }
              />
            </div>
            <div className='grid gap-4 sm:grid-cols-2'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('scripts.workingDir')}
                <Input
                  className='font-mono text-xs'
                  value={form.working_dir || ''}
                  onChange={(event) =>
                    setForm({ ...form, working_dir: event.target.value })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('scripts.runUser')}
                <Input
                  className='font-mono text-xs'
                  value={form.run_user || ''}
                  onChange={(event) =>
                    setForm({ ...form, run_user: event.target.value })
                  }
                />
              </label>
            </div>
            <label className='flex items-center gap-2 text-sm font-medium'>
              <Checkbox
                checked={form.enabled !== false}
                onCheckedChange={(checked) =>
                  setForm({ ...form, enabled: checked === true })
                }
              />
              {t('common.enabled')}
            </label>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className='text-base'>
              {t('scripts.parameters')}
            </CardTitle>
          </CardHeader>
          <CardContent className='grid gap-4'>
            <div className='grid gap-3 sm:grid-cols-[1fr_140px_1fr_auto]'>
              <Input
                placeholder={t('scripts.paramName')}
                value={parameter.name}
                onChange={(event) =>
                  setParameter({ ...parameter, name: event.target.value })
                }
              />
              <Select
                value={parameter.type}
                onValueChange={(value) =>
                  setParameter({ ...parameter, type: value })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {['string', 'number', 'bool', 'secret'].map((type) => (
                    <SelectItem key={type} value={type}>
                      {type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                placeholder={t('scripts.paramDefault')}
                value={parameter.default || ''}
                onChange={(event) =>
                  setParameter({ ...parameter, default: event.target.value })
                }
              />
              <Button type='button' variant='outline' onClick={addParameter}>
                {t('scripts.addParam')}
              </Button>
            </div>
            {(form.parameters || []).map((item, index) => (
              <div
                className='flex items-center gap-3 border-b py-2 text-sm'
                key={`${item.name}-${index}`}
              >
                <span className='font-mono'>{item.name}</span>
                <span className='text-muted-foreground'>{item.type}</span>
                <span className='text-muted-foreground'>
                  {item.default || '-'}
                </span>
                <Button
                  type='button'
                  variant='ghost'
                  size='icon'
                  className='ms-auto size-7'
                  onClick={() =>
                    setForm({
                      ...form,
                      parameters: (form.parameters || []).filter(
                        (_, itemIndex) => itemIndex !== index
                      ),
                    })
                  }
                  aria-label={t('common.remove')}
                >
                  <X className='size-3.5' />
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className='text-base'>
              {t('scripts.environment')}
            </CardTitle>
          </CardHeader>
          <CardContent className='grid gap-4'>
            <div className='grid gap-3 sm:grid-cols-[1fr_1fr_auto]'>
              <Input
                placeholder='KEY'
                value={environment.key}
                onChange={(event) =>
                  setEnvironment({ ...environment, key: event.target.value })
                }
              />
              <Input
                placeholder='VALUE'
                value={environment.value}
                onChange={(event) =>
                  setEnvironment({ ...environment, value: event.target.value })
                }
              />
              <Button type='button' variant='outline' onClick={addEnvironment}>
                {t('common.add')}
              </Button>
            </div>
            {Object.entries(form.environment || {}).map(([key, value]) => (
              <div
                className='flex items-center gap-3 border-b py-2 font-mono text-xs'
                key={key}
              >
                <span>
                  {key}={value}
                </span>
                <Button
                  type='button'
                  variant='ghost'
                  size='icon'
                  className='ms-auto size-7'
                  onClick={() => {
                    const next = { ...(form.environment || {}) }
                    delete next[key]
                    setForm({ ...form, environment: next })
                  }}
                  aria-label={t('common.remove')}
                >
                  <X className='size-3.5' />
                </Button>
              </div>
            ))}
          </CardContent>
        </Card>
        <Separator />
        <div className='flex items-center gap-2'>
            {canWrite ? (
              <Button onClick={save}>{t('common.save')}</Button>
            ) : null}
          <Button asChild variant='outline'>
            <Link to='/scripts'>{t('common.cancel')}</Link>
          </Button>
        </div>
        {editing && (
          <Card>
            <CardHeader>
              <CardTitle className='text-base'>
                {t('scripts.revisionHistory')}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {revisions.data?.length ? (
                <div className='divide-y border'>
                  {revisions.data.map((revision) => (
                    <details className='p-3' key={revision.revision}>
                      <summary className='cursor-pointer text-sm'>
                        <span className='font-mono'>r{revision.revision}</span>
                        <span className='ms-3 text-muted-foreground'>
                          {revision.sha256.slice(0, 16)}…
                        </span>
                      </summary>
                      <pre className='mt-3 max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap'>
                        {revision.content}
                      </pre>
                    </details>
                  ))}
                </div>
              ) : (
                <p className='text-sm text-muted-foreground'>
                  {t('scripts.noRevisions')}
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </EditorShell>
  )
}

export function GroupEditor() {
  const { t } = useTranslation()
  const canWrite = useCanWrite()
  const id = window.location.pathname.split('/')[2] || ''
  const editing = id !== 'new'
  const current = useQuery({
    queryKey: ['group', id],
    queryFn: () => api.get<Group>(`/groups/${id}`),
    enabled: editing,
  })
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const defaultForm: Partial<Group> = {
    name: '',
    description: '',
    type: 'static',
    members: [],
  }
  const [draft, setDraft] = useState<Partial<Group> | null>(null)
  const form = draft || current.data || defaultForm
  const setForm = (next: Partial<Group>) => setDraft(next)
  const [error, setError] = useState('')
  const save = async () => {
    setError('')
    if (!form.name) {
      setError(t('common.name'))
      return
    }
    try {
      if (editing) await api.put(`/groups/${id}`, form)
      else await api.post('/groups', form)
      window.location.assign('/groups')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('common.noData'))
    }
  }
  const members = form.members || []
  return (
    <EditorShell
      title={editing ? t('groups.editTitle') : t('groups.newGroup')}
      description={t('groups.description')}
    >
      <div className='grid max-w-4xl gap-5'>
        <Card>
          <CardContent className='grid gap-5 pt-6'>
            {error && <ErrorState error={error} />}
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.name')}
              <Input
                required
                value={form.name || ''}
                onChange={(event) =>
                  setForm({ ...form, name: event.target.value })
                }
              />
            </label>
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.description')}
              <Input
                value={form.description || ''}
                onChange={(event) =>
                  setForm({ ...form, description: event.target.value })
                }
              />
            </label>
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.type')}
              <Select
                value={form.type || 'static'}
                onValueChange={(value) => setForm({ ...form, type: value })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value='static'>{t('groups.static')}</SelectItem>
                  <SelectItem value='label'>{t('groups.label')}</SelectItem>
                </SelectContent>
              </Select>
            </label>
            {form.type === 'label' ? (
              <div className='grid gap-4 sm:grid-cols-2'>
                <label className='grid gap-2 text-sm font-medium'>
                  {t('groups.labelKey')}
                  <Input
                    value={form.label_key || ''}
                    onChange={(event) =>
                      setForm({ ...form, label_key: event.target.value })
                    }
                  />
                </label>
                <label className='grid gap-2 text-sm font-medium'>
                  {t('groups.labelValue')}
                  <Input
                    value={form.label_value || ''}
                    onChange={(event) =>
                      setForm({ ...form, label_value: event.target.value })
                    }
                  />
                </label>
              </div>
            ) : (
              <div className='grid gap-2'>
                <span className='text-sm font-medium'>
                  {t('groups.members')}
                </span>
                <div className='grid max-h-64 gap-1 overflow-auto rounded-md border p-2'>
                  {(nodes.data || []).map((node) => (
                    <label
                      className='flex items-center gap-2 rounded px-2 py-2 text-sm hover:bg-muted'
                      key={node.id}
                    >
                      <Checkbox
                        checked={members.includes(node.id)}
                        onCheckedChange={(checked) =>
                          setForm({
                            ...form,
                            members: checked
                              ? [...members, node.id]
                              : members.filter((member) => member !== node.id),
                          })
                        }
                      />
                      {node.hostname}
                      <span className='ms-auto text-xs text-muted-foreground'>
                        {node.status}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            <div className='flex items-center gap-2'>
              {canWrite ? (
                <Button onClick={save}>{t('common.save')}</Button>
              ) : null}
              <Button asChild variant='outline'>
                <Link to='/groups'>{t('common.cancel')}</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </EditorShell>
  )
}

export function ApplicationEditor() {
  const { t } = useTranslation()
  const canRun = useCanRun()
  const canWrite = useCanWrite()
  const id = window.location.pathname.split('/')[2] || ''
  const editing = id !== 'new'
  const current = useQuery({
    queryKey: ['application', id],
    queryFn: () => api.get<Application>(`/applications/${id}`),
    enabled: editing,
  })
  const artifacts = useQuery({
    queryKey: ['artifacts'],
    queryFn: () => api.get<Artifact[]>('/artifacts'),
  })
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const states = useQuery({
    queryKey: ['application-state', id],
    queryFn: () => api.get<ApplicationNodeState[]>(`/applications/${id}/state`),
    enabled: editing,
  })
  const executions = useQuery({
    queryKey: ['application-executions', id],
    queryFn: () => api.get<Execution[]>(`/applications/${id}/executions`),
    enabled: editing,
  })
  const assignedNodes = useQuery({
    queryKey: ['application-nodes', id],
    queryFn: () => api.get<string[]>(`/applications/${id}/nodes`),
    enabled: editing,
  })
  const revisions = useQuery({
    queryKey: ['application-revisions', id],
    queryFn: () =>
      api.get<{ revision: number; content: string }[]>(
        `/applications/${id}/revisions`
      ),
    enabled: editing,
  })
  const defaultForm: Partial<Application> = {
    name: '',
    description: '',
    version: '',
    artifact_id: '',
    binary_path: '',
    arguments: [],
    environment: {},
    config: '',
    config_path: '',
    unit_name: '',
    health_check: {
      type: 'systemd',
      target: '',
      timeout: 5,
      attempts: 3,
      interval: 2,
    },
  }
  const [draft, setDraft] = useState<Partial<Application> | null>(null)
  const form = draft || current.data || defaultForm
  const setForm = (next: Partial<Application>) => setDraft(next)
  const [arg, setArg] = useState('')
  const [env, setEnv] = useState({ key: '', value: '' })
  const [selectedNodesDraft, setSelectedNodesDraft] = useState<string[] | null>(
    null
  )
  const selectedNodes = selectedNodesDraft || assignedNodes.data || []
  const setSelectedNodes = (next: string[]) => setSelectedNodesDraft(next)
  const [operation, setOperation] = useState('deploy')
  const [error, setError] = useState('')
  const save = async () => {
    setError('')
    if (!form.name) {
      setError(t('common.name'))
      return
    }
    try {
      const saved = editing
        ? await api.put<Application>(`/applications/${id}`, form)
        : await api.post<Application>('/applications', form)
      const applicationId = saved.id
      if (selectedNodes.length)
        await api.post(`/applications/${applicationId}/assign`, {
          node_ids: selectedNodes,
        })
      if (editing && assignedNodes.data) {
        const removed = assignedNodes.data.filter(
          (nodeId) => !selectedNodes.includes(nodeId)
        )
        if (removed.length)
          await api.post(`/applications/${applicationId}/assign`, {
            node_ids: removed,
            remove: true,
          })
      }
      window.location.assign(`/applications/${applicationId}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('common.noData'))
    }
  }
  const deploy = async () => {
    if (!editing || !selectedNodes.length) return
    setError('')
    try {
      await api.post(`/applications/${id}/deploy`, {
        node_ids: selectedNodes,
        operation,
      })
      await states.refetch()
      await executions.refetch()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('common.noData'))
    }
  }
  const addArg = () => {
    if (!arg.trim()) return
    setForm({ ...form, arguments: [...(form.arguments || []), arg.trim()] })
    setArg('')
  }
  const addEnv = () => {
    if (!env.key.trim()) return
    setForm({
      ...form,
      environment: { ...(form.environment || {}), [env.key.trim()]: env.value },
    })
    setEnv({ key: '', value: '' })
  }
  const health = form.health_check || {
    type: 'systemd',
    target: '',
    timeout: 5,
    attempts: 3,
    interval: 2,
  }
  return (
    <EditorShell
      title={editing ? t('apps.editTitle') : t('apps.newTitle')}
      description={t('apps.formDescription')}
    >
      <div className='grid max-w-4xl gap-6'>
        <Card>
          <CardHeader>
            <CardTitle className='text-base'>{t('apps.title')}</CardTitle>
          </CardHeader>
          <CardContent className='grid gap-5'>
            {error && <ErrorState error={error} />}
            <div className='grid gap-4 sm:grid-cols-2'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('common.name')}
                <Input
                  required
                  value={form.name || ''}
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('common.version')}
                <Input
                  value={form.version || ''}
                  onChange={(event) =>
                    setForm({ ...form, version: event.target.value })
                  }
                />
              </label>
            </div>
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.description')}
              <Input
                value={form.description || ''}
                onChange={(event) =>
                  setForm({ ...form, description: event.target.value })
                }
              />
            </label>
            <div className='grid gap-4 sm:grid-cols-2'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.artifact')}
                <Select
                  value={form.artifact_id || ''}
                  onValueChange={(value) =>
                    setForm({ ...form, artifact_id: value })
                  }
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('apps.selectArtifact')} />
                  </SelectTrigger>
                  <SelectContent>
                    {(artifacts.data || []).map((artifact) => (
                      <SelectItem key={artifact.id} value={artifact.id}>
                        {artifact.name} {artifact.version}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.binaryPath')}
                <Input
                  className='font-mono text-xs'
                  value={form.binary_path || ''}
                  onChange={(event) =>
                    setForm({ ...form, binary_path: event.target.value })
                  }
                />
              </label>
            </div>
            <div className='grid gap-4 sm:grid-cols-2'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.configPath')}
                <Input
                  className='font-mono text-xs'
                  value={form.config_path || ''}
                  onChange={(event) =>
                    setForm({ ...form, config_path: event.target.value })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.unit')}
                <Input
                  className='font-mono text-xs'
                  value={form.unit_name || ''}
                  onChange={(event) =>
                    setForm({ ...form, unit_name: event.target.value })
                  }
                />
              </label>
            </div>
            <label className='grid gap-2 text-sm font-medium'>
              {t('apps.configContent')}
              <Textarea
                className='min-h-40 font-mono text-xs'
                value={form.config || ''}
                onChange={(event) =>
                  setForm({ ...form, config: event.target.value })
                }
              />
            </label>
            <div className='grid gap-3'>
              <span className='text-sm font-medium'>{t('apps.arguments')}</span>
              <div className='flex items-center gap-2'>
                <Input
                  className='font-mono text-xs'
                  value={arg}
                  onChange={(event) => setArg(event.target.value)}
                  placeholder='--port 8080'
                />
                <Button type='button' variant='outline' onClick={addArg}>
                  <Plus className='me-1 size-4' />
                  {t('common.add')}
                </Button>
              </div>
              {(form.arguments || []).map((value, index) => (
                <div
                  className='flex items-center gap-2 border-b py-2 font-mono text-xs'
                  key={`${value}-${index}`}
                >
                  {value}
                  <Button
                    type='button'
                    variant='ghost'
                    size='icon'
                    className='ms-auto size-7'
                    onClick={() =>
                      setForm({
                        ...form,
                        arguments: (form.arguments || []).filter(
                          (_, itemIndex) => itemIndex !== index
                        ),
                      })
                    }
                    aria-label={t('common.remove')}
                  >
                    <X className='size-3.5' />
                  </Button>
                </div>
              ))}
            </div>
            <div className='grid gap-3'>
              <span className='text-sm font-medium'>{t('apps.env')}</span>
              <div className='grid gap-2 sm:grid-cols-[1fr_1fr_auto]'>
                <Input
                  className='font-mono text-xs'
                  value={env.key}
                  onChange={(event) =>
                    setEnv({ ...env, key: event.target.value })
                  }
                  placeholder='KEY'
                />
                <Input
                  className='font-mono text-xs'
                  value={env.value}
                  onChange={(event) =>
                    setEnv({ ...env, value: event.target.value })
                  }
                  placeholder='VALUE'
                />
                <Button type='button' variant='outline' onClick={addEnv}>
                  <Plus className='me-1 size-4' />
                  {t('common.add')}
                </Button>
              </div>
              {Object.entries(form.environment || {}).map(([key, value]) => (
                <div
                  className='flex items-center gap-2 border-b py-2 font-mono text-xs'
                  key={key}
                >
                  {key}={value}
                  <Button
                    type='button'
                    variant='ghost'
                    size='icon'
                    className='ms-auto size-7'
                    onClick={() => {
                      const next = { ...(form.environment || {}) }
                      delete next[key]
                      setForm({ ...form, environment: next })
                    }}
                    aria-label={t('common.remove')}
                  >
                    <X className='size-3.5' />
                  </Button>
                </div>
              ))}
            </div>
            <Separator />
            <div className='grid gap-4 sm:grid-cols-2'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.healthCheckType')}
                <Select
                  value={health.type}
                  onValueChange={(value) =>
                    setForm({
                      ...form,
                      health_check: { ...health, type: value },
                    })
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {['systemd', 'tcp', 'http', 'command'].map((type) => (
                      <SelectItem key={type} value={type}>
                        {type}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.healthCheckTarget')}
                <Input
                  className='font-mono text-xs'
                  value={health.target}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      health_check: { ...health, target: event.target.value },
                    })
                  }
                />
              </label>
            </div>
            <div className='grid gap-4 sm:grid-cols-3'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.healthTimeout')}
                <Input
                  type='number'
                  min={1}
                  value={health.timeout}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      health_check: {
                        ...health,
                        timeout: Number(event.target.value),
                      },
                    })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.healthAttempts')}
                <Input
                  type='number'
                  min={1}
                  value={health.attempts}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      health_check: {
                        ...health,
                        attempts: Number(event.target.value),
                      },
                    })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('apps.healthInterval')}
                <Input
                  type='number'
                  min={1}
                  value={health.interval}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      health_check: {
                        ...health,
                        interval: Number(event.target.value),
                      },
                    })
                  }
                />
              </label>
            </div>
            <div className='flex items-center gap-2'>
              {canWrite ? (
                <Button onClick={save}>{t('common.save')}</Button>
              ) : null}
              <Button asChild variant='outline'>
                <Link to='/applications'>{t('common.cancel')}</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className='text-base'>{t('apps.assignNodes')}</CardTitle>
          </CardHeader>
          <CardContent className='grid max-h-64 gap-1 overflow-auto'>
            {(nodes.data || []).map((node) => (
              <label
                className='flex items-center gap-2 rounded px-2 py-2 text-sm hover:bg-muted'
                key={node.id}
              >
                <Checkbox
                  checked={selectedNodes.includes(node.id)}
                  disabled={!canWrite}
                  onCheckedChange={(checked) =>
                    setSelectedNodes(
                      checked
                        ? [...selectedNodes, node.id]
                        : selectedNodes.filter((nodeId) => nodeId !== node.id)
                    )
                  }
                />
                {node.hostname}
                <span className='ms-auto text-xs text-muted-foreground'>
                  {node.status}
                </span>
              </label>
            ))}
          </CardContent>
        </Card>
        {editing && (
          <>
            {canRun ? (
              <Card>
              <CardHeader>
                <CardTitle className='text-base'>{t('apps.deploy')}</CardTitle>
              </CardHeader>
              <CardContent className='flex flex-wrap gap-2'>
                <Select
                  value={operation}
                  onValueChange={setOperation}
                  disabled={!canRun}
                >
                  <SelectTrigger className='w-40'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {['deploy', 'start', 'stop', 'restart', 'upgrade'].map(
                      (value) => (
                        <SelectItem key={value} value={value}>
                          {value}
                        </SelectItem>
                      )
                    )}
                  </SelectContent>
                </Select>
                <Button
                  onClick={deploy}
                  disabled={!canRun || !selectedNodes.length}
                >
                  {t('apps.deploy')}
                </Button>
              </CardContent>
              </Card>
            ) : null}
            <Card>
              <CardHeader>
                <CardTitle className='text-base'>{t('apps.health')}</CardTitle>
              </CardHeader>
              <CardContent>
                {states.data?.length ? (
                  <div className='divide-y'>
                    {states.data.map((state) => (
                      <div
                        className='grid grid-cols-[1fr_auto] gap-2 py-2 text-sm'
                        key={state.node_id}
                      >
                        <span className='font-mono text-xs'>
                          {state.node_id}
                        </span>
                        <StatusBadge status={state.health} />
                        <span className='text-xs text-muted-foreground'>
                          {state.error || state.operation}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className='text-sm text-muted-foreground'>
                    {t('apps.noHealth')}
                  </p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className='text-base'>
                  {t('apps.executionHistory')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {executions.data?.length ? (
                  <div className='divide-y'>
                    {executions.data.map((execution) => (
                      <div
                        className='grid grid-cols-[1fr_auto] gap-2 py-2 text-sm'
                        key={execution.id}
                      >
                        <span className='font-mono text-xs'>
                          {execution.id.slice(0, 12)}…
                        </span>
                        <StatusText value={execution.status} />
                        <span className='text-xs text-muted-foreground'>
                          {execution.node_id}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className='text-sm text-muted-foreground'>
                    {t('apps.noExecutions')}
                  </p>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className='text-base'>
                  {t('apps.versions')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {revisions.data?.length ? (
                  <div className='divide-y border'>
                    {revisions.data.map((revision) => (
                      <details className='p-3' key={revision.revision}>
                        <summary className='cursor-pointer text-sm'>
                          <span className='font-mono'>
                            r{revision.revision}
                          </span>
                        </summary>
                        <pre className='mt-3 max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap'>
                          {revision.content}
                        </pre>
                      </details>
                    ))}
                  </div>
                ) : (
                  <p className='text-sm text-muted-foreground'>
                    {t('apps.noVersions')}
                  </p>
                )}
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </EditorShell>
  )
}

function StatusText({ value }: { value: string }) {
  return (
    <span className='text-sm'>
      <span className='me-1.5 inline-block size-1.5 rounded-full bg-current align-middle' />
      {value}
    </span>
  )
}

export function ArtifactEditor() {
  const { t } = useTranslation()
  const canWrite = useCanWrite()
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [fileName, setFileName] = useState('')
  const upload = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setUploading(true)
    setError('')
    try {
      const response = await fetch('/api/artifacts', {
        method: 'POST',
        headers: { Authorization: `Bearer ${getToken() || ''}` },
        body: new FormData(event.currentTarget),
      })
      if (!response.ok)
        throw new Error(`${t('artifacts.uploadFailed')}: ${response.status}`)
      window.location.assign('/artifacts')
    } catch (err) {
      setError(err instanceof Error ? err.message : t('artifacts.uploadFailed'))
    } finally {
      setUploading(false)
    }
  }
  return (
    <EditorShell
      title={t('common.upload')}
      description={t('artifacts.description')}
    >
      <Card className='max-w-4xl'>
        <CardContent className='pt-6'>
          <form className='grid gap-5' onSubmit={upload}>
            {error && <ErrorState error={error} />}
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.name')}
              <Input name='name' required />
            </label>
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.version')}
              <Input name='version' required />
            </label>
            <label className='grid gap-2 text-sm font-medium'>
              {t('artifacts.archField')}
              <select
                className='h-9 rounded-md border bg-background px-3 text-sm'
                name='architecture'
                defaultValue='amd64'
              >
                <option value='amd64'>amd64</option>
                <option value='arm64'>arm64</option>
              </select>
            </label>
            <label
              htmlFor='artifact-file'
              className='grid cursor-pointer gap-2 text-sm font-medium'
            >
              {t('artifacts.file')}
              <span className='flex h-9 items-center rounded-md border px-3 text-sm font-normal'>
                <span className='truncate'>
                  {fileName || t('artifacts.selectFile')}
                </span>
              </span>
              <input
                id='artifact-file'
                type='file'
                name='file'
                required
                className='sr-only'
                aria-label={t('artifacts.file')}
                onChange={(event) =>
                  setFileName(event.target.files?.[0]?.name || '')
                }
              />
            </label>
            <div className='flex items-center gap-2'>
              {canWrite ? (
                <Button type='submit' disabled={uploading}>
                  {uploading ? t('common.uploading') : t('common.upload')}
                </Button>
              ) : null}
              <Button asChild variant='outline'>
                <Link to='/artifacts'>{t('common.cancel')}</Link>
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </EditorShell>
  )
}
