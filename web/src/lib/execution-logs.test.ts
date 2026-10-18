import { describe, expect, it } from 'vitest'
import { executionLogView, type LogChunk } from './execution-logs'

const view = (
  chunks: LogChunk[],
  options: Partial<Parameters<typeof executionLogView>[0]> = {}
) => executionLogView({ chunks, terminal: true, stream: 'all', ...options })

describe('终态日志与已收到的历史', () => {
  it('失败重试后成功：保留此前attempt的stdout/stderr及seq顺序', () => {
    const result = view(
      [
        { stream: 'stdout', seq: 2, chunk: 'second success\n' },
        { stream: 'stderr', seq: 1, chunk: 'first failure\n' },
        { stream: 'stdout', seq: 0, chunk: 'first attempt\n' },
      ],
      { stdout: 'second success\n', stderr: '' }
    )
    expect(result.content).toBe(
      'first attempt\nfirst failure\nsecond success\n'
    )
    expect(result.supplements).toEqual([])
  })

  it('多流交错按seq输出，单流选择保留各自顺序', () => {
    const chunks = [
      { stream: 'stderr', seq: 3, chunk: 'D' },
      { stream: 'stdout', seq: 2, chunk: 'C' },
      { stream: 'stderr', seq: 1, chunk: 'B' },
      { stream: 'stdout', seq: 0, chunk: 'A' },
    ]
    expect(view(chunks, { stdout: 'AC', stderr: 'BD' }).content).toBe('ABCD')
    expect(
      view(chunks, { stream: 'stderr', stdout: 'AC', stderr: 'BD' })
    ).toEqual({ content: 'BD', supplements: [] })
  })

  it('终态摘要截断不缩短完整长chunks', () => {
    const output = 'long-output\n'.repeat(10000)
    const result = view([{ stream: 'stdout', seq: 0, chunk: output }], {
      stdout: output.slice(0, 512),
      stdoutTruncated: true,
    })
    expect(result.content).toBe(output)
    expect(result.supplements).toEqual([])
  })

  it('摘要未被历史涵盖：保留历史并独立补充终态完整输出', () => {
    const result = view([{ stream: 'stdout', seq: 0, chunk: 'first\n' }], {
      stdout: 'first\nsecond\n',
    })
    expect(result.content).toBe('first\n')
    expect(result.supplements).toEqual([
      { stream: 'stdout', content: 'first\nsecond\n', truncated: false },
    ])
  })

  it('不确定片段缺失时仍保留历史，补充摘要显式标记自身截断', () => {
    const result = view(
      [{ stream: 'stdout', seq: 0, chunk: 'prior attempt\n' }],
      { stdout: 'final partial', stdoutTruncated: true }
    )
    expect(result.content).toBe('prior attempt\n')
    expect(result.supplements).toEqual([
      { stream: 'stdout', content: 'final partial', truncated: true },
    ])
  })
})
