/**
 * Composer helpers for attaching workspace files to a chat turn.
 *
 * Typing `@` in the input opens a file picker. Chosen paths become chips
 * (they are not inserted as raw text). The gateway validates the list and
 * prefixes the model-facing message.
 */

export const MAX_ATTACHED_FILES = 8

export function fileNameOf(path) {
  if (!path) return ''
  const parts = String(path).replace(/\\/g, '/').split('/')
  return parts[parts.length - 1] || path
}

export function addAttachedFile(files, path) {
  const next = Array.isArray(files) ? files : []
  const item = typeof path === 'string' ? path.trim().replace(/\\/g, '/') : ''
  if (!item) return next
  if (next.includes(item)) return next
  if (next.length >= MAX_ATTACHED_FILES) return next
  return [...next, item]
}

export function removeAttachedFile(files, path) {
  const next = Array.isArray(files) ? files : []
  return next.filter((item) => item !== path)
}

/**
 * If the caret sits in an `@query` token, return it so the picker can open.
 * Requires start-of-text or whitespace before `@` so emails stay untouched.
 */
export function parseAtQuery(text, cursor) {
  if (typeof text !== 'string' || typeof cursor !== 'number') return null
  if (cursor < 0 || cursor > text.length) return null
  const before = text.slice(0, cursor)
  const match = before.match(/(^|[\s])@([^\s@]*)$/)
  if (!match) return null
  const query = match[2]
  const start = cursor - query.length - 1
  return { query, start, end: cursor }
}

/** Remove the `@query` token after a file is picked (the path lives on a chip). */
export function applyAtPick(text, range) {
  if (typeof text !== 'string' || !range) return text || ''
  const before = text.slice(0, range.start)
  const after = text.slice(range.end)
  if (before.endsWith(' ') && /^\s/.test(after)) {
    return `${before}${after.replace(/^\s+/, '')}`
  }
  return `${before}${after}`
}
