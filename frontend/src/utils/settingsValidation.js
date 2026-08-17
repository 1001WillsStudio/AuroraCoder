/**
 * Settings → Save rules for built-in and custom LLM providers.
 *
 * A built-in card is a pre-configured provider (name/URL locked). A custom
 * card is the same shape with those fields editable. Both use the same
 * usable-key and encode helpers: the server never sends the real secret
 * back, so an empty box plus keyConfigured means "keep the stored key."
 *
 * A missing API key never blocks Save. Without a key the provider cannot
 * discover models and stays unused in the main window; the user can add
 * a key later. Custom cards still need a name and base URL.
 */

/** True when the user typed a key, or a key is already stored server-side. */
export function providerHasUsableKey(apiKey, keyConfigured) {
  if (apiKey === true) return true
  if (typeof apiKey === 'string' && apiKey.trim()) return true
  return Boolean(keyConfigured)
}

/**
 * PUT /api/settings encoding: typed text is sent as-is; an empty field
 * with a stored key becomes `true` so the server keeps the secret.
 * @returns {string|true|undefined}
 */
export function encodeStoredApiKey(apiKey, keyConfigured) {
  if (typeof apiKey === 'string' && apiKey.trim()) return apiKey.trim()
  if (apiKey === true || Boolean(keyConfigured)) return true
  return undefined
}

function customCardStarted(p) {
  return Boolean(String(p?.name || '').trim() || String(p?.base_url || '').trim())
}

/**
 * @param {Array<{
 *   errorKey: string,
 *   name?: string,
 *   base_url?: string,
 *   api_key?: string|boolean,
 *   keyConfigured?: boolean,
 *   preconfigured?: boolean
 * }>} providers
 * @param {{nameRequired: string, baseUrlRequired: string}} messages
 * @returns {Record<string, string>} blocking field errors
 */
export function validateProviders(providers, messages) {
  const errors = {}
  ;(providers || []).forEach((p) => {
    if (p.preconfigured) return
    const k = p.errorKey
    if (!String(p?.name || '').trim()) errors[k] = messages.nameRequired
    if (!String(p?.base_url || '').trim()) errors[k] = errors[k] || messages.baseUrlRequired
  })
  return errors
}

/**
 * Non-blocking: a started custom card with no usable key.
 * @returns {Record<string, string>}
 */
export function collectProviderKeyWarnings(providers, message) {
  const warnings = {}
  ;(providers || []).forEach((p) => {
    if (p.preconfigured) return
    if (!customCardStarted(p)) return
    if (!providerHasUsableKey(p.api_key, p.keyConfigured)) warnings[p.errorKey] = message
  })
  return warnings
}
