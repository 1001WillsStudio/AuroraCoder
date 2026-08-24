/**
 * Helpers for deleting a conversation from history.
 *
 * Deleting a parent chat also removes its subagent children. If the open
 * chat is in that set, the UI either returns to the surviving parent or
 * starts a new chat.
 */

/**
 * Target id plus every descendant (subagent) recorded under it.
 * Unknown ids still return ``[targetId]`` so a stale row can be deleted.
 */
export function collectDeletionIds(conversations, targetId) {
  if (!targetId) return []
  const byParent = new Map()
  for (const conv of conversations || []) {
    const parent = conv?.parent_id
    const id = conv?.id
    if (!parent || !id) continue
    const list = byParent.get(parent) || []
    list.push(id)
    byParent.set(parent, list)
  }
  const out = []
  const stack = [targetId]
  const seen = new Set()
  while (stack.length) {
    const id = stack.pop()
    if (!id || seen.has(id)) continue
    seen.add(id)
    out.push(id)
    const children = byParent.get(id) || []
    for (let i = children.length - 1; i >= 0; i -= 1) stack.push(children[i])
  }
  return out
}

/** Number of subagent chats that would be removed with *targetId*. */
export function childCountForDelete(conversations, targetId) {
  return Math.max(0, collectDeletionIds(conversations, targetId).length - 1)
}

/**
 * Normalize the DELETE response. The gateway returns
 * ``{ deleted, deleted_ids }``; tolerate a bare id or a missing body.
 */
export function deletedIdsFromResponse(result, fallbackId) {
  if (Array.isArray(result?.deleted_ids) && result.deleted_ids.length) {
    return result.deleted_ids.filter(Boolean)
  }
  if (Array.isArray(result?.deleted) && result.deleted.length) {
    return result.deleted.filter(Boolean)
  }
  if (typeof result?.deleted === 'string' && result.deleted) {
    return [result.deleted]
  }
  return fallbackId ? [fallbackId] : []
}

export function openChatWasDeleted(openId, deletedIds) {
  return Boolean(openId && (deletedIds || []).includes(openId))
}

/**
 * Where to go after a delete.
 * - Open chat not deleted → stay.
 * - Open chat deleted, parent survived → parent (subagent case).
 * - Otherwise → ``null`` (new chat / welcome).
 */
export function nextOpenConversationId(openId, parentId, deletedIds) {
  const gone = new Set(deletedIds || [])
  if (!openId || !gone.has(openId)) return openId || null
  if (parentId && !gone.has(parentId)) return parentId
  return null
}
