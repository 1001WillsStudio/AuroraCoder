/**
 * Keyboard focus helpers for modal dialogs.
 *
 * Settings used to open as a plain overlay: the gear kept document focus,
 * so Tab walked the sidebar (+ New Chat, Upload Project) behind the dim.
 * These helpers move focus into a container, wrap Tab at its edges, and
 * restore the previously focused control on close.
 */

/** Elements a Tab press can land on (disabled / tabindex=-1 excluded). */
export const FOCUSABLE_SELECTOR = [
  'a[href]:not([tabindex="-1"])',
  'button:not([disabled]):not([tabindex="-1"])',
  'textarea:not([disabled]):not([tabindex="-1"])',
  'input:not([disabled]):not([tabindex="-1"])',
  'select:not([disabled]):not([tabindex="-1"])',
  '[tabindex]:not([tabindex="-1"]):not([disabled])',
].join(',')

function isTabbable(el) {
  if (!el || el.disabled || el.hidden) return false
  if (typeof el.getAttribute === 'function' && el.getAttribute('aria-hidden') === 'true') {
    return false
  }
  return true
}

/**
 * @param {{querySelectorAll?: Function}|null|undefined} root
 * @returns {Element[]}
 */
export function getFocusableElements(root) {
  if (!root || typeof root.querySelectorAll !== 'function') return []
  return Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR)).filter(isTabbable)
}

/**
 * First tabbable descendant, or the container itself (for tabIndex=-1).
 * @param {{querySelectorAll?: Function, focus?: Function}|null|undefined} root
 */
export function initialFocusTarget(root) {
  const list = getFocusableElements(root)
  return list[0] || root || null
}

/**
 * Where Tab / Shift+Tab should go when it would leave the dialog.
 * Returns null when the browser should handle the press (still inside).
 * @param {unknown[]} focusables
 * @param {unknown} current
 * @param {boolean} shiftKey
 */
export function wrapTabTarget(focusables, current, shiftKey) {
  if (!focusables.length) return null
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  const inside = focusables.includes(current)
  if (!inside) return shiftKey ? last : first
  if (shiftKey && current === first) return last
  if (!shiftKey && current === last) return first
  return null
}

/**
 * If Tab would leave ``root``, preventDefault and move focus.
 * @param {{key?: string, shiftKey?: boolean, target?: unknown, preventDefault?: Function}|null|undefined} event
 * @param {{querySelectorAll?: Function, focus?: Function}|null|undefined} root
 * @returns {boolean} true when the event was trapped
 */
export function trapTabKey(event, root) {
  if (!event || event.key !== 'Tab' || !root) return false
  const focusables = getFocusableElements(root)
  if (!focusables.length) {
    event.preventDefault?.()
    moveFocus(root)
    return true
  }
  const next = wrapTabTarget(focusables, event.target, !!event.shiftKey)
  if (!next) return false
  event.preventDefault?.()
  moveFocus(next)
  return true
}

/**
 * @param {{focus?: Function}|null|undefined} el
 * @returns {boolean}
 */
export function moveFocus(el) {
  if (el && typeof el.focus === 'function') {
    el.focus()
    return true
  }
  return false
}
