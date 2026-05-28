import React, { useState, useMemo, useRef, useLayoutEffect } from 'react'
import { 
  X, FileCode, Plus, Minus, Code2, FolderOpen, 
  RefreshCw
} from 'lucide-react'
import useLanguage from '../hooks/useLanguage'

/**
 * Code Panel - Displays edited files with git-style diff highlighting
 * Shows tabs for each file edited during the session
 */
function CodePanel({ files, activeFileId, onFileSelect, onFileClose, onClose, onRefresh, isLoading }) {
  const { t } = useLanguage()
  const tabsRef = useRef(null)
  const codeContentRef = useRef(null)
  
  const activeFile = files.find(f => f.id === activeFileId)
  
  // Calculate diff stats for the active file
  const diffStats = useMemo(() => {
    if (!activeFile?.lines) return { added: 0, removed: 0, total: 0 }
    
    const added = activeFile.lines.filter(l => l.type === 'added').length
    const removed = activeFile.lines.filter(l => l.type === 'removed').length
    const total = activeFile.lines.filter(l => l.type !== 'removed').length
    
    return { added, removed, total }
  }, [activeFile])
  // Track previous line count per file to distinguish new files from new edits
  const prevLineCountsRef = useRef(new Map())

  // ── Smart scroll: new file → first edit; updated file → latest edit; tab switch → no jump ──
  useLayoutEffect(() => {
    if (!activeFile || !codeContentRef.current) return

    const fileId = activeFile.id
    const currCount = activeFile.lines?.length ?? 0
    const prevCount = prevLineCountsRef.current.get(fileId) ?? -1
    const isNewFile = prevCount === -1
    const hasNewLines = currCount > prevCount

    if (!isNewFile && !hasNewLines) return

    // Update tracking before async scroll so subsequent renders see the new count
    const next = new Map(prevLineCountsRef.current)
    next.set(fileId, currCount)
    prevLineCountsRef.current = next

    const raf = requestAnimationFrame(() => {
      const container = codeContentRef.current
      if (!container) return
      const diffLines = container.querySelectorAll('.diff-added, .diff-removed')
      if (diffLines.length === 0) return

      // New file → first edit. Updated file → latest (new) edit.
      const targetLine = isNewFile ? diffLines[0] : diffLines[diffLines.length - 1]
      targetLine.scrollIntoView({ block: 'center', behavior: 'smooth' })
    })
    return () => cancelAnimationFrame(raf)
  }, [activeFile])
  if (files.length === 0) {
    return (
    <div className="code-panel">
        <div className="code-panel-header">
          <div className="code-tabs">
            <span className="code-tab active" style={{ cursor: 'default' }}>
              <Code2 size={14} />
              {t('code.codeView')}
            </span>
          </div>
          <div className="code-panel-actions">
            <button 
              className="code-action-btn" 
              onClick={onRefresh}
              disabled={isLoading}
              title={t('code.refresh')}
            >
              <RefreshCw size={16} className={isLoading ? 'spin' : ''} />
            </button>
            <button className="code-action-btn close-panel" onClick={onClose} title={t('code.closePanel')}>
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="code-empty-state">
          <FolderOpen size={48} />
          <h3>{t('code.noFilesEdited')}</h3>
          <p>{t('code.noFilesHint')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="code-panel">
      {/* Header with tabs */}
      <div className="code-panel-header">
        <div className="code-tabs" ref={tabsRef} onWheel={(e) => { if (tabsRef.current) { e.preventDefault(); tabsRef.current.scrollLeft += e.deltaY } }}>
          {files.map(file => (
            <button
              key={file.id}
              className={`code-tab ${file.id === activeFileId ? 'active' : ''} ${file.isViewOnly ? 'view-only' : ''}`}
              onClick={() => onFileSelect(file.id)}
              title={file.isViewOnly ? t('code.viewing', { path: file.path }) : file.path}
            >
              <FileCode size={14} />
              <span>{getFileName(file.path)}</span>
              {file.hasChanges && <span className="code-tab-modified" />}
              {file.isViewOnly && <span className="code-tab-view-badge">{t('code.viewBadge')}</span>}
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
            title={t('code.refresh')}
          >
            <RefreshCw size={16} className={isLoading ? 'spin' : ''} />
          </button>
          <button className="code-action-btn close-panel" onClick={onClose} title={t('code.closePanel')}>
            <X size={18} />
          </button>
        </div>
      </div>

      {activeFile && (
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
              <span>{t('code.lines', { n: diffStats.total })}</span>
            </div>
          </div>

          {/* Code content with git-style diff */}
          <div className="code-content" ref={codeContentRef}>
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
