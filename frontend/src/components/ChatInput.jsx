import React, { forwardRef } from 'react'
import { Send, RotateCcw, ArrowRightFromLine } from 'lucide-react'
import useLanguage from '../hooks/useLanguage'

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
  const { t } = useLanguage()
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
              ? t('chat.placeholder.pendingInterrupt')
              : isStreaming
                ? t('chat.placeholder.streaming')
                : t('chat.placeholder.normal')
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
              title={t('chat.title.cancelPending')}
            >
              <RotateCcw size={18} />
            </button>
          ) : isStreaming && !hasText ? (
            <button className="stop-btn" onClick={onStop} title={t('chat.title.stop')}>
              <div className="stop-icon" />
            </button>
          ) : isStreaming && hasText ? (
            <button
              className="send-btn interrupt-btn"
              onClick={onInterruptSend}
              title={t('chat.title.interrupt')}
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
          ? t('chat.interruptQueued', { msg: pendingInterrupt.message.substring(0, 50) + (pendingInterrupt.message.length > 50 ? '...' : '') })
          : isStreaming
            ? t('chat.hint.streaming')
            : messagesCount > 0
              ? <>
                  {t('chat.hint.normal')}
                  <button
                    className="continue-new-chat-link"
                    onClick={onContinueInNewChat}
                    title={t('chat.continueNewChatTitle')}
                  >
                    <ArrowRightFromLine size={13} />
                    {t('chat.continueNewChat')}
                  </button>
                </>
              : t('chat.hint.normal')
        }
      </div>
    </div>
  )
})

ChatInput.displayName = 'ChatInput'
export default ChatInput
