import React, { useState, useMemo } from 'react'
import { 
  X, FileCode, Plus, Minus, Code2, FolderOpen, 
  Maximize2, Minimize2, RefreshCw
} from 'lucide-react'

/**
 * Code Panel - Displays edited files with git-style diff highlighting
 * Shows tabs for each file edited during the session
 */
function CodePanel({ files, activeFileId, onFileSelect, onFileClose, onClose, onRefresh, isLoading }) {
  const [isMinimized, setIsMinimized] = useState(false)
  
  const activeFile = files.find(f => f.id === activeFileId)
  
  // Calculate diff stats for the active file
  const diffStats = useMemo(() => {
    if (!activeFile?.lines) return { added: 0, removed: 0, total: 0 }
    
    const added = activeFile.lines.filter(l => l.type === 'added').length
    const removed = activeFile.lines.filter(l => l.type === 'removed').length
    const total = activeFile.lines.filter(l => l.type !== 'removed').length
    
    return { added, removed, total }
  }, [activeFile])

  if (files.length === 0) {
    return (
      <div className="code-panel">
        <div className="code-panel-header">
          <div className="code-tabs">
            <span className="code-tab active" style={{ cursor: 'default' }}>
              <Code2 size={14} />
              Code View
            </span>
          </div>
          <div className="code-panel-actions">
            <button 
              className="code-action-btn" 
              onClick={onRefresh}
              disabled={isLoading}
              title="Refresh"
            >
              <RefreshCw size={16} className={isLoading ? 'spin' : ''} />
            </button>
            <button className="code-action-btn close-panel" onClick={onClose} title="Close panel">
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="code-empty-state">
          <FolderOpen size={48} />
          <h3>No files edited yet</h3>
          <p>When the agent edits or creates files, they'll appear here with diff highlighting.</p>
        </div>
      </div>
    )
  }

  return (
    <div className={`code-panel ${isMinimized ? 'minimized' : ''}`}>
      {/* Header with tabs */}
      <div className="code-panel-header">
        <div className="code-tabs">
          {files.map(file => (
            <button
              key={file.id}
              className={`code-tab ${file.id === activeFileId ? 'active' : ''} ${file.isViewOnly ? 'view-only' : ''}`}
              onClick={() => onFileSelect(file.id)}
              title={file.isViewOnly ? `Viewing: ${file.path}` : file.path}
            >
              <FileCode size={14} />
              <span>{getFileName(file.path)}</span>
              {file.hasChanges && <span className="code-tab-modified" />}
              {file.isViewOnly && <span className="code-tab-view-badge">view</span>}
              <span 
                className="code-tab-close"
                onClick={(e) => {
                  e.stopPropagation()
                  onFileClose(file.id)
                }}
              >
                <X size={12} />
              </span>
            </button>
          ))}
        </div>
        <div className="code-panel-actions">
          <button 
            className="code-action-btn" 
            onClick={onRefresh}
            disabled={isLoading}
            title="Refresh"
          >
            <RefreshCw size={16} className={isLoading ? 'spin' : ''} />
          </button>
          <button 
            className="code-action-btn" 
            onClick={() => setIsMinimized(!isMinimized)}
            title={isMinimized ? "Expand" : "Minimize"}
          >
            {isMinimized ? <Maximize2 size={16} /> : <Minimize2 size={16} />}
          </button>
          <button className="code-action-btn close-panel" onClick={onClose} title="Close panel">
            <X size={18} />
          </button>
        </div>
      </div>

      {!isMinimized && activeFile && (
        <>
          {/* File info bar */}
          <div className="code-file-info">
            <div className="code-file-path">
              <FileCode size={14} />
              <span>{activeFile.path}</span>
            </div>
            <div className="code-file-stats">
              {diffStats.added > 0 && (
                <span className="stat-added">
                  <Plus size={12} /> {diffStats.added}
                </span>
              )}
              {diffStats.removed > 0 && (
                <span className="stat-removed">
                  <Minus size={12} /> {diffStats.removed}
                </span>
              )}
              <span>{diffStats.total} lines</span>
            </div>
          </div>

          {/* Code content with git-style diff */}
          <div className="code-content">
            <div className="code-file-view">
              {activeFile.lines?.map((line, idx) => (
                <div 
                  key={idx} 
                  className={`code-line ${line.type ? `diff-${line.type}` : ''}`}
                >
                  {/* Line number column - empty for removed lines */}
                  <span className="code-line-number">
                    {line.type === 'removed' ? '' : line.lineNumber || ''}
                  </span>
                  
                  {/* Diff marker column - always same width for alignment */}
                  <span className={`diff-marker ${line.type || 'unchanged'}`}>
                    {line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '}
                  </span>
                  
                  {/* Code content - preserves original indentation */}
                  <span className="code-line-content">{line.content}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/**
 * Get just the filename from a path
 */
function getFileName(path) {
  if (!path) return 'Untitled'
  const parts = path.replace(/\\/g, '/').split('/')
  return parts[parts.length - 1] || 'Untitled'
}

export default CodePanel
