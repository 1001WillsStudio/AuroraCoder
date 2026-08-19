/**
 * Helpers for the Workspace tree. First paint stays at depth 5; extra
 * levels are loaded one folder at a time and dropped when that folder closes.
 */

export function findTreeNode(tree, path) {
  for (const node of tree || []) {
    if (node.path === path) return node
    const found = findTreeNode(node.children, path)
    if (found) return found
  }
  return null
}

export function setFolderChildren(tree, path, children) {
  return (tree || []).map((node) => {
    if (node.path === path) return { ...node, children: children || [] }
    if (node.children?.length) {
      return { ...node, children: setFolderChildren(node.children, path, children) }
    }
    return node
  })
}

/** Expanded folders that are in the tree but have no children, shallowest first. */
export function expandedEmptyFolderPaths(tree, expanded) {
  const paths = []
  for (const path of expanded || []) {
    const node = findTreeNode(tree, path)
    if (node && node.type === 'folder' && !(node.children && node.children.length)) {
      paths.push(path)
    }
  }
  paths.sort((a, b) => a.split('/').length - b.split('/').length)
  return paths
}
