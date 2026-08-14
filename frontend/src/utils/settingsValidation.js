/**
 * Client-side settings validation (pure — no React, no I/O).
 *
 * GET /api/settings never returns real secrets. A configured custom-provider
 * key arrives as `api_key: true`; the panel blanks the input and sets
 * `_key_configured` so the user can override later. Save must treat that
 * stored key as present — otherwise an unchanged form is rejected with
 * "API key required" and PUT /api/settings is never sent.
 */

/** True when the user typed a key, or a key is already stored server-side. */
export function customProviderHasUsableKey(cp) {
  if (!cp) return false
  if (cp.api_key === true) return true
  if (typeof cp.api_key === 'string' && cp.api_key.trim()) return true
  return Boolean(cp._key_configured)
}

/**
 * @param {Array<{name?: string, base_url?: string, api_key?: string|boolean, _key_configured?: boolean}>} customProviders
 * @param {{nameRequired: string, baseUrlRequired: string, apiKeyRequired: string}} messages
 * @returns {Record<string, string>} field errors keyed `custom-${index}`
 */
export function validateCustomProviders(customProviders, messages) {
  const errors = {}
  ;(customProviders || []).forEach((cp, i) => {
    const b = `custom-${i}`
    if (!String(cp?.name || '').trim()) errors[b] = messages.nameRequired
    if (!String(cp?.base_url || '').trim()) errors[b] = errors[b] || messages.baseUrlRequired
    if (!customProviderHasUsableKey(cp)) errors[b] = errors[b] || messages.apiKeyRequired
  })
  return errors
}
