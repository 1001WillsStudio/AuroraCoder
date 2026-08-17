/**
 * Encode an API key field for PUT /api/settings.
 *
 * The server never sends the real secret back — only "a key exists" —
 * so the input is empty on purpose. An empty box plus keyConfigured
 * becomes `true` so the server keeps the stored key. Typed text is
 * sent as-is. Completeness is not checked here: a provider without a
 * key cannot discover models and stays unused in the main window.
 *
 * @returns {string|true|undefined}
 */
export function encodeStoredApiKey(apiKey, keyConfigured) {
  if (typeof apiKey === 'string' && apiKey.trim()) return apiKey.trim()
  if (apiKey === true || Boolean(keyConfigured)) return true
  return undefined
}

/** Matches the Max Iterations Per Turn spinbutton (min=5, max=200). */
export const MAX_ITERATIONS_MIN = 5
export const MAX_ITERATIONS_MAX = 200

/**
 * HTML min/max on a number input are not checked when Save is a <button>
 * onclick (no form submit). Reject out-of-range values before PUT.
 *
 * Empty / omitted is valid — the server default (30) is used.
 *
 * @returns {string|null} translation key, or null if the value is allowed
 */
export function validateMaxIterations(value) {
  if (value === '' || value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  const n = typeof value === 'number' ? value : Number(String(value).trim())
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < MAX_ITERATIONS_MIN || n > MAX_ITERATIONS_MAX) {
    return 'msg.maxIterationsRange'
  }
  return null
}
