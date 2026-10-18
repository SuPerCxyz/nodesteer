import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, authenticatedFetch, getToken, setToken } from './api'

const unauthorized = vi.fn()

describe('认证请求的会话失效边界', () => {
  beforeEach(() => {
    setToken('old-session')
    unauthorized.mockReset()
    window.addEventListener('nodesteer-unauthorized', unauthorized)
  })
  afterEach(() => {
    window.removeEventListener('nodesteer-unauthorized', unauthorized)
    setToken(null)
    vi.unstubAllGlobals()
  })

  it('JSON请求401清Token并发送统一事件，403/500保留会话', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    for (const status of [403, 500]) {
      fetch.mockResolvedValueOnce(new Response('{}', { status }))
      await expect(api.get('/private')).rejects.toMatchObject({ status })
      expect(getToken()).toBe('old-session')
    }
    fetch.mockResolvedValueOnce(new Response('{}', { status: 401 }))
    await expect(api.get('/private')).rejects.toMatchObject({ status: 401 })
    expect(getToken()).toBeNull()
    expect(unauthorized).toHaveBeenCalledTimes(1)
  })

  it('上传/下载共用401处理，上传不覆盖浏览器multipart Content-Type', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }))
    vi.stubGlobal('fetch', fetch)
    await authenticatedFetch('/api/artifacts', {
      method: 'POST',
      body: new FormData(),
    })
    const headers = fetch.mock.calls[0][1].headers as Headers
    expect(headers.get('Authorization')).toBe('Bearer old-session')
    expect(headers.has('Content-Type')).toBe(false)
    expect(getToken()).toBeNull()
    expect(unauthorized).toHaveBeenCalledTimes(1)
  })

  it('旧请求的迟到401不得注销已经建立的新会话', async () => {
    let resolve!: (value: Response) => void
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise<Response>((done) => {
            resolve = done
          })
      )
    )
    const pending = authenticatedFetch('/api/private')
    setToken('new-session')
    resolve(new Response(null, { status: 401 }))
    await pending
    expect(getToken()).toBe('new-session')
    expect(unauthorized).not.toHaveBeenCalled()
  })
})
