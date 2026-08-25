import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  Folder, FolderOpen, File, ChevronRight, ChevronDown,
  RefreshCw, FileCode, FileText, Image, Database, Settings,
  FileJson, Braces, Download, Trash2, FolderArchive, AtSign
} from 'lucide-react'
import useLanguage from '../hooks/useLanguage'
import {
  deleteWorkspacePath,
  downloadWorkspaceFile,
  exportWorkspaceFolder,
  getFileTree,
} from '../services/api'
import { expandedEmptyFolderPaths, setFolderChildren } from '../utils/fileTree'

// ── File-type icon ──────────────────────────────────────────────────────────
const getFileIcon = (extension) => {
  const iconProps = { size: 14 }
  switch (extension) {
    case '.js':
    case '.jsx':
    case '.ts':
    case '.tsx':
      return <FileCode {...iconProps} style={{ color: '#f7df1e' }} />
    case '.py':
      return <FileCode {...iconProps} style={{ color: '#3776ab' }} />
    case '.json':
      return <FileJson {...iconProps} style={{ color: '#f59e0b' }} />
    case '.md':
    case '.txt':
      return <FileText {...iconProps} style={{ color: '#6b7280' }} />
    case '.css':
    case '.scss':
    case '.sass':
      return <Braces {...iconProps} style={{ color: '#38bdf8' }} />
    case '.html':
      return <FileCode {...iconProps} style={{ color: '#e34c26' }} />
    case '.png':
    case '.jpg':
    case '.jpeg':
    case '.gif':
    case '.svg':
    case '.webp':
      return <Image {...iconProps} style={{ color: '#a855f7' }} />
    case '.sql':
    case '.db':
    case '.sqlite':
      return <Database {...iconProps} style={{ color: '#06b6d4' }} />
    case '.yml':
    case '.yaml':
    case '.toml':
    case '.ini':
    case '.env':
      return <Settings {...iconProps} style={{ color: '#94a3b8' }} />
    default:
      return <File {...iconProps} />
  }
}

// ── Single tree row ─────────────────────────────────────────────────────────
// Deliberately NOT memoised on expansion: the whole *visible* subtree must
// re-render whenever the expanded set changes so nested folders open instantly.
// Collapsed folders render no children, so the visible node count stays small.
function TreeNode({ node, level, onFileClick, expandedFolders, toggleFolder, onContextMenu }) {
  const isFolder = node.type === 'folder'
  const isExpanded = isFolder && expandedFolders.has(node.path)
  const hasChildren = isFolder

  const handleClick = () => {
    if (isFolder) toggleFolder(node)
    else onFileClick?.(node.path)
  }

  return (
    <div className="tree-node">
      <div
        className={`tree-item ${isFolder ? 'folder' : 'file'}`}
        style={{ paddingLeft: `${level * 16 + 8}px` }}
        onClick={handleClick}
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onContextMenu?.(e, node) }}
      >
        {isFolder ? (
          <>
            <span className="tree-chevron">
              {hasChildren
                ? (isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />)
                : <span style={{ width: 14 }} />}
            </span>
            <span className="tree-icon folder-icon">
              {isExpanded ? <FolderOpen size={14} /> : <Folder size={14} />}
            </span>
          </>
        ) : (
          <>
            <span className="tree-chevron" style={{ width: 14 }} />
            <span className="tree-icon file-icon">{getFileIcon(node.extension)}</span>
          </>
        )}
        <span className="tree-name">{node.name}</span>
      </div>

      {isExpanded && (
        <div className="tree-children">
          {(node.children || []).map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              level={level + 1}
              onFileClick={onFileClick}
              expandedFolders={expandedFolders}
              toggleFolder={toggleFolder}
              onContextMenu={onContextMenu}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ── Right-click context menu ────────────────────────────────────────────────
function ContextMenu({ x, y, node, onClose, onDelete, onDownload, onExport, onAddToChat, t }) {
  const menuRef = useRef(null)
  const isFolder = node.type === 'folder'

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) onClose()
    }
    const handleEscape = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [onClose])

  useEffect(() => {
    if (!menuRef.current) return
    const rect = menuRef.current.getBoundingClientRect()
    if (rect.right > window.innerWidth) menuRef.current.style.left = `${x - rect.width}px`
    if (rect.bottom > window.innerHeight) menuRef.current.style.top = `${y - rect.height}px`
  }, [x, y])

  return (
    <div ref={menuRef} className="tree-context-menu" style={{ left: x, top: y }}>
      {!isFolder && onAddToChat && (
        <button className="context-menu-item" onClick={() => { onAddToChat(node); onClose() }} data-testid="file-tree-add-to-chat">
          <AtSign size={14} />
          <span>{t('fileTree.addToChat')}</span>
        </button>
      )}
      {isFolder ? (
        <button className="context-menu-item" onClick={() => { onExport(node); onClose() }}>
          <FolderArchive size={14} />
          <span>{t('fileTree.export')}</span>
        </button>
      ) : (
        <button className="context-menu-item" onClick={() => { onDownload(node); onClose() }}>
          <Download size={14} />
          <span>{t('fileTree.download')}</span>
        </button>
      )}
      <button className="context-menu-item danger" onClick={() => { onDelete(node); onClose() }}>
        <Trash2 size={14} />
        <span>{t('fileTree.delete')}</span>
      </button>
    </div>
  )
}

