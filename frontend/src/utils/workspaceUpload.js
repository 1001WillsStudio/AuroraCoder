/**
 * Which selected files go into the Upload Project zip.
 *
 * Every file the user chose is packed. A folder .gitignore is not applied —
 * that used to drop paths like secret.txt with no toast, and the Workspace
 * tree hides .gitignore itself (dotfile), so the skip was invisible.
 */

/** Path inside the selected folder (strips the folder name prefix). */
export function projectRelativePath(file) {
  const rel = file.webkitRelativePath || file.name || ''
  return rel.split('/').slice(1).join('/')
}

/**
 * @param {Iterable<{webkitRelativePath?: string, name?: string}>} files
 * @returns {{ inProject: string, file: object }[]}
 */
export function workspaceUploadEntries(files) {
  const entries = []
  for (const file of files) {
    const inProject = projectRelativePath(file)
    if (!inProject) continue
    entries.push({ inProject, file })
  }
  return entries
}
