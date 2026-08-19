/**
 * Pure helpers for the Workspace file tree.
 *
 * The first listing stays at the historic depth cap. Opening a folder that
 * still has unlisted contents merges that folder's subtree into the snapshot.
 */

export function findTreeNode(tree, path) {
  for (const node of tree || []) {
    if (node.path === path) return node
    const found = findTreeNode(node.children, path)
    if (found) return found
  }
  return null
}

export function mergeFolderChildren(tree, path, children, truncated) {
  return (tree || []).map((node) => {
    if (node.path === path) {
      const next = { ...node, children: children || [] }
      if (truncated) next.truncated = true
      else delete next.truncated
      return next
    }
    if (Array.isArray(node.children) && node.children.length > 0) {
      return {
        ...node,
        children: mergeFolderChildren(node.children, path, children, truncated),
      }
    }
    return node
  })
}
