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
