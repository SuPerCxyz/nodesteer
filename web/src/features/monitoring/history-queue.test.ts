import { describe, expect, it } from 'vitest'
import { scheduleHistory } from './history-queue'

describe('history request queue', () => {
  it('limits concurrency and cancels queued work without truncating the rest', async () => {
    let active = 0,
      peak = 0,
      calls = 0
    const release: (() => void)[] = []
    const controls = Array.from({ length: 9 }, () => new AbortController())
    const requests = controls.map((control) =>
      scheduleHistory(
        control.signal,
        () =>
          new Promise<number>((resolve) => {
            calls++
            active++
            peak = Math.max(peak, active)
            release.push(() => {
              active--
              resolve(1)
            })
          })
      )
    )
    const aborted = requests[6].catch((err: Error) => err.name)
    controls[6].abort()
    expect(await aborted).toBe('AbortError')
    expect(calls).toBe(6)
    release.splice(0).forEach((done) => done())
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(calls).toBe(8)
    release.splice(0).forEach((done) => done())
    await Promise.all(requests.filter((_, index) => index !== 6))
    expect(peak).toBe(6)
  })

  it('releases an active slot when the request observes abort', async () => {
    const active = new AbortController()
    let started = false
    const pending = scheduleHistory(
      active.signal,
      () =>
        new Promise<never>((_resolve, reject) => {
          active.signal.addEventListener(
            'abort',
            () => reject(new DOMException('Aborted', 'AbortError')),
            { once: true }
          )
        })
    ).catch((error: Error) => error.name)
    const blockers = Array.from({ length: 5 }, () => new AbortController())
    const waits = blockers.map((control) =>
      scheduleHistory(
        control.signal,
        () =>
          new Promise<void>((_resolve, reject) => {
            control.signal.addEventListener(
              'abort',
              () => reject(new DOMException('Aborted', 'AbortError')),
              { once: true }
            )
          })
      ).catch(() => {})
    )
    const next = scheduleHistory(new AbortController().signal, async () => {
      started = true
    })
    expect(started).toBe(false)
    active.abort()
    expect(await pending).toBe('AbortError')
    await next
    expect(started).toBe(true)
    blockers.forEach((control) => control.abort())
    await Promise.all(waits)
  })
})
