import React, { useState, useEffect, useCallback, useRef } from 'react'
import { 
  Folder, FolderOpen, File, ChevronRight, ChevronDown, 
  RefreshCw, FileCode, FileText, Image, Database, Settings,
  FileJson, FileType, Coffee, Braces, Download, Trash2, FolderArchive
} from 'lucide-react'

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

// Single tree node component
const TreeNode = ({ node, level = 0, onFileClick, expandedFolders, toggleFolder, onContextMenu }) => {
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
}

// Context menu component
const ContextMenu = ({ x, y, node, onClose, onDelete, onDownload, onExport }) => {
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
          <span>Export as .zip</span>
        </button>
      ) : (
        <button className="context-menu-item" onClick={() => { onDownload(node); onClose() }}>
          <Download size={14} />
          <span>Download</span>
        </button>
      )}
      <button
        className="context-menu-item danger"
        onClick={() => { onDelete(node); onClose() }}
      >
        <Trash2 size={14} />
        <span>Delete</span>
      </button>
    </div>
  )
}

// Main FileTree component
const FileTree = ({ onFileClick, isStreaming, refreshTrigger = 0 }) => {
  const [tree, setTree] = useState([])
  const [rootPath, setRootPath] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expandedFolders, setExpandedFolders] = useState(new Set())
  const [contextMenu, setContextMenu] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null)
  
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
  
  // Initial load
  useEffect(() => {
    fetchTree()
  }, [fetchTree])
  
  // Refresh when file system operations are detected (via refreshTrigger)
  useEffect(() => {
    if (refreshTrigger > 0) {
      fetchTree()
    }
  }, [refreshTrigger, fetchTree])
  
  // Also refresh when streaming stops (final catch-all)
  useEffect(() => {
    if (!isStreaming) {
      const timer = setTimeout(fetchTree, 500)
      return () => clearTimeout(timer)
    }
  }, [isStreaming, fetchTree])
  
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
        <span className="file-tree-title">Workspace</span>
        <button 
          className="file-tree-refresh"
          onClick={fetchTree}
          disabled={loading}
          title="Refresh file tree"
        >
          <RefreshCw size={14} className={loading ? 'spin' : ''} />
        </button>
      </div>
      
      <div className="file-tree-content">
        {loading && tree.length === 0 ? (
          <div className="file-tree-loading">
            <RefreshCw size={16} className="spin" />
            <span>Loading...</span>
          </div>
        ) : error ? (
          <div className="file-tree-empty">
            <p>{error}</p>
            <button onClick={fetchTree}>Retry</button>
          </div>
        ) : tree.length === 0 ? (
          <div className="file-tree-empty">
            <Folder size={24} />
            <p>Workspace is empty</p>
            <span>Files will appear here when created</span>
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
        />
      )}

      {/* Delete confirmation dialog */}
      {confirmDelete && (
        <div className="tree-confirm-overlay" onClick={() => setConfirmDelete(null)}>
          <div className="tree-confirm-dialog" onClick={(e) => e.stopPropagation()}>
            <p>Delete <strong>{confirmDelete.name}</strong>{confirmDelete.type === 'folder' ? ' and all its contents' : ''}?</p>
            <div className="tree-confirm-actions">
              <button className="tree-confirm-cancel" onClick={() => setConfirmDelete(null)}>Cancel</button>
              <button className="tree-confirm-delete" onClick={handleDeleteConfirm}>Delete</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default FileTree
