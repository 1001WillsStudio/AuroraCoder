/**
 * Keyboard helpers for the Workspace file tree.
 *
 * Rows are real <button>s (Tab + Enter/Space). These cover the remaining
 * shortcuts: opening the Download / Export / Delete menu without a pointer.
 */

/** True for the standard keyboard shortcuts that open a context menu. */
export function isTreeContextMenuKey(event) {
  if (!event) return false
  return event.key === 'ContextMenu' || (event.key === 'F10' && Boolean(event.shiftKey))
}

/** Anchor a keyboard-opened menu to the focused control's box. */
export function menuCoordsFromRect(rect) {
  if (!rect) return { x: 0, y: 0 }
  return { x: rect.left, y: rect.bottom }
}
