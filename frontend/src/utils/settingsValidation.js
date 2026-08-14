/**
 * Settings → Save rules for built-in and custom LLM providers.
 *
 * A built-in card is a pre-configured provider (name/URL locked). A custom
 * card is the same shape with those fields editable. Both use the same
 * usable-key and encode helpers: the server never sends the real secret
 * back, so an empty box plus keyConfigured means "keep the stored key."
 *
 * Pre-configured cards do not require a key — unused built-ins must not
 * block Save. A user-added custom card still does.
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

/** Custom (user-added) cards must have a key; pre-configured ones may not. */
export function providerKeyIsRequired(provider) {
  return !provider?.preconfigured
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
 * @param {{nameRequired: string, baseUrlRequired: string, apiKeyRequired: string}} messages
 * @returns {Record<string, string>}
 */
export function validateProviders(providers, messages) {
  const errors = {}
  ;(providers || []).forEach((p) => {
    const k = p.errorKey
    if (!p.preconfigured) {
      if (!String(p?.name || '').trim()) errors[k] = messages.nameRequired
      if (!String(p?.base_url || '').trim()) errors[k] = errors[k] || messages.baseUrlRequired
    }
    if (providerKeyIsRequired(p) && !providerHasUsableKey(p.api_key, p.keyConfigured)) {
      errors[k] = errors[k] || messages.apiKeyRequired
    }
  })
  return errors
}
