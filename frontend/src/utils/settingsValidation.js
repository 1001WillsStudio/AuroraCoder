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
/** Stored sentinel — not 0, which is the invalid value this control rejects. */
export const MAX_ITERATIONS_UNLIMITED = 'unlimited'

/** Matches the Max Tool Concurrency spinbutton (min=1, max=20). */
export const MAX_TOOL_CONCURRENCY_MIN = 1
export const MAX_TOOL_CONCURRENCY_MAX = 20
/** Matches the Terminal Max Output spinbutton (min=1000, max=100000). */
export const TERMINAL_MAX_OUTPUT_MIN = 1000
export const TERMINAL_MAX_OUTPUT_MAX = 100000
/** Matches the Web Secondary Max Tokens spinbutton (min=256, max=32768). */
export const WEB_SECONDARY_MAX_TOKENS_MIN = 256
export const WEB_SECONDARY_MAX_TOKENS_MAX = 32768

export function isUnlimitedMaxIterations(value) {
  return typeof value === 'string' && value.trim().toLowerCase() === MAX_ITERATIONS_UNLIMITED
}

/**
 * HTML min/max on a number input are not checked when Save is a <button>
 * onclick (no form submit). Reject out-of-range values before PUT.
 *
 * Empty / omitted is valid — the server default (30) is used.
 * The string ``unlimited`` is valid (Unlimited checkbox). 0 is not.
 *
 * @returns {string|null} translation key, or null if the value is allowed
 */
export function validateMaxIterations(value) {
  if (value === '' || value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  if (isUnlimitedMaxIterations(value)) return null
  const n = typeof value === 'number' ? value : Number(String(value).trim())
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < MAX_ITERATIONS_MIN || n > MAX_ITERATIONS_MAX) {
    return 'msg.maxIterationsRange'
  }
  return null
}

/**
 * HTML min/max on a number input are not checked when Save is a <button>
 * onclick (no form submit). Empty / omitted is valid — the server default
 * is used.
 *
 * @returns {string|null} translation key, or null if the value is allowed
 */
export function validateIntegerRange(value, min, max, errorKey) {
  if (value === '' || value === null || value === undefined) return null
  if (typeof value === 'string' && value.trim() === '') return null
  const n = typeof value === 'number' ? value : Number(String(value).trim())
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < min || n > max) {
    return errorKey
  }
  return null
}

export function validateMaxToolConcurrency(value) {
  return validateIntegerRange(
    value, MAX_TOOL_CONCURRENCY_MIN, MAX_TOOL_CONCURRENCY_MAX, 'msg.maxToolConcurrencyRange',
  )
}

export function validateTerminalMaxOutput(value) {
  return validateIntegerRange(
    value, TERMINAL_MAX_OUTPUT_MIN, TERMINAL_MAX_OUTPUT_MAX, 'msg.terminalMaxOutputRange',
  )
}

export function validateWebSecondaryMaxTokens(value) {
  return validateIntegerRange(
    value, WEB_SECONDARY_MAX_TOKENS_MIN, WEB_SECONDARY_MAX_TOKENS_MAX, 'msg.webSecondaryMaxTokensRange',
  )
}
