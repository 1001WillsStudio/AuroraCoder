import React, { useState, useEffect, useRef } from 'react'
import { 
  Search, Globe, FileText, FilePlus, FileEdit, Trash2, 
  FolderOpen, Terminal, Package, CheckCircle, Loader2,
  ChevronDown, ChevronRight, Eye, StopCircle
} from 'lucide-react'
import useLanguage from '../hooks/useLanguage'

/**
 * User-friendly tool activity display
 * Shows what the agent is doing in a clear, visual way
 */
function ToolActivity({ toolCalls, toolResults, onStopTool, onLoadConversation, subagentChildIds }) {
  if (!toolCalls || toolCalls.length === 0) return null

  // subagentChildIds is now a map { tool_call_id → child_id }
  // For backward compatibility with older sessions, also support _fallback array
  const fallbackIds = subagentChildIds?._fallback || []
  let fallbackIdx = 0
  return (
    <div className="tool-activity-section">
      {toolCalls.map((tc, idx) => {
        const toolResult = toolResults?.find(r => r.tool_call_id === tc.id)
        // Use tool_call_id → child_id map, fall back to index-based if no mapping
        const childId = tc.name === 'subagent'
          ? (subagentChildIds?.[tc.id] || fallbackIds[fallbackIdx++])
          : null
        return (
          <ToolActivityItem
            key={tc.id || idx}
            toolCall={tc}
            result={toolResult}
            onStop={onStopTool}
            onLoadConversation={onLoadConversation}
            childConversationId={childId}
          />
        )
      })}
    </div>
  )
}

