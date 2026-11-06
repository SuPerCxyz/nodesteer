/**
 * Palette rollback switch. Default is the Azure Pop (A) palette.
 * Set to 'spectrum' for Spectrum Light (E), 'new' for the previous brand-blue
 * snapshot, or 'legacy' for the older snapshot.
 * No settings-page UI: change this one constant and rebuild.
 */
export type PaletteMode = 'azure' | 'spectrum' | 'new' | 'legacy'

export const PALETTE_MODE: PaletteMode = 'azure'

export function applyPaletteMode(
  root: HTMLElement = document.documentElement,
  mode: PaletteMode = PALETTE_MODE
) {
  root.dataset.palette = mode
}
