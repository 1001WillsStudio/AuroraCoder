/**
 * Sidebar Model picker: Settings → Default Model wins over last-used.
 *
 * /api/providers returns `default` from other.agent.default_model. The
 * picker used to restore localStorage `selectedProvider` whenever that id
 * was still listed, so a saved Default Model never reached New Chat or a
 * fresh page load.
 */

/**
 * @param {{ id: string }[] | null | undefined} providers
 * @param {string | null | undefined} apiDefault  `data.default` from /api/providers
 * @param {string | null | undefined} lastUsed    localStorage last-used; ignored
 *   when `apiDefault` is still in the list
 * @returns {string | null}
 */
export function pickSidebarProvider(providers, apiDefault, lastUsed) {
  const list = Array.isArray(providers) ? providers : []
  const has = (id) => Boolean(id) && list.some((p) => p.id === id)
  if (has(apiDefault)) return apiDefault
  if (has(lastUsed)) return lastUsed
  if (apiDefault) return apiDefault
  if (lastUsed) return lastUsed
  return list[0]?.id ?? null
}
