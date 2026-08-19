/**
 * Keep the open chat in the address bar (`/c/{id}`) so reload and
 * Back/Forward can restore it. History/location are injectable so Node
 * tests can run these helpers without a browser.
 */

const CONVERSATION_PATH_RE = /^\/c\/([^/]+)$/

export function parseConversationId(pathname) {
  if (!pathname || typeof pathname !== 'string') return null
  const path = pathname.split('?')[0]
  const match = path.match(CONVERSATION_PATH_RE)
  if (!match) return null
  try {
    return decodeURIComponent(match[1])
  } catch {
    return match[1]
  }
}

export function conversationPath(conversationId) {
  if (!conversationId) return '/'
  return `/c/${encodeURIComponent(conversationId)}`
}

/**
 * Align the address bar with `conversationId`.
 * @returns {'push'|'replace'|null} the history method used, or null if already matched
 */
export function syncConversationUrl(conversationId, options = {}) {
  const hist = options.history ?? (typeof window !== 'undefined' ? window.history : null)
  const loc = options.location ?? (typeof window !== 'undefined' ? window.location : null)
  const mode = options.mode || 'replace'
  if (!hist || !loc) return null
  const next = conversationPath(conversationId)
  const currentPath = loc.pathname || '/'
  if (currentPath === next) return null
  const state = { conversationId: conversationId || null }
  if (mode === 'push') {
    hist.pushState(state, '', next)
    return 'push'
  }
  hist.replaceState(state, '', next)
  return 'replace'
}
