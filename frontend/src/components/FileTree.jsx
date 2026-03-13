import React, { useState, useEffect, useCallback } from 'react'
import { 
  Folder, FolderOpen, File, ChevronRight, ChevronDown, 
  RefreshCw, FileCode, FileText, Image, Database, Settings,
  FileJson, FileType, Coffee, Braces
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
const TreeNode = ({ node, level = 0, onFileClick, expandedFolders, toggleFolder }) => {
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
  
  return (
    <div className="tree-node">
      <div 
        className={`tree-item ${isFolder ? 'folder' : 'file'}`}
        style={{ paddingLeft: `${level * 16 + 8}px` }}
        onClick={handleClick}
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
            />
          ))}
        </div>
      )}
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
  
  // Get root folder name from path
  const rootName = rootPath ? rootPath.split(/[/\\]/).pop() : 'Workspace'
  
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
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default FileTree
