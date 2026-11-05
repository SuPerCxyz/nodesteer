import { useTranslation } from 'react-i18next'
import type { Group, Node, Target } from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { statusLabel } from '@/lib/status-labels'
import { normalizeTarget } from '@/lib/targets'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

/**
 * 共享目标编辑器：节点 / 分组 / 标签三种目标类型。
 * 任务试运行与调度计划复用，避免各自维护一套选择逻辑。
 */
export function TargetEditor({
  target,
  onChange,
  nodes,
  groups,
  disabled = false,
}: {
  target?: Target
  onChange: (target: Target) => void
  nodes: Node[]
  groups: Group[]
  disabled?: boolean
}) {
  const { t } = useTranslation()
  const value = normalizeTarget(target)
  const selectedNodes = value.node_ids || []
  const selectedGroups = value.group_ids || []

  const setType = (type: string) => {
    onChange(normalizeTarget({ ...value, type }))
  }

  return (
    <div className='grid gap-3'>
      <div className='grid gap-3 sm:grid-cols-2'>
        <label className='grid gap-2 text-sm font-medium'>
          {t('targets.type')}
          <Select
            value={value.type}
            onValueChange={setType}
            disabled={disabled}
          >
            <SelectTrigger className='w-full'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='node'>{t('targets.node')}</SelectItem>
              <SelectItem value='group'>{t('targets.group')}</SelectItem>
              <SelectItem value='label'>{t('targets.label')}</SelectItem>
            </SelectContent>
          </Select>
        </label>
        <p className='self-end text-xs text-muted-foreground'>
          {t('targets.hint')}
        </p>
      </div>
      {value.type === 'node' ? (
        <div className='grid max-h-56 gap-1 overflow-auto rounded-md border p-2'>
          {nodes.length ? (
            nodes.map((node) => (
              <label
                className='flex items-center gap-2 rounded px-2 py-2 text-sm hover:bg-muted'
                key={node.id}
              >
                <Checkbox
                  checked={selectedNodes.includes(node.id)}
                  disabled={disabled}
                  onCheckedChange={(checked) =>
                    onChange({
                      type: 'node',
                      node_ids: checked
                        ? [...selectedNodes, node.id]
                        : selectedNodes.filter((id) => id !== node.id),
                    })
                  }
                />
                {nodeDisplayName(node, t('common.unknownNode'))}
                <span className='ms-auto text-xs text-muted-foreground'>
                  {statusLabel(node.status)}
                </span>
              </label>
            ))
          ) : (
            <span className='px-2 py-1 text-sm text-muted-foreground'>
              {t('targets.noNodes')}
            </span>
          )}
        </div>
      ) : null}
      {value.type === 'group' ? (
        <div className='grid max-h-48 gap-1 overflow-auto rounded-md border p-2'>
          {groups.length ? (
            groups.map((group) => (
              <label
                className='flex items-center gap-2 rounded px-2 py-2 text-sm hover:bg-muted'
                key={group.id}
              >
                <Checkbox
                  checked={selectedGroups.includes(group.id)}
                  disabled={disabled}
                  onCheckedChange={(checked) =>
                    onChange({
                      type: 'group',
                      group_ids: checked
                        ? [...selectedGroups, group.id]
                        : selectedGroups.filter((id) => id !== group.id),
                    })
                  }
                />
                {group.name}
              </label>
            ))
          ) : (
            <span className='px-2 py-1 text-sm text-muted-foreground'>
              {t('targets.noGroups')}
            </span>
          )}
        </div>
      ) : null}
      {value.type === 'label' ? (
        <div className='grid gap-4 sm:grid-cols-2'>
          <label className='grid gap-2 text-sm font-medium'>
            {t('targets.labelKey')}
            <Input
              value={value.label_key || ''}
              disabled={disabled}
              onChange={(event) =>
                onChange({
                  type: 'label',
                  label_key: event.target.value,
                  label_value: value.label_value || '',
                })
              }
            />
          </label>
          <label className='grid gap-2 text-sm font-medium'>
            {t('targets.labelValue')}
            <Input
              value={value.label_value || ''}
              disabled={disabled}
              onChange={(event) =>
                onChange({
                  type: 'label',
                  label_key: value.label_key || '',
                  label_value: event.target.value,
                })
              }
            />
          </label>
        </div>
      ) : null}
    </div>
  )
}
