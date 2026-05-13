import React, { forwardRef } from 'react'
import { Send, RotateCcw, ArrowRightFromLine } from 'lucide-react'

/**
 * Chat input area with 4 visual modes:
 * 
 * 1. Normal         — textarea + send button
 * 2. Streaming      — textarea + stop button (or send if text entered = interrupt)
 * 3. PendingInterrupt — disabled textarea + cancel button
 * 4. Hidden         — not rendered (subagent view)
 */
const ChatInput = forwardRef(({
  value,
  onChange,
  isStreaming,
  pendingInterrupt,
  messagesCount,
  onSend,
  onInterruptSend,
  onStop,
  onCancelPendingInterrupt,
  onContinueInNewChat,
}, ref) => {
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (isStreaming) {
        onInterruptSend()
      } else {
        onSend()
      }
    }
  }

  const hasText = value.trim().length > 0

  return (
    <div className="input-container">
      <div className="input-wrapper">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            pendingInterrupt
              ? "Interrupt queued - waiting for safe point..."
              : isStreaming
                ? "Type to interrupt and redirect the agent..."
                : "Ask me anything..."
          }
          rows={1}
          disabled={!!pendingInterrupt}
          className={`chat-input ${isStreaming ? 'streaming-mode' : ''} ${pendingInterrupt ? 'pending-interrupt' : ''}`}
        />
        <div className="input-actions">
          {pendingInterrupt ? (
            <button
              className="send-btn pending-btn"
              onClick={onCancelPendingInterrupt}
              title="Cancel pending interrupt"
            >
              <RotateCcw size={18} />
            </button>
          ) : isStreaming && !hasText ? (
            <button className="stop-btn" onClick={onStop} title="Stop generation">
              <div className="stop-icon" />
            </button>
          ) : isStreaming && hasText ? (
            <button
              className="send-btn interrupt-btn"
              onClick={onInterruptSend}
              title="Send and interrupt current generation"
            >
              <Send size={20} />
            </button>
          ) : (
            <button
              className="send-btn"
              onClick={() => onSend()}
              disabled={!hasText}
            >
              <Send size={20} />
            </button>
          )}
        </div>
      </div>
      <div className="input-hint">
        {pendingInterrupt
          ? `Interrupt queued: "${pendingInterrupt.message.substring(0, 50)}${pendingInterrupt.message.length > 50 ? '...' : ''}" - Waiting for tool calls to complete...`
          : isStreaming
            ? "Type a message to interrupt and redirect the agent with your new instructions."
            : messagesCount > 0
              ? <>
                  AuroraCoder can search, browse, write code, and execute commands.
                  <button
                    className="continue-new-chat-link"
                    onClick={onContinueInNewChat}
                    title="Ask the agent to summarize progress and continue in a fresh context"
                  >
                    <ArrowRightFromLine size={13} />
                    Continue in new chat
                  </button>
                </>
              : "AuroraCoder can search, browse, write code, and execute commands."
        }
      </div>
    </div>
  )
})

ChatInput.displayName = 'ChatInput'
export default ChatInput
