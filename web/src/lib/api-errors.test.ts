import i18n from '@/i18n'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { ApiError } from './api'
import { formatApiError } from './api-errors'

const ARTIFACT = '5b313720-2727-46fa-b0c2-e18727d9b979'
const APPLICATION = '4c10b536-14ca-4b1f-bc6f-fa36da603d8b'
const SCRIPT = 'a1b1c1d1-1111-4222-8333-444455556666'
const TASK = 'b2c962eb-2222-4333-8444-555566667777'
const GROUP = 'c0ac39f0-3333-4444-8555-666677778888'
const NODE = '7a60b813-4444-4555-8666-777788889999'
const TRANSFER = 'd9e8f7a6-5555-4666-8777-888899990000'

describe('formatApiError', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  afterAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('把制品引用阻断翻译为中文，并保留引用方类型与 ID', () => {
    const message = formatApiError(
      new ApiError(
        409,
        `artifact ${ARTIFACT} is referenced by application ${APPLICATION}`
      )
    )

    expect(message).toContain('无法删除该发布包')
    expect(message).toContain('托管应用')
    expect(message).toContain(APPLICATION)
  })

  it('覆盖脚本、任务、分组、节点、应用、调度的引用阻断', () => {
    expect(
      formatApiError(`script ${SCRIPT} is referenced by task ${TASK}`)
    ).toContain('无法删除该脚本')
    expect(
      formatApiError(`application ${APPLICATION} is referenced by task ${TASK}`)
    ).toContain('无法删除该托管应用')
    expect(
      formatApiError(`task ${TASK} is referenced by schedule ${TRANSFER}`)
    ).toContain('无法删除该任务')
    expect(
      formatApiError(`group ${GROUP} is referenced by task ${TASK}`)
    ).toContain('无法删除该分组')
    expect(
      formatApiError(`node ${NODE} is referenced by group ${GROUP}`)
    ).toContain('无法删除该节点')
  })

  it('翻译 cron 字段数错误', () => {
    const message = formatApiError(
      'invalid cron expression: expected exactly 5 fields, found 6: [* * * * * *]'
    )

    expect(message).toBe(
      'Cron 表达式无效：需要 5 个字段（分 时 日 月 周），实际收到 6 个。'
    )
  })

  it('翻译其他 cron 解析错误', () => {
    expect(
      formatApiError(
        'invalid cron expression: end of range (9) above maximum (7): 9'
      )
    ).toBe('Cron 表达式无效：end of range (9) above maximum (7): 9')
  })

  it('翻译节点仍有活动传输错误', () => {
    const message = formatApiError(
      `node ${NODE} has active file transfer ${TRANSFER}`
    )

    expect(message).toContain(NODE)
    expect(message).toContain(TRANSFER)
    expect(message).toContain('进行中的文件传输')
  })

  it('翻译重复用户名与其他唯一约束错误', () => {
    expect(formatApiError('UNIQUE constraint failed: users.username')).toBe(
      '用户名已存在，请更换后重试。'
    )
    expect(formatApiError('UNIQUE constraint failed: applications.name')).toBe(
      'applications.name 已存在，请勿重复提交。'
    )
  })

  it('翻译 404 与 403 的通用错误', () => {
    expect(formatApiError('not found', 404)).toBe('资源不存在或已被删除。')
    expect(formatApiError('permission denied', 403)).toBe(
      '没有权限执行此操作。'
    )
    expect(formatApiError(new ApiError(404, 'not found'))).toBe(
      '资源不存在或已被删除。'
    )
  })

  it('带上资源类型的 404 错误显示具体资源', () => {
    expect(formatApiError(new ApiError(404, 'node not found'))).toBe(
      '节点不存在或已被删除。'
    )
    expect(formatApiError(new ApiError(404, 'script not found'))).toBe(
      '脚本不存在或已被删除。'
    )
    expect(formatApiError(new ApiError(404, 'application not found'))).toBe(
      '托管应用不存在或已被删除。'
    )
  })

  it('未识别的错误保持原文', () => {
    expect(formatApiError('node_ip must be valid')).toBe(
      'node_ip must be valid'
    )
    expect(formatApiError(new Error('network down'))).toBe('network down')
  })

  it('空输入返回空字符串', () => {
    expect(formatApiError(undefined)).toBe('')
    expect(formatApiError('')).toBe('')
  })

  it('ApiError 保留后端原文便于排查', () => {
    const error = new ApiError(
      409,
      `artifact ${ARTIFACT} is referenced by application ${APPLICATION}`
    )

    expect(error.rawMessage).toBe(
      `artifact ${ARTIFACT} is referenced by application ${APPLICATION}`
    )
    expect(error.status).toBe(409)
  })

  it('切换到英文后输出英文案', async () => {
    await i18n.changeLanguage('en')
    try {
      expect(formatApiError('not found', 404)).toBe(
        'The resource does not exist or has been deleted.'
      )
      expect(formatApiError('UNIQUE constraint failed: users.username')).toBe(
        'This username is already taken. Choose another one.'
      )
    } finally {
      await i18n.changeLanguage('zh')
    }
  })

  it('重名与重复参数映射为本地化文案', () => {
    expect(formatApiError('script name already exists: demo')).toContain('demo')
    expect(formatApiError('task name already exists: demo')).toContain('demo')
    expect(formatApiError('parameter name is required')).toBeTruthy()
    expect(formatApiError('duplicate parameter name: msg')).toContain('msg')
    expect(formatApiError('task name is required')).toBeTruthy()
  })
})
