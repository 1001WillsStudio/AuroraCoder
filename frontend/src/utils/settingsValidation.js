/**
 * Settings → Save checks for custom LLM providers.
 *
 * What this solves: clicking Save with no edits used to fail with
 * "API key required" on a provider whose key was already stored. The
 * server never sends the real secret back (only "yes, a key exists"),
 * so the input is empty on purpose. This helper treats that stored key
 * as present so other settings can be saved without re-typing the key.
 *
 * A newly added provider with a blank key is still rejected.
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
