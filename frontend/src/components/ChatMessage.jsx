import React, { useState, useEffect, useRef, useMemo } from 'react'
import { User, Bot, ChevronDown, ChevronRight, Loader2, Brain, RotateCcw, AlertCircle, GitBranch, AlertTriangle } from 'lucide-react'
import useLanguage from '../hooks/useLanguage'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import ToolActivity from './ToolActivity'

/**
 * Collapsible thinking block
 */
function ThinkingBlock({ content, label, isActive, defaultOpen = false }) {
  const [isOpen, setIsOpen] = useState(defaultOpen || isActive)
  
  if (!content) return null
  
  return (
    <div className="thinking-block">
      <button className="thinking-toggle" onClick={() => setIsOpen(!isOpen)}>
        {isOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <Brain size={14} className="thinking-icon" />
        <span className="thinking-label">{label}</span>
        {isActive && <Loader2 size={14} className="spin" />}
      </button>
      {isOpen && (
        <div className="thinking-content">
          <pre>{content}</pre>
        </div>
      )}
    </div>
  )
}

/**
 * Renders markdown content with code highlighting
 */
function MarkdownContent({ content }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ node, inline, className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || '')
          return !inline && match ? (
            <SyntaxHighlighter
              style={oneDark}
              language={match[1]}
              PreTag="div"
              customStyle={{
                margin: '1em 0',
                borderRadius: '8px',
                fontSize: '0.9em'
              }}
              {...props}
            >
              {String(children).replace(/\n$/, '')}
            </SyntaxHighlighter>
          ) : (
            <code className="inline-code" {...props}>
              {children}
            </code>
          )
        }
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

/**
 * Main chat message component
 * Renders user messages and assistant responses with activity timeline
 */