function ToolActivityItem({ toolCall, result, onStop, onLoadConversation, childConversationId }) {
  const { t } = useLanguage()
  const [expanded, setExpanded] = useState(true)  // Open by default
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const startTimeRef = useRef(Date.now())
  const isComplete = !!result
  const isTerminated = result?.isTerminated
  
  // Track elapsed time for running tools
  useEffect(() => {
    if (isComplete) return
    
    // Reset start time when tool starts
    startTimeRef.current = Date.now()
    setElapsedSeconds(0)
    
    const interval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startTimeRef.current) / 1000)
      setElapsedSeconds(elapsed)
    }, 1000)
    
    return () => clearInterval(interval)
  }, [isComplete, toolCall.id])
  
  // Parse arguments safely — LLM sometimes produces malformed JSON
  let args = {}
  try {
    const rawArgs = toolCall.arguments
    if (typeof rawArgs === 'string' && rawArgs.trim()) {
      args = JSON.parse(rawArgs) || {}
    } else if (typeof rawArgs === 'object') {
      args = rawArgs || {}
    }
  } catch (e) {
    // Downgraded to debug: LLM-generated JSON is occasionally malformed.
    // This is handled gracefully by falling back to empty args.
    console.debug('[ToolActivityItem] Failed to parse arguments:', e.message)
    args = {}
  }

  const config = getToolConfig(toolCall.name, args, result, t)
  
  // Format elapsed time
  const formatElapsed = (seconds) => {
    if (seconds < 60) return `${seconds}s`
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}m ${secs}s`
  }
  
  // Handle stop click
  const handleStopClick = (e) => {
    e.stopPropagation()
    if (onStop) {
      onStop({
        toolCall,
        toolName: toolCall.name,
        elapsedSeconds,
        config
      })
    }
  }
  
  const handleHeaderClick = () => {
    if (config.isSubagent && childConversationId && onLoadConversation) {
      onLoadConversation(childConversationId)
    } else if (config.hasExpandedView) {
      setExpanded(!expanded)
    }
  }

  return (
    <div className={`tool-activity-item ${isComplete ? (isTerminated ? 'terminated' : 'complete') : 'running'}`}>
      <div
        className={`tool-activity-header${config.isSubagent && childConversationId ? ' clickable-subagent' : ''}`}
        onClick={handleHeaderClick}
      >
        <span className="tool-activity-icon">{config.icon}</span>
        <span className="tool-activity-label">{config.label}</span>
        <span className="tool-activity-detail">{config.detail}</span>

        {config.isSubagent && childConversationId && (
          <span className="subagent-view-link">{t('tool.viewSubagent')}</span>
        )}
        
        {/* Elapsed time for running tools */}
        {!isComplete && (
          <span className="tool-activity-elapsed">{formatElapsed(elapsedSeconds)}</span>
        )}
        
        <span className="tool-activity-status">
          {isComplete ? (
            isTerminated ? (
              <StopCircle size={16} className="status-terminated" />
            ) : (
              <CheckCircle size={16} className="status-complete" />
            )
          ) : (
            <Loader2 size={16} className="status-running spin" />
          )}
        </span>
        
        {/* Stop button for running tools */}
        {!isComplete && onStop && (
          <button 
            className="tool-stop-btn"
            onClick={handleStopClick}
            title={t('tool.stopTitle')}
          >
            <StopCircle size={14} />
            <span>{t('tool.stop')}</span>
          </button>
        )}
        
        {config.hasExpandedView && (
          <span className="tool-activity-expand">
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </span>
        )}
      </div>
      
      {expanded && config.hasExpandedView && (
        <div className="tool-activity-content">
          {config.expandedContent}
        </div>
      )}
    </div>
  )
}

/**
 * Get display configuration for each tool type
 */
function getToolConfig(toolName, args, result, t) {
  const resultContent = result?.content || ''
  
  switch (toolName) {
    case 'google_search':
      return {
        icon: <Search size={16} />,
        label: t('tool.searching'),
        detail: `"${args.search_term || 'the web'}"`,
        hasExpandedView: false
      }
    
    case 'web_browser':
      return {
        icon: <Globe size={16} />,
        label: t('tool.reading'),
        detail: truncateUrl(args.target_url || 'webpage'),
        hasExpandedView: false
      }
    
    case 'read_file':
      return {
        icon: <Eye size={16} />,
        label: t('tool.readingFile'),
        detail: args.target_file || 'file',
        hasExpandedView: false
      }
    
    case 'write_file':
      return {
        icon: <FilePlus size={16} />,
        label: t('tool.creatingFile'),
        detail: args.target_file || 'file',
        hasExpandedView: true,
        expandedContent: <FilePreview content={args.code_edit} isNew={true} />
      }
    
    case 'edit_file': {
      const edits = args.edits || []
      const editCount = edits.length
      return {
        icon: <FileEdit size={16} />,
        label: t('tool.editingFile'),
        detail: `${args.target_file || 'file'}${editCount > 1 ? ` (${editCount} edits)` : ''}`,
        hasExpandedView: editCount > 0,
        expandedContent: (
          <div className="multi-edit-view">
            {edits.map((edit, i) => (
              <EditRangeView
                key={i}
                edit={edit}
                editIndex={edits.length > 1 ? i + 1 : null}
              />
            ))}
          </div>
        )
      }
    }
    
    case 'delete_file':
      return {
        icon: <Trash2 size={16} />,
        label: t('tool.deletingFile'),
        detail: args.target_file || 'file',
        hasExpandedView: false
      }
    
    case 'close_file':
      return {
        icon: <FileText size={16} />,
        label: t('tool.closingFile'),
        detail: args.target_file || 'file',
        hasExpandedView: false
      }
    
    case 'list_directory':
      return {
        icon: <FolderOpen size={16} />,
        label: t('tool.listingDirectory'),
        detail: args.relative_workspace_path || '/',
        hasExpandedView: false
      }
    
    case 'search_files':
      return {
        icon: <Search size={16} />,
        label: t('tool.searchingFiles'),
        detail: `"${args.query || ''}"`,
        hasExpandedView: false
      }
    
    case 'grep_search':
      return {
        icon: <Search size={16} />,
        label: t('tool.searchingInFiles'),
        detail: `"${args.query || ''}"`,
        hasExpandedView: false
      }
    
    case 'run_terminal_command':
      return {
        icon: <Terminal size={16} />,
        label: t('tool.runningCommand'),
        detail: truncateCommand(args.command || ''),
        hasExpandedView: true,
        expandedContent: <CommandPreview command={args.command} output={resultContent} />
      }
    
    case 'subagent':
      return {
        icon: <Package size={16} />,
        label: t('tool.subagent'),
        detail: (args.task || '').slice(0, 60) + ((args.task || '').length > 60 ? '...' : ''),
        hasExpandedView: false,
        isSubagent: true,
      }

    case 'tool_store':
      return {
        icon: <Package size={16} />,
        label: args.action === 'search' ? t('tool.searchingTools') : t('tool.usingTool'),
        detail: args.query || args.tool_name || '',
        hasExpandedView: false
      }
    
    default:
      return {
        icon: <Package size={16} />,
        label: toolName?.replace(/_/g, ' ') || 'Tool',
        detail: '',
        hasExpandedView: false
      }
  }
}

/**
 * Parse a remove_line_number string like "13-15" or "42" into { start, end }.
 */
function parseRemoveLineNumber(remove_line_number) {
  if (!remove_line_number) return { start: null, end: null }
  const parts = String(remove_line_number).split('-')
  const start = parseInt(parts[0], 10)
  const end = parts.length > 1 ? parseInt(parts[1], 10) : start
  return isNaN(start) ? { start: null, end: null } : { start, end: isNaN(end) ? start : end }
}

/**
 * Parse content_to_remove anchor string.
 * Multi-line format: "first_line\n[TO]\nlast_line"
 * Single-line format: just the line content (no [TO] marker)
 */
function parseContentToRemove(content_to_remove) {
  if (!content_to_remove) return { first: '', last: '', isMultiLine: false }
  const marker = '\n[TO]\n'
  const idx = content_to_remove.indexOf(marker)
  if (idx === -1) {
    // Single-line: no [TO] marker
    return { first: content_to_remove, last: content_to_remove, isMultiLine: false }
  }
  return {
    first: content_to_remove.slice(0, idx),
    last: content_to_remove.slice(idx + marker.length),
    isMultiLine: true
  }
}

/**
 * Displays a range-based edit: shows the replaced range context
 * and the full replacement content with line numbers.
 */
function EditRangeView({ edit, editIndex }) {
  const { t } = useLanguage()
  // Parse new-format fields
  const { remove_line_number, content_to_remove, replace_content } = edit
  const { start: start_line, end: end_line } = parseRemoveLineNumber(remove_line_number)
  const { first: start_content, last: end_content, isMultiLine } = parseContentToRemove(content_to_remove)

  const isDelete = !replace_content
  const effectiveEnd = end_line || start_line

  let locationLabel = ''
  if (start_line != null && effectiveEnd != null && start_line !== effectiveEnd) {
    locationLabel = `Lines ${start_line}–${effectiveEnd}`
  } else if (start_line != null) {
    locationLabel = `Line ${start_line}`
  }

  const rangeSpan = (effectiveEnd != null && start_line != null) ? (effectiveEnd - start_line + 1) : 1

  const lines = isDelete ? [] : (replace_content || '').split('\n')
  const maxPreview = 30
  const hasMore = lines.length > maxPreview
  const displayLines = hasMore ? lines.slice(0, maxPreview) : lines

  const showRemovedStart = !!start_content
  const showRemovedEnd = isMultiLine && effectiveEnd !== start_line && !!end_content
  const showEllipsis = rangeSpan > 2 && isMultiLine

  return (
    <div className="diff-view">
      {(editIndex || locationLabel) && (
        <div className="diff-location">
          {editIndex && <span className="diff-edit-index">{t('tool.editIndex', { n: editIndex })}</span>}
          {editIndex && locationLabel && <span className="diff-location-sep"> · </span>}
          {locationLabel && <span>{locationLabel}</span>}
          {isDelete && <span className="diff-delete-badge"> {t('tool.deletedBadge')}</span>}
        </div>
      )}
      {showRemovedStart && (
        <div className="diff-removed">
          <div className="diff-line removed">
            <span className="diff-line-num">{start_line}</span>
            <span className="diff-content">{start_content}</span>
          </div>
          {showEllipsis && (
            <div className="diff-line removed diff-line-ellipsis">
              <span className="diff-line-num">⋮</span>
              <span className="diff-content diff-ellipsis-text">({t('tool.moreLines', { n: rangeSpan - 2 })})</span>
            </div>
          )}
          {showRemovedEnd && (
            <div className="diff-line removed">
              <span className="diff-line-num">{effectiveEnd}</span>
              <span className="diff-content">{end_content}</span>
            </div>
          )}
        </div>
      )}
      {!isDelete && (
        <div className="diff-added">
          {displayLines.map((line, idx) => (
            <div key={idx} className="diff-line added">
              <span className="diff-line-num">{(start_line || 1) + idx}</span>
              <span className="diff-content">{line || ' '}</span>
            </div>
          ))}
          {hasMore && (
            <div className="diff-more">{t('tool.moreLines', { n: lines.length - maxPreview })}</div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Preview for new file content
 */
function FilePreview({ content, isNew }) {
  const { t } = useLanguage()
  if (!content) return null
  
  const lines = content.split('\n')
  const displayLines = lines.slice(0, 20) // Show first 20 lines
  const hasMore = lines.length > 20
  
  return (
    <div className="file-preview">
      <div className="file-preview-content">
        {displayLines.map((line, idx) => (
          <div key={idx} className={`file-line ${isNew ? 'new' : ''}`}>
            <span className="line-number">{idx + 1}</span>
            <span className="line-content">{line || ' '}</span>
          </div>
        ))}
        {hasMore && (
          <div className="file-preview-more">
            {t('tool.moreLines', { n: lines.length - 20 })}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * Terminal command preview with output
 */
function CommandPreview({ command, output }) {
  const { t } = useLanguage()
  const [showOutput, setShowOutput] = useState(false)
  
  return (
    <div className="command-preview">
      <div className="command-line">
        <span className="command-prompt">$</span>
        <code>{command}</code>
      </div>
      {output && (
        <>
          <button 
            className="command-output-toggle"
            onClick={() => setShowOutput(!showOutput)}
          >
            {showOutput ? t('tool.hideOutput') : t('tool.showOutput')}
          </button>
          {showOutput && (
            <pre className="command-output">{output.slice(0, 2000)}</pre>
          )}
        </>
      )}
    </div>
  )
}

/**
 * Utility functions
 */
function truncateUrl(url) {
  if (!url) return ''
  try {
    const parsed = new URL(url)
    return parsed.hostname + (parsed.pathname.length > 20 ? parsed.pathname.slice(0, 20) + '...' : parsed.pathname)
  } catch {
    return url.length > 40 ? url.slice(0, 40) + '...' : url
  }
}

function truncateCommand(cmd) {
  if (!cmd) return ''
  return cmd.length > 50 ? cmd.slice(0, 50) + '...' : cmd
}

export default ToolActivity
