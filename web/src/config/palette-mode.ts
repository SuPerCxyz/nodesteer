/**
 * Palette rollback switch. Default is the Spectrum Light (E) palette.
 * Set to 'new' for the previous brand-blue snapshot, or 'legacy' for the
 * older snapshot. No settings-page UI: change this one constant and rebuild.
 */
export type PaletteMode = 'spectrum' | 'new' | 'legacy'

export const PALETTE_MODE: PaletteMode = 'spectrum'

export function applyPaletteMode(
  root: HTMLElement = document.documentElement,
  mode: PaletteMode = PALETTE_MODE
) {
  root.dataset.palette = mode
}
