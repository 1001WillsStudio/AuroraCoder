/**
 * Conversation delete helpers for the mobile SPA.
 *
 * Same subtree semantics as frontend/src/utils/conversationDelete.js and
 * the gateway store: deleting a parent removes every descendant, not only
 * direct children. Loaded as a classic script (assigns globalThis).
 */
(function (root) {
  function collectDeletionIds(conversations, targetId) {
    if (!targetId) return []
    const byParent = new Map()
    for (const conv of conversations || []) {
      const parent = conv && conv.parent_id
      const id = conv && conv.id
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

  function childCountForDelete(conversations, targetId) {
    return Math.max(0, collectDeletionIds(conversations, targetId).length - 1)
  }

  function deletedIdsFromResponse(result, fallbackId) {
    if (Array.isArray(result && result.deleted_ids) && result.deleted_ids.length) {
      return result.deleted_ids.filter(Boolean)
    }
    if (Array.isArray(result && result.deleted) && result.deleted.length) {
      return result.deleted.filter(Boolean)
    }
    if (result && typeof result.deleted === 'string' && result.deleted) {
      return [result.deleted]
    }
    return fallbackId ? [fallbackId] : []
  }

  /**
   * Prefer the gateway's deleted_ids (full subtree). If the body only
   * names the requested id, walk the local list so nested grandchildren
   * are not left on screen.
   */
  function resolveDeletedIds(result, targetId, conversations) {
    if (Array.isArray(result && result.deleted_ids) && result.deleted_ids.length) {
      return result.deleted_ids.filter(Boolean)
    }
    const local = collectDeletionIds(conversations, targetId)
    if (local.length) return local
    return deletedIdsFromResponse(result, targetId)
  }

  function nextOpenConversationId(openId, parentId, deletedIds) {
    const gone = new Set(deletedIds || [])
    if (!openId || !gone.has(openId)) return openId || null
    if (parentId && !gone.has(parentId)) return parentId
    return null
  }

  function afterDeleteOpenId(openId, conversations, deletedIds) {
    const list = Array.isArray(conversations) ? conversations : []
    const open = list.find(function (c) { return c && c.id === openId })
    const parentId = open && open.parent_id ? open.parent_id : null
    return nextOpenConversationId(openId, parentId, deletedIds)
  }

  /**
   * Confirm copy + post-DELETE navigation for one user action.
   * ``action`` is stay | parent | new.
   */
  function planAfterDelete(openId, conversations, result, targetId) {
    const deletedIds = resolveDeletedIds(result, targetId, conversations)
    const next = afterDeleteOpenId(openId, conversations, deletedIds)
    var action = 'stay'
    if (next !== openId) action = next ? 'parent' : 'new'
    return {
      descendantCount: childCountForDelete(conversations, targetId),
      deletedIds: deletedIds,
      next: next,
      action: action,
    }
  }

  function deleteConfirmMessage(title, descendantCount) {
    const label = title || 'Untitled'
    const n = Number(descendantCount) || 0
    if (n > 0) {
      return 'Delete “' + label + '” and ' + n + ' subagent chat(s)? This cannot be undone.'
    }
    return 'Delete “' + label + '”? This cannot be undone.'
  }

  root.ConversationDelete = {
    collectDeletionIds: collectDeletionIds,
    childCountForDelete: childCountForDelete,
    deletedIdsFromResponse: deletedIdsFromResponse,
    resolveDeletedIds: resolveDeletedIds,
    nextOpenConversationId: nextOpenConversationId,
    afterDeleteOpenId: afterDeleteOpenId,
    planAfterDelete: planAfterDelete,
    deleteConfirmMessage: deleteConfirmMessage,
  }
})(typeof globalThis !== 'undefined' ? globalThis : this)
