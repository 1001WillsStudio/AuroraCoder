/**
 * Conversation IDs that work outside a secure context.
 *
 * `crypto.randomUUID()` is secure-context-only (HTTPS or localhost). This
 * app is often served over http:// on a non-localhost hostname, where
 * calling it throws TypeError and the fork button appears to do nothing.
 *
 * `crypto.getRandomValues()` is available in those same insecure contexts;
 * Math.random is the last-resort fallback if even that is missing.
 *
 * @param {Crypto|null|undefined} [webCrypto] Injectable Web Crypto object
 *   (defaults to `globalThis.crypto`). Tests pass a stub that omits
 *   `randomUUID` to simulate an insecure HTTP origin.
 * @returns {string} RFC 4122 version-4 UUID
 */
export function newConversationId(webCrypto) {
  const c = webCrypto !== undefined ? webCrypto : globalThis.crypto
  if (c && typeof c.randomUUID === 'function') {
    return c.randomUUID()
  }
  const bytes = new Uint8Array(16)
  if (c && typeof c.getRandomValues === 'function') {
    c.getRandomValues(bytes)
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
