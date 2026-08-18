/**
 * Pure helpers for the right-hand file viewer (code panel).
 *
 * Workspace delete and viewer Refresh must not leave a tab showing
 * contents that are no longer on disk.
 */

/** True if filePath is the deleted path or lives under a deleted folder. */
export function pathIsDeleted(filePath, deletedPath) {
  if (!filePath || !deletedPath) return false
  const file = String(filePath).replace(/\\/g, '/')
  const deleted = String(deletedPath).replace(/\\/g, '/')
  if (file === deleted) return true
  const prefix = deleted.endsWith('/') ? deleted : `${deleted}/`
  return file.startsWith(prefix)
}

/**
 * Drop viewer tabs for a workspace path that was just deleted.
 * @returns {{ files: object[], nextActiveId: string|null }}
 */
export function closePanelFilesForDeletedPath(files, activeFileId, deletedPath) {
  const remaining = (files || []).filter(f => !pathIsDeleted(f.path, deletedPath))
  const nextActiveId = remaining.some(f => f.id === activeFileId)
    ? activeFileId
    : (remaining[0]?.id ?? null)
  return { files: remaining, nextActiveId }
}

/**
 * Apply /api/files/read results to view-only tabs.
 * Missing (non-OK) view-only tabs are dropped; still-present files get
 * fresh lines. Diff-tracked tabs (not isViewOnly) are left untouched.
 *
 * @param {object[]} files
 * @param {{ id: string, missing?: boolean, content?: string }[]} results
 */
export function applyViewOnlyReadResults(files, results) {
  const byId = new Map((results || []).map(r => [r.id, r]))
  const next = []
  for (const f of files || []) {
    const r = byId.get(f.id)
    if (!r || !f.isViewOnly) {
      next.push(f)
      continue
    }
    if (r.missing) continue
    const content = r.content ?? ''
    next.push({
      ...f,
      lines: String(content).split('\n').map((line, idx) => ({
        lineNumber: idx + 1,
        content: line,
        type: null,
      })),
    })
  }
  return next
}
