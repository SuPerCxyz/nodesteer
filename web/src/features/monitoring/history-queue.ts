let historyInFlight = 0
const historyQueue: (() => void)[] = []

export function scheduleHistory<T>(
  signal: AbortSignal,
  run: () => Promise<T>
): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => {
      const index = historyQueue.indexOf(start)
      if (index >= 0) historyQueue.splice(index, 1)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const start = () => {
      signal.removeEventListener('abort', abort)
      if (signal.aborted) {
        abort()
        return
      }
      historyInFlight++
      void run()
        .then(resolve, reject)
        .finally(() => {
          historyInFlight--
          historyQueue.shift()?.()
        })
    }
    if (signal.aborted) {
      abort()
      return
    }
    if (historyInFlight < 6) start()
    else {
      historyQueue.push(start)
      signal.addEventListener('abort', abort, { once: true })
    }
  })
}