function ChatMessage({ message, msgIdx, isLatest, isStreaming, onRetry, onStopTool, onLoadConversation, subagentChildIds, senderLabel, onForkConversation, forkWarning, forkClickRef, messagesLength, onForkDismiss, appIsStreaming }) {
  const { t } = useLanguage()
  const forkBtnRef = useRef(null)
  const isForkWarning = forkWarning?.frontendMsgIdx === msgIdx

  // Click outside warning to dismiss
  useEffect(() => {
    if (!isForkWarning) return
    const handler = (e) => {
      if (forkBtnRef.current && !forkBtnRef.current.contains(e.target)
          && !e.target.closest('.fork-warning-bar')) {
        onForkDismiss?.()
      }
    }
    const timer = setTimeout(() => document.addEventListener('mousedown', handler), 0)
    return () => { clearTimeout(timer); document.removeEventListener('mousedown', handler) }
  }, [isForkWarning, onForkDismiss])
  const isUser = message.role === 'user'
  const activities = message.activities || []
  const hasContent = message.content && message.content.length > 0
  const isError = message.isError
  const isTimeout = message.isTimeout
  const canRetry = message.canRetry && onRetry
  
  // Group consecutive activities for better display
  // Each block follows: thinking → content → tool_calls/results
  const groupedActivities = useMemo(() => groupActivities(activities, message.content), [activities, message.content])
  
  // Count thinking blocks for labeling
  const thinkingCount = activities.filter(a => a.type === 'thinking').length
  let thinkingIndex = 0

  return (
    <div className={`message ${isUser ? 'user-message' : 'assistant-message'} ${message.isError ? 'error-message' : ''}`}>
      <div className="message-avatar">
        {isUser ? (
          <div className="avatar user-avatar">
            <User size={20} />
          </div>
        ) : (
          <div className="avatar assistant-avatar">
            <Bot size={20} />
          </div>
        )}
      </div>

      <div className="message-content">
        {senderLabel && (
          <div className="sender-label">{senderLabel}</div>
        )}
        {isUser ? (
          <>
            <div className="user-message-row">
              <div className="message-text">
                <p>{message.content}</p>
              </div>
              {/* Fork button — appears on user messages except the first, only when not streaming */}
              {onForkConversation && isUser && msgIdx > 0 && !appIsStreaming && (
                <button
                  ref={forkBtnRef}
                  className={`fork-btn${isForkWarning ? ' fork-btn-warning' : ''}`}
                  title={t('app.forkConversation')}
                  onClick={() => {
                    const now = Date.now()
                    const prev = forkClickRef.current
                    const isDoubleClick = prev.idx === msgIdx && (now - prev.time) < 400
                    forkClickRef.current = { time: now, idx: msgIdx }
                    if (isForkWarning) {
                      onForkConversation(msgIdx, true)
                    } else {
                      onForkConversation(msgIdx, isDoubleClick)
                    }
                  }}
                >
                  <GitBranch size={14} />
                  {isForkWarning && (
                    <span className="fork-btn-label">{t('app.forkAnyway')}</span>
                  )}
                </button>
              )}
            </div>
            {/* Inline warning bar — rendered outside user-message-row for full-width layout */}
            {isForkWarning && (
              <div className="fork-warning-bar">
                <AlertTriangle size={14} />
                <span>
                  {t('app.forkWarning', { count: forkWarning.toolsAfterFork.length })}
                </span>
              </div>
            )}
          </>
        ) : (
          // Assistant message - render activities timeline
          <>
            {groupedActivities.map((group, groupIdx) => {
              const isLastGroup = groupIdx === groupedActivities.length - 1
              
              if (group.type === 'thinking') {
                thinkingIndex++
                const label = thinkingCount > 1
                  ? t('chat.reasoning', { current: thinkingIndex, total: thinkingCount })
                  : (isStreaming && isLatest && isLastGroup ? t('chat.thinking') : t('chat.reasoningShort'))

                return (
                  <ThinkingBlock
                    key={`thinking-${groupIdx}`}
                    content={group.content}
                    label={label}
                    isActive={isStreaming && isLatest && isLastGroup}
                    defaultOpen={isStreaming && isLatest && isLastGroup}
                  />
                )
              }

              if (group.type === 'content') {
                return (
                  <div key={`content-${groupIdx}`} className="message-text">
                    <MarkdownContent content={group.content} />
                  </div>
                )
              }

              if (group.type === 'tool_group') {
                return (
                  <ToolActivity
                    key={`tools-${groupIdx}`}
                    toolCalls={group.toolCalls}
                    toolResults={group.toolResults}
                    onStopTool={isStreaming && isLatest ? onStopTool : null}
                    onLoadConversation={onLoadConversation}
                    subagentChildIds={subagentChildIds}
                  />
                )
              }

              return null
            })}

            {/* Error with retry button */}
            {isError && canRetry && (
              <div className="error-actions">
                <button className="retry-btn" onClick={onRetry}>
                  <RotateCcw size={16} />
                  <span>{isTimeout ? t('chat.retryRequest') : t('chat.tryAgain')}</span>
                </button>
                {isTimeout && (
                  <span className="error-hint">
                    <AlertCircle size={14} />
                    {t('chat.timeoutHint')}
                  </span>
                )}
              </div>
            )}
            
            {/* Streaming indicator when no activities and no content */}
            {isStreaming && isLatest && activities.length === 0 && !hasContent && (
              <div className="typing-indicator">
                <span></span>
                <span></span>
                <span></span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/**
 * Group activities into logical blocks.
 * Each block follows the order: thinking → content → tool_calls/results.
 * Content is inserted between thinking and tool groups when present.
 */
function groupActivities(activities, content) {
  const groups = []
  let currentToolGroup = null
  let contentInserted = false
  
  for (const activity of activities) {
    if (activity.type === 'thinking') {
      if (currentToolGroup) {
        groups.push(currentToolGroup)
        currentToolGroup = null
      }
      groups.push({ type: 'thinking', content: activity.content })
    } 
    else if (activity.type === 'tool_call') {
      if (!currentToolGroup) {
        if (content && !contentInserted) {
          contentInserted = true
          groups.push({ type: 'content', content })
        }
        currentToolGroup = { type: 'tool_group', toolCalls: [], toolResults: [] }
      }
      currentToolGroup.toolCalls.push(activity)
    }
    else if (activity.type === 'tool_result') {
      if (!currentToolGroup) {
        currentToolGroup = { type: 'tool_group', toolCalls: [], toolResults: [] }
      }
      currentToolGroup.toolResults.push(activity)
    }
  }
  
  if (currentToolGroup) {
    groups.push(currentToolGroup)
  }
  
  // If no tool calls, content goes at the end (final response)
  if (content && !contentInserted) {
    groups.push({ type: 'content', content })
  }
  
  return groups
}

export default React.memo(ChatMessage)
