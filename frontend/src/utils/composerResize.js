/**
 * Grow the chat composer with the draft, up to the CSS max-height.
 *
 * The textarea is rows=1 (~60px). Without this, a multiline paste stays
 * locked at that height and the top visible line is sliced by the border.
 */

export const COMPOSER_MAX_HEIGHT_PX = 200

/**
 * Height the composer should take given measured scrollHeight and CSS max.
 * Grows with the draft; never exceeds maxHeight.
 */
export function composerHeight(scrollHeight, maxHeight = COMPOSER_MAX_HEIGHT_PX) {
  const scroll = Math.max(0, Number(scrollHeight) || 0)
  const max = Number(maxHeight)
  if (!Number.isFinite(max) || max <= 0) return scroll
  return Math.min(scroll, max)
}

/**
 * Apply that height to a textarea-like node (real DOM or a test double).
 * Resets height first so scrollHeight reflects the full draft.
 * @returns {number} the applied pixel height
 */
export function applyComposerResize(el, maxHeight) {
  if (!el || el.style == null) return 0
  let cap = maxHeight
  if (cap == null && typeof getComputedStyle === 'function') {
    const parsed = parseFloat(getComputedStyle(el).maxHeight)
    if (Number.isFinite(parsed)) cap = parsed
  }
  if (cap == null) cap = COMPOSER_MAX_HEIGHT_PX
  el.style.height = 'auto'
  const measured = el.scrollHeight
  const next = composerHeight(measured, cap)
  el.style.height = `${next}px`
  el.style.overflowY = measured > cap ? 'auto' : 'hidden'
  return next
}