// ── Main component ──────────────────────────────────────────────────────────
// First listing is the unchanged depth-5 snapshot. Clicking a folder that
// has no children listed fetches one level of that folder. Closing it
// drops those children. Refresh / end-of-stream refetch the snapshot, then
// re-open any folder that is still expanded (one level at a time).
const FileTree = ({ onFileClick, isStreaming, refreshTrigger = 0, onPathDeleted, onAddToChat }) => {
  const { t } = useLanguage()
  const [tree, setTree] = useState([])
  const [, setRootPath] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expandedFolders, setExpandedFolders] = useState(() => new Set())
  const [contextMenu, setContextMenu] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null)

  // Last structure we rendered — skip setState when a refetch is identical so
  // an in-progress expansion never flickers or collapses.
  const lastTreeJsonRef = useRef('')
  const expandedRef = useRef(expandedFolders)
  const onDemandRef = useRef(new Set())
  expandedRef.current = expandedFolders

  const loadOneLevel = useCallback(async (path) => {
    const data = await getFileTree({ path, maxDepth: 1 })
    if (data.error) return null
    return data.tree || []
  }, [])

  const fetchOneLevel = useCallback(async (path) => {
    try {
      const children = await loadOneLevel(path)
      if (children == null) return
      if (!expandedRef.current.has(path)) return
      onDemandRef.current.add(path)
      setTree((prev) => {
        const next = setFolderChildren(prev, path, children)
        lastTreeJsonRef.current = JSON.stringify(next)
        return next
      })
    } catch (err) {
      console.error('File tree one-level error:', err)
    }
  }, [loadOneLevel])

  const restoreExpanded = useCallback(async (snapshot) => {
    let tree = snapshot
    const loaded = new Set()
    for (;;) {
      const pending = expandedEmptyFolderPaths(tree, expandedRef.current)
        .filter((path) => !loaded.has(path))
      if (!pending.length) break
      const path = pending[0]
      loaded.add(path)
      let children
      try {
        children = await loadOneLevel(path)
      } catch (err) {
        console.error('File tree restore error:', err)
        break
      }
      if (children == null || !expandedRef.current.has(path)) continue
      tree = setFolderChildren(tree, path, children)
      onDemandRef.current.add(path)
    }
    return tree
  }, [loadOneLevel])

  const fetchTree = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getFileTree({ maxDepth: 5 })
      if (data.error) {
        setError(data.error)
        setTree([])
        lastTreeJsonRef.current = ''
      } else {
        onDemandRef.current = new Set()
        const restored = await restoreExpanded(data.tree || [])
        lastTreeJsonRef.current = JSON.stringify(restored)
        setTree(restored)
        setRootPath(data.root)
      }
    } catch (err) {
      setError('Failed to load file tree')
      console.error('File tree error:', err)
    } finally {
      setLoading(false)
    }
  }, [restoreExpanded])

  // Initial load.
  useEffect(() => { fetchTree() }, [fetchTree])

  // Auto-retry on failure — handles the backend startup race so the user
  // doesn't have to click the manual "Retry" button.
  const treeRetryCountRef = useRef(0)
  useEffect(() => {
    if (!error) { treeRetryCountRef.current = 0; return }
    if (treeRetryCountRef.current >= 10) return  // max 10 retries (~30 s)
    treeRetryCountRef.current++
    const timer = setTimeout(() => fetchTree(), 3000)
    return () => clearTimeout(timer)
  }, [error, fetchTree])

  // Re-fetch on detected file-system changes: agent write/delete/terminal,
  // and project upload (both bump refreshTrigger).
  useEffect(() => {
    if (refreshTrigger > 0) fetchTree()
  }, [refreshTrigger, fetchTree])

  // Re-fetch once when streaming ends (catches terminal-created files).
  const wasStreamingRef = useRef(false)
  useEffect(() => {
    if (isStreaming) {
      wasStreamingRef.current = true
    } else if (wasStreamingRef.current) {
      wasStreamingRef.current = false
      fetchTree()
    }
  }, [isStreaming, fetchTree])

  const toggleFolder = useCallback((node) => {
    const path = node.path
    const closing = expandedRef.current.has(path)
    setExpandedFolders((prev) => {
      const next = new Set(prev)
      if (closing) next.delete(path)
      else next.add(path)
      return next
    })
    if (closing && onDemandRef.current.has(path)) {
      for (const p of [...onDemandRef.current]) {
        if (p === path || p.startsWith(`${path}/`)) onDemandRef.current.delete(p)
      }
      setTree((prev) => {
        const next = setFolderChildren(prev, path, [])
        lastTreeJsonRef.current = JSON.stringify(next)
        return next
      })
    } else if (!closing && !(node.children && node.children.length)) {
      fetchOneLevel(path)
    }
  }, [fetchOneLevel])

  const handleContextMenu = useCallback((e, node) => {
    setContextMenu({ x: e.clientX, y: e.clientY, node })
  }, [])

  const handleDownload = useCallback(async (node) => {
    try {
      await downloadWorkspaceFile(node.path, node.name)
    } catch (err) {
      console.error('Download failed:', err)
      alert(err.message || 'Download failed')
    }
  }, [])

  const handleExport = useCallback(async (node) => {
    try {
      await exportWorkspaceFolder(node.path, `${node.name}.zip`)
    } catch (err) {
      console.error('Export failed:', err)
      alert(err.message || 'Export failed')
    }
  }, [])

  const handleDeleteRequest = useCallback((node) => setConfirmDelete(node), [])

  const handleDeleteConfirm = useCallback(async () => {
    if (!confirmDelete) return
    try {
      await deleteWorkspacePath(confirmDelete.path)
      onPathDeleted?.(confirmDelete.path)
    } catch (err) {
      console.error('Delete failed:', err)
      alert(err.message || 'Failed to delete')
    }
    try {
      await fetchTree()
    } finally {
      setConfirmDelete(null)
    }
  }, [confirmDelete, fetchTree, onPathDeleted])

  return (
    <div className="file-tree" data-testid="file-tree">
      <div className="file-tree-header">
        <span className="file-tree-title">{t('fileTree.workspace')}</span>
        <button
          className="file-tree-refresh"
          onClick={fetchTree}
          disabled={loading}
          title={t('fileTree.refresh')}
        >
          <RefreshCw size={14} className={loading ? 'spin' : ''} />
        </button>
      </div>

      <div className="file-tree-content">
        {loading && tree.length === 0 ? (
          <div className="file-tree-loading">
            <RefreshCw size={16} className="spin" />
            <span>{t('fileTree.loading')}</span>
          </div>
        ) : error ? (
          <div className="file-tree-empty">
            <p>{error}</p>
            <button onClick={fetchTree}>{t('fileTree.retry')}</button>
          </div>
        ) : tree.length === 0 ? (
          <div className="file-tree-empty">
            <Folder size={24} />
            <p>{t('fileTree.empty')}</p>
            <span>{t('fileTree.emptyHint')}</span>
          </div>
        ) : (
          <div className="file-tree-nodes">
            {tree.map((node) => (
              <TreeNode
                key={node.path}
                node={node}
                level={0}
                onFileClick={onFileClick}
                expandedFolders={expandedFolders}
                toggleFolder={toggleFolder}
                onContextMenu={handleContextMenu}
              />
            ))}
          </div>
        )}
      </div>

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          node={contextMenu.node}
          onClose={() => setContextMenu(null)}
          onDelete={handleDeleteRequest}
          onDownload={handleDownload}
          onExport={handleExport}
          onAddToChat={onAddToChat}
          t={t}
        />
      )}

      {confirmDelete && (
        <div className="tree-confirm-overlay" onClick={() => setConfirmDelete(null)}>
          <div className="tree-confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <p>{t('fileTree.deleteConfirm', { name: confirmDelete.name, folderSuffix: confirmDelete.type === 'folder' ? t('fileTree.deleteConfirmFolder') : '' })}</p>
            <div className="tree-confirm-actions">
              <button className="tree-confirm-cancel" onClick={() => setConfirmDelete(null)}>{t('fileTree.cancel')}</button>
              <button className="tree-confirm-delete" onClick={handleDeleteConfirm}>{t('fileTree.delete')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default FileTree
