import React, { useState, useEffect, useCallback, useRef } from 'react'
import { 
  Folder, FolderOpen, File, ChevronRight, ChevronDown, 
  RefreshCw, FileCode, FileText, Image, Database, Settings,
  FileJson, FileType, Coffee, Braces, Download, Trash2, FolderArchive
} from 'lucide-react'
import useLanguage from '../hooks/useLanguage'

// Get icon based on file extension
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

// Single tree node component — memoised to avoid O(n) re-renders when
// only one folder's expansion state changes or the loading flag toggles.
const TreeNode = React.memo(
  ({ node, level = 0, onFileClick, expandedFolders, toggleFolder, onContextMenu }) => {
  const isFolder = node.type === 'folder'
  const isExpanded = expandedFolders.has(node.path)
  const hasChildren = isFolder && node.children && node.children.length > 0
  
  const handleClick = () => {
    if (isFolder) {
      toggleFolder(node.path)
    } else {
      onFileClick?.(node.path)
    }
  }

  const handleContextMenu = (e) => {
    e.preventDefault()
    e.stopPropagation()
    onContextMenu?.(e, node)
  }
  
  return (
    <div className="tree-node">
      <div 
        className={`tree-item ${isFolder ? 'folder' : 'file'}`}
        style={{ paddingLeft: `${level * 16 + 8}px` }}
        onClick={handleClick}
        onContextMenu={handleContextMenu}
      >
        {isFolder ? (
          <>
            <span className="tree-chevron">
              {hasChildren ? (
                isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />
              ) : (
                <span style={{ width: 14 }} />
              )}
            </span>
            <span className="tree-icon folder-icon">
              {isExpanded ? <FolderOpen size={14} /> : <Folder size={14} />}
            </span>
          </>
        ) : (
          <>
            <span className="tree-chevron" style={{ width: 14 }} />
            <span className="tree-icon file-icon">
              {getFileIcon(node.extension)}
            </span>
          </>
        )}
        <span className="tree-name">{node.name}</span>
      </div>
      
      {isFolder && isExpanded && hasChildren && (
        <div className="tree-children">
          {node.children.map((child, idx) => (
            <TreeNode
              key={child.path || idx}
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
  },
  (prevProps, nextProps) => {
    // Only re-render if something meaningful changed
    return (
      prevProps.node === nextProps.node &&
      prevProps.node?.path === nextProps.node?.path &&
      prevProps.level === nextProps.level &&
      prevProps.onFileClick === nextProps.onFileClick &&
      prevProps.expandedFolders?.has(prevProps.node?.path) ===
        nextProps.expandedFolders?.has(nextProps.node?.path) &&
      prevProps.onContextMenu === nextProps.onContextMenu
    )
  }
)

// Context menu component
const ContextMenu = ({ x, y, node, onClose, onDelete, onDownload, onExport, t }) => {
  const menuRef = useRef(null)
  const isFolder = node.type === 'folder'

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        onClose()
      }
    }
    const handleEscape = (e) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [onClose])

  useEffect(() => {
    if (menuRef.current) {
      const rect = menuRef.current.getBoundingClientRect()
      if (rect.right > window.innerWidth) {
        menuRef.current.style.left = `${x - rect.width}px`
      }
      if (rect.bottom > window.innerHeight) {
        menuRef.current.style.top = `${y - rect.height}px`
      }
    }
  }, [x, y])

  return (
    <div
      ref={menuRef}
      className="tree-context-menu"
      style={{ left: x, top: y }}
    >
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
      <button
        className="context-menu-item danger"
        onClick={() => { onDelete(node); onClose() }}
      >
        <Trash2 size={14} />
        <span>{t('fileTree.delete')}</span>
      </button>
    </div>
  )
}

// Main FileTree component
const FileTree = ({ onFileClick, isStreaming, refreshTrigger = 0 }) => {
  const { t } = useLanguage()
  const [tree, setTree] = useState([])
  const [rootPath, setRootPath] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expandedFolders, setExpandedFolders] = useState(new Set())
  const [contextMenu, setContextMenu] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null)
  
  const debounceTimerRef = useRef(null)
  const lastFetchTimeRef = useRef(0)

  const fetchTree = useCallback(async () => {
    setLoading(true)
    setError(null)

    try {
      const response = await fetch('/api/files/tree?max_depth=5')
      const data = await response.json()

      if (data.error) {
        setError(data.error)
        setTree([])
      } else {
        setTree(data.tree || [])
        setRootPath(data.root)
      }
    } catch (err) {
      setError('Failed to load file tree')
      console.error('File tree error:', err)
    } finally {
      setLoading(false)
    }
  }, [])

  // Debounced wrapper — collapses rapid consecutive triggers (e.g. multiple
  // write_file calls in one batch) into a single fetch, preventing a storm of
  // HTTP requests and re-renders during active streaming.
  const debouncedFetchTree = useCallback(() => {
    const now = Date.now()
    const MIN_INTERVAL = 500   // minimum ms between *actual* fetches
    const DEBOUNCE_DELAY = 300 // wait ms after last trigger before fetching

    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = null
    }

    const timeSinceLastFetch = now - lastFetchTimeRef.current
    const delay = timeSinceLastFetch < MIN_INTERVAL
      ? MIN_INTERVAL - timeSinceLastFetch + DEBOUNCE_DELAY
      : DEBOUNCE_DELAY

    debounceTimerRef.current = setTimeout(() => {
      lastFetchTimeRef.current = Date.now()
      fetchTree()
      debounceTimerRef.current = null
    }, delay)
  }, [fetchTree])

  // Initial load — always immediate (no debounce needed)
  useEffect(() => {
    fetchTree()
  }, [fetchTree])

  // Refresh when file system operations are detected (via refreshTrigger).
  // Uses the debounced variant so a burst of tool results only issues one fetch.
  useEffect(() => {
    if (refreshTrigger > 0) {
      debouncedFetchTree()
    }
  }, [refreshTrigger, debouncedFetchTree])

  // Also refresh once when streaming stops — catches files created by
  // terminal commands (pip install, git clone, …) that don't go through
  // write_file / edit_file / delete_file.  Also uses the debounced path so
  // it won't fire if a tool-result refresh already happened moments ago.
  const wasStreamingRef = useRef(false)
  useEffect(() => {
    if (isStreaming) {
      wasStreamingRef.current = true
    } else if (wasStreamingRef.current) {
      wasStreamingRef.current = false
      const timer = setTimeout(debouncedFetchTree, 500)
      return () => clearTimeout(timer)
    }
  }, [isStreaming, debouncedFetchTree])
  
  const toggleFolder = useCallback((path) => {
    setExpandedFolders(prev => {
      const next = new Set(prev)
      if (next.has(path)) {
        next.delete(path)
      } else {
        next.add(path)
      }
      return next
    })
  }, [])

  const handleContextMenu = useCallback((e, node) => {
    setContextMenu({ x: e.clientX, y: e.clientY, node })
  }, [])

  const handleDownload = useCallback((node) => {
    const a = document.createElement('a')
    a.href = `/api/files/download?file_path=${encodeURIComponent(node.path)}`
    a.download = node.name
    document.body.appendChild(a)
    a.click()
    a.remove()
  }, [])

  const handleExport = useCallback((node) => {
    const a = document.createElement('a')
    a.href = `/api/files/export?folder_path=${encodeURIComponent(node.path)}`
    a.download = `${node.name}.zip`
    document.body.appendChild(a)
    a.click()
    a.remove()
  }, [])

  const handleDeleteRequest = useCallback((node) => {
    setConfirmDelete(node)
  }, [])

  const handleDeleteConfirm = useCallback(async () => {
    if (!confirmDelete) return
    try {
      const res = await fetch('/api/files/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: confirmDelete.path })
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        alert(err.detail || 'Failed to delete')
      }
      fetchTree()
    } catch (err) {
      console.error('Delete failed:', err)
      alert('Delete failed: ' + err.message)
    } finally {
      setConfirmDelete(null)
    }
  }, [confirmDelete, fetchTree])
  
  return (
    <div className="file-tree">
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
            {tree.map((node, idx) => (
              <TreeNode
                key={node.path || idx}
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

      {/* Context menu */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          node={contextMenu.node}
          onClose={() => setContextMenu(null)}
          onDelete={handleDeleteRequest}
          onDownload={handleDownload}
          onExport={handleExport}
          t={t}
        />
      )}

      {/* Delete confirmation dialog */}
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
