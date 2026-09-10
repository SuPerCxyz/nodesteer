export async function copyText(value: string): Promise<boolean> {
  if (!value) throw new Error('nothing to copy')

  const legacyCopy = () => {
    const textarea = document.createElement('textarea')
    textarea.value = value
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    textarea.style.pointerEvents = 'none'
    document.body.appendChild(textarea)
    try {
      textarea.focus()
      textarea.select()
      textarea.setSelectionRange(0, textarea.value.length)
      return document.execCommand('copy')
    } finally {
      textarea.remove()
    }
  }

  // HTTP origins are not allowed to use the asynchronous Clipboard API. Keep
  // the legacy call synchronous while the click gesture is still active.
  if (!window.isSecureContext) {
    if (legacyCopy()) return false
    throw new Error('clipboard copy failed')
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch {
    // Fall through to the legacy path when permission is denied.
  }

  // execCommand may report success on HTTP while the browser still refuses to
  // update the system clipboard. Callers must treat this as a manual fallback,
  // not as proof that the value was copied.
  if (legacyCopy()) return false
  throw new Error('clipboard copy failed')
}

export function selectText(element: HTMLElement | null): boolean {
  if (!element) return false
  const selection = window.getSelection()
  if (!selection) return false
  const range = document.createRange()
  range.selectNodeContents(element)
  selection.removeAllRanges()
  selection.addRange(range)
  return true
}
