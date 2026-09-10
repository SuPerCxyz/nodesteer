import { beforeEach, describe, expect, it, vi } from 'vitest'
import { copyText, selectText } from './clipboard'

describe('copyText', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: false,
    })
  })

  it('uses the synchronous fallback first on HTTP origins', async () => {
    const execCommand = vi.spyOn(document, 'execCommand').mockReturnValue(true)
    const writeText = vi.fn().mockRejectedValue(new Error('permission denied'))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    await expect(copyText('complete command')).resolves.toBe(false)

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(writeText).not.toHaveBeenCalled()
    execCommand.mockRestore()
  })

  it('reports a successful write on secure origins', async () => {
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: true,
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    await expect(copyText('complete command')).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('complete command')
  })

  it('selects a manual fallback target', () => {
    const target = document.createElement('pre')
    target.textContent = 'complete command'
    document.body.append(target)

    expect(selectText(target)).toBe(true)
    expect(window.getSelection()?.toString()).toBe('complete command')
    target.remove()
  })
})
