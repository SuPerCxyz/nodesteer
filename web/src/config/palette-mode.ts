/**
 * Single rollback switch. Set to 'legacy' to restore the pre-token palette.
 * No settings-page UI: change this one constant and rebuild.
 */
export const PALETTE_MODE: 'new' | 'legacy' = 'new'

export function applyPaletteMode(
  root: HTMLElement = document.documentElement,
  mode: 'new' | 'legacy' = PALETTE_MODE
) {
  root.dataset.palette = mode
}
