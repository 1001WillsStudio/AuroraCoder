import React, { useState, useEffect, useRef } from 'react'
import { 
  Search, Globe, FileText, FilePlus, FileEdit, Trash2, 
  FolderOpen, Terminal, Package, CheckCircle, Loader2,
  ChevronDown, ChevronRight, Eye, StopCircle
} from 'lucide-react'

/**
 * User-friendly tool activity display
 * Shows what the agent is doing in a clear, visual way
 */
function ToolActivity({ toolCalls, toolResults, onStopTool, onLoadConversation, subagentChildIds }) {
  if (!toolCalls || toolCalls.length === 0) return null

  let subagentIdx = 0
  return (
    <div className="tool-activity-section">
      {toolCalls.map((tc, idx) => {
        const toolResult = toolResults?.find(r => r.tool_call_id === tc.id)
        const childId = tc.name === 'subagent' ? subagentChildIds?.[subagentIdx++] : null
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
  
  // Parse arguments safely
  let args = {}
  try {
    const rawArgs = toolCall.arguments
    if (typeof rawArgs === 'string' && rawArgs.trim()) {
      args = JSON.parse(rawArgs)
    } else if (typeof rawArgs === 'object') {
      args = rawArgs || {}
    }
  } catch (e) {
    console.warn('[ToolActivityItem] Failed to parse arguments:', e)
    args = {}
  }

  const config = getToolConfig(toolCall.name, args, result)
  
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
          <span className="subagent-view-link">View →</span>
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
            title="Stop this tool"
          >
            <StopCircle size={14} />
            <span>Stop</span>
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
function getToolConfig(toolName, args, result) {
  const resultContent = result?.content || ''
  
  switch (toolName) {
    case 'google_search':
      return {
        icon: <Search size={16} />,
        label: 'Searching',
        detail: `"${args.search_term || 'the web'}"`,
        hasExpandedView: false
      }
    
    case 'web_browser':
      return {
        icon: <Globe size={16} />,
        label: 'Reading',
        detail: truncateUrl(args.target_url || 'webpage'),
        hasExpandedView: false
      }
    
    case 'read_file':
      return {
        icon: <Eye size={16} />,
        label: 'Reading file',
        detail: args.target_file || 'file',
        hasExpandedView: false
      }
    
    case 'write_file':
      return {
        icon: <FilePlus size={16} />,
        label: 'Creating file',
        detail: args.target_file || 'file',
        hasExpandedView: true,
        expandedContent: <FilePreview content={args.code_edit} isNew={true} />
      }
    
    case 'edit_file':
      return {
        icon: <FileEdit size={16} />,
        label: 'Editing file',
        detail: args.target_file || 'file',
        hasExpandedView: true,
        expandedContent: (
          <DiffView 
            removed={args.search_content} 
            added={args.replace_content}
            startLine={args.start_line}
          />
        )
      }
    
    case 'delete_file':
      return {
        icon: <Trash2 size={16} />,
        label: 'Deleting file',
        detail: args.target_file || 'file',
        hasExpandedView: false
      }
    
    case 'close_file':
      return {
        icon: <FileText size={16} />,
        label: 'Closing file',
        detail: args.target_file || 'file',
        hasExpandedView: false
      }
    
    case 'list_directory':
      return {
        icon: <FolderOpen size={16} />,
        label: 'Listing directory',
        detail: args.relative_workspace_path || '/',
        hasExpandedView: false
      }
    
    case 'search_files':
      return {
        icon: <Search size={16} />,
        label: 'Searching files',
        detail: `"${args.query || ''}"`,
        hasExpandedView: false
      }
    
    case 'grep_search':
      return {
        icon: <Search size={16} />,
        label: 'Searching in files',
        detail: `"${args.query || ''}"`,
        hasExpandedView: false
      }
    
    case 'run_terminal_command':
      return {
        icon: <Terminal size={16} />,
        label: 'Running command',
        detail: truncateCommand(args.command || ''),
        hasExpandedView: true,
        expandedContent: <CommandPreview command={args.command} output={resultContent} />
      }
    
    case 'subagent':
      return {
        icon: <Package size={16} />,
        label: 'Subagent',
        detail: (args.task || '').slice(0, 60) + ((args.task || '').length > 60 ? '...' : ''),
        hasExpandedView: false,
        isSubagent: true,
      }

    case 'tool_store':
      return {
        icon: <Package size={16} />,
        label: args.action === 'search' ? 'Searching tools' : 'Using tool',
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
 * Diff view for file edits - shows removed lines in red, added lines in green
 */
function DiffView({ removed, added, startLine }) {
  if (!removed && !added) {
    return <div className="diff-empty">No changes</div>
  }

  const removedLines = (removed || '').split('\n')
  const addedLines = (added || '').split('\n')
  
  return (
    <div className="diff-view">
      {startLine && (
        <div className="diff-location">Line {startLine}</div>
      )}
      {removed && (
        <div className="diff-removed">
          {removedLines.map((line, idx) => (
            <div key={`r-${idx}`} className="diff-line removed">
              <span className="diff-prefix">-</span>
              <span className="diff-content">{line || ' '}</span>
            </div>
          ))}
        </div>
      )}
      {added && (
        <div className="diff-added">
          {addedLines.map((line, idx) => (
            <div key={`a-${idx}`} className="diff-line added">
              <span className="diff-prefix">+</span>
              <span className="diff-content">{line || ' '}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Preview for new file content
 */
function FilePreview({ content, isNew }) {
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
            ... {lines.length - 20} more lines
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
            {showOutput ? 'Hide output' : 'Show output'}
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
