type Stream = 'stdout' | 'stderr'

export type LogChunk = { stream: string; seq: number; chunk: string }

/** 保留已收到的历史；终态摘要未被涵盖时独立补充，不推断日志块丢失。 */
export function executionLogView({
  chunks,
  stream,
  terminal,
  stdout = '',
  stderr = '',
  stdoutTruncated = false,
  stderrTruncated = false,
}: {
  chunks: LogChunk[]
  stream: Stream | 'all'
  terminal: boolean
  stdout?: string
  stderr?: string
  stdoutTruncated?: boolean
  stderrTruncated?: boolean
}) {
  const ordered = chunks.slice().sort((a, b) => a.seq - b.seq)
  const content = ordered
    .filter((chunk) => stream === 'all' || chunk.stream === stream)
    .map((chunk) => chunk.chunk)
    .join('')
  const supplements = (['stdout', 'stderr'] as const).flatMap((name) => {
    if (stream !== 'all' && stream !== name) return []
    const summary = name === 'stdout' ? stdout : stderr
    const received = ordered
      .filter((chunk) => chunk.stream === name)
      .map((chunk) => chunk.chunk)
      .join('')
    if (!terminal || !summary || received.includes(summary)) return []
    return [
      {
        stream: name,
        content: summary,
        truncated: name === 'stdout' ? stdoutTruncated : stderrTruncated,
      },
    ]
  })
  return { content, supplements }
}
