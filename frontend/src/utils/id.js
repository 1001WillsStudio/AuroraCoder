/**
 * Conversation IDs that work on plain HTTP as well as HTTPS.
 *
 * crypto.randomUUID() is secure-context-only. AuroraCoder is often served
 * over http:// (Docker port-map, LAN IP), where window.isSecureContext is
 * false and randomUUID is undefined. Calling it throws TypeError and aborts
 * fork / new-chat flows before any state updates.
 *
 * crypto.getRandomValues() remains available in insecure contexts; we fall
 * back to it (then Math.random) and stamp RFC 4122 version-4 bits.
 *
 * @param {Crypto|undefined} webCrypto - injectable for tests; defaults to
 *   globalThis.crypto
 * @returns {string} RFC 4122 version-4 UUID
 */
export function newConversationId(webCrypto = globalThis.crypto) {
  if (webCrypto && typeof webCrypto.randomUUID === 'function') {
    return webCrypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  if (webCrypto && typeof webCrypto.getRandomValues === 'function') {
    webCrypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return (
    hex.slice(0, 8) + '-' +
    hex.slice(8, 12) + '-' +
    hex.slice(12, 16) + '-' +
    hex.slice(16, 20) + '-' +
    hex.slice(20)
  )
}
