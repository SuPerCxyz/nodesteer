import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { Condition, Node, RemoteCondition, Task } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

const emptyRemote = (): RemoteCondition => ({
  node_id: '',
  property: 'online',
  task_id: '',
  operator: '==',
  value: 'online',
})

/**
 * 调度计划远程条件编辑器。
 * 支持多条远程条件；后端以 remote 或 and(remote...) 持久化。
 */
export function RemoteConditionEditor({
  value,
  onChange,
  nodes,
  tasks,
}: {
  value?: Condition
  onChange: (condition: Condition | undefined) => void
  nodes: Node[]
  tasks: Task[]
}) {
  const { t } = useTranslation()
  const items: Condition[] =
    value?.type === 'and' ? value.and || [] : value ? [value] : []
  const remoteItems = items.filter((item) => item.type === 'remote')
  const [draft, setDraft] = useState<RemoteCondition>(emptyRemote())

  const emit = (next: Condition[]) => {
    if (!next.length) {
      onChange(undefined)
      return
    }
    onChange(next.length === 1 ? next[0] : { type: 'and', and: next })
  }

  const add = () => {
    if (!draft.node_id) return
    const next: Condition = { type: 'remote', remote: { ...draft } }
    emit([...remoteItems, next])
    setDraft(emptyRemote())
  }

  const remove = (index: number) => {
    emit(remoteItems.filter((_, i) => i !== index))
  }

  return (
    <div className='grid gap-3'>
      {remoteItems.length ? (
        <div className='grid gap-2'>
          {remoteItems.map((item, index) => {
            const remote = item.remote
            if (!remote) return null
            const nodeName =
              nodes.find((node) => node.id === remote.node_id)?.hostname ||
              remote.node_id
            const taskName =
              tasks.find((task) => task.id === remote.task_id)?.name ||
              remote.task_id
            return (
              <div
                className='flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm'
                key={`${remote.node_id}-${remote.property}-${index}`}
              >
                <span className='font-mono text-xs'>
                  {nodeName}.{remote.property}
                  {remote.property === 'last_execution' && taskName
                    ? `(${taskName})`
                    : ''}{' '}
                  {remote.operator} {remote.value}
                </span>
                <Button
                  className='ms-auto'
                  size='sm'
                  type='button'
                  variant='ghost'
                  onClick={() => remove(index)}
                >
                  {t('common.delete')}
                </Button>
              </div>
            )
          })}
        </div>
      ) : (
        <p className='text-sm text-muted-foreground'>
          {t('schedules.noRemoteConditions')}
        </p>
      )}
      <div className='grid gap-3 sm:grid-cols-2'>
        <label className='grid gap-2 text-sm font-medium'>
          {t('tasks.conditionNode')}
          <Select
            value={draft.node_id}
            onValueChange={(node_id) => setDraft({ ...draft, node_id })}
          >
            <SelectTrigger className='w-full'>
              <SelectValue placeholder={t('tasks.selectNode')} />
            </SelectTrigger>
            <SelectContent>
              {nodes.map((node) => (
                <SelectItem key={node.id} value={node.id}>
                  {node.hostname}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className='grid gap-2 text-sm font-medium'>
          {t('tasks.conditionProperty')}
          <Select
            value={draft.property}
            onValueChange={(property) => setDraft({ ...draft, property })}
          >
            <SelectTrigger className='w-full'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='online'>online</SelectItem>
              <SelectItem value='last_execution'>
                {t('tasks.lastExecution')}
              </SelectItem>
            </SelectContent>
          </Select>
        </label>
        {draft.property === 'last_execution' ? (
          <label className='grid gap-2 text-sm font-medium'>
            {t('tasks.conditionTaskId')}
            <Select
              value={draft.task_id}
              onValueChange={(task_id) => setDraft({ ...draft, task_id })}
            >
              <SelectTrigger className='w-full'>
                <SelectValue placeholder={t('schedules.selectTask')} />
              </SelectTrigger>
              <SelectContent>
                {tasks.map((task) => (
                  <SelectItem key={task.id} value={task.id}>
                    {task.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        ) : null}
        <label className='grid gap-2 text-sm font-medium'>
          {t('tasks.conditionOperator')}
          <Select
            value={draft.operator}
            onValueChange={(operator) => setDraft({ ...draft, operator })}
          >
            <SelectTrigger className='w-full'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='=='>==</SelectItem>
              <SelectItem value='!='>!=</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <label className='grid gap-2 text-sm font-medium'>
          {t('tasks.remoteConditionValue')}
          <Select
            value={draft.value}
            onValueChange={(value) => setDraft({ ...draft, value })}
          >
            <SelectTrigger className='w-full'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='online'>online</SelectItem>
              <SelectItem value='offline'>offline</SelectItem>
              <SelectItem value='pending'>pending</SelectItem>
              <SelectItem value='maintenance'>maintenance</SelectItem>
              <SelectItem value='disabled'>disabled</SelectItem>
              <SelectItem value='SUCCESS'>SUCCESS</SelectItem>
              <SelectItem value='FAILED'>FAILED</SelectItem>
              <SelectItem value='RUNNING'>RUNNING</SelectItem>
              <SelectItem value='SKIPPED'>SKIPPED</SelectItem>
              <SelectItem value='CANCELED'>CANCELED</SelectItem>
              <SelectItem value='TIMED_OUT'>TIMED_OUT</SelectItem>
              <SelectItem value='BLOCKED'>BLOCKED</SelectItem>
            </SelectContent>
          </Select>
        </label>
      </div>
      <Button className='w-fit' type='button' variant='outline' onClick={add}>
        {t('schedules.addRemoteCondition')}
      </Button>
    </div>
  )
}
