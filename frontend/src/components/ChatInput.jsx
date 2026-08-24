import React, { forwardRef, useEffect, useRef, useState } from 'react'
import { Send, RotateCcw, ArrowRightFromLine, AtSign, X } from 'lucide-react'
import useLanguage from '../hooks/useLanguage'
import { isPrimaryClick } from '../utils/composerGuard'
import { applyAtPick, fileNameOf, parseAtQuery } from '../utils/attachedFiles'

/**
 * Chat input area with 4 visual modes:
 *
 * 1. Normal         — textarea + send button
 * 2. Streaming      — textarea + stop button (or send if text entered = interrupt)
 * 3. PendingInterrupt — disabled textarea + cancel button
 * 4. Hidden         — not rendered (subagent view)
 *
 * Workspace files can be attached as chips (`@` picker or the file-tree
 * "Add to chat" action). Chips travel with the send; they are not typed text.
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
  attachedFiles = [],
  onAttachFile,
  onDetachFile,
  onSearchFiles,
}, ref) => {
  const { t } = useLanguage()
  const [atQuery, setAtQuery] = useState(null)
  const [hits, setHits] = useState([])
  const [activeHit, setActiveHit] = useState(0)
  const [searching, setSearching] = useState(false)
  const debounceRef = useRef(null)
  const pickerRef = useRef(null)

  const attached = Array.isArray(attachedFiles) ? attachedFiles : []
  const hasText = value.trim().length > 0
  const canSend = hasText || attached.length > 0
  const pickerOpen = Boolean(atQuery) && !pendingInterrupt

  useEffect(() => {
    if (!pickerOpen) return undefined
    const onPointerDown = (event) => {
      const picker = pickerRef.current
      const inputEl = typeof ref === 'object' ? ref?.current : null
      const target = event.target
      if (picker?.contains(target) || inputEl?.contains?.(target)) return
      if (typeof target.closest === 'function' && target.closest('.attach-file-btn')) return
      setAtQuery(null)
    }
    document.addEventListener('mousedown', onPointerDown)
    return () => document.removeEventListener('mousedown', onPointerDown)
  }, [pickerOpen, ref])

  useEffect(() => {
    if (!atQuery || !onSearchFiles) {
      setHits([])
      setSearching(false)
      return undefined
    }
    const query = atQuery.query
    if (!query) {
      setHits([])
      setSearching(false)
      return undefined
    }
    setSearching(true)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(async () => {
      try {
        const result = await onSearchFiles(query)
        const files = Array.isArray(result?.files) ? result.files : []
        setHits(files.filter((f) => !attached.includes(f.path)))
        setActiveHit(0)
      } catch {
        setHits([])
      } finally {
        setSearching(false)
      }
    }, 120)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [atQuery, attached, onSearchFiles])

  const pickHit = (hit) => {
    if (!hit?.path || !onAttachFile) return
    onAttachFile(hit.path)
    if (atQuery) onChange(applyAtPick(value, atQuery))
    setAtQuery(null)
    setHits([])
    requestAnimationFrame(() => {
      const el = typeof ref === 'object' ? ref?.current : null
      el?.focus()
    })
  }

  const syncAtQuery = (text, cursor) => {
    setAtQuery(parseAtQuery(text, cursor))
  }

  const handleChange = (e) => {
    onChange(e.target.value)
    syncAtQuery(e.target.value, e.target.selectionStart)
  }

  const handleKeyDown = (e) => {
    if (pickerOpen) {
      if (hits.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          setActiveHit((i) => (i + 1) % hits.length)
          return
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault()
          setActiveHit((i) => (i - 1 + hits.length) % hits.length)
          return
        }
        if ((e.key === 'Enter' && !e.shiftKey) || (e.key === 'Tab' && !e.shiftKey)) {
          e.preventDefault()
          pickHit(hits[activeHit])
          return
        }
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        return
      }
    }
    if (e.key === 'Escape' && atQuery) {
      e.preventDefault()
      setAtQuery(null)
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (e.repeat) return
      if (isStreaming) {
        onInterruptSend()
      } else {
        onSend()
      }
    }
  }

  const queuedPreview = () => {
    if (!pendingInterrupt) return ''
    const text = (pendingInterrupt.message || '').trim()
    if (text) return text.substring(0, 50) + (text.length > 50 ? '...' : '')
    const names = (pendingInterrupt.attachedFiles || []).map(fileNameOf).filter(Boolean)
    if (names.length) {
      const joined = names.join(', ')
      return joined.substring(0, 50) + (joined.length > 50 ? '...' : '')
    }
    return '…'
  }

  const handleAtButton = () => {
    if (pendingInterrupt) return
    const el = typeof ref === 'object' ? ref?.current : null
    const cursor = el ? el.selectionStart : value.length
    const prefix = cursor > 0 && !/\s$/.test(value.slice(0, cursor)) ? ' @' : '@'
    const next = `${value.slice(0, cursor)}${prefix}${value.slice(cursor)}`
    const nextCursor = cursor + prefix.length
    onChange(next)
    setAtQuery(parseAtQuery(next, nextCursor))
    requestAnimationFrame(() => {
      if (!el) return
      el.focus()
      el.setSelectionRange(nextCursor, nextCursor)
    })
  }

  return (
    <div className="input-container">
      {attached.length > 0 && (
        <div className="attached-file-chips" data-testid="chat-attached-files">
          {attached.map((path) => (
            <span key={path} className="attached-file-chip" title={path}>
              <AtSign size={12} />
              <span className="attached-file-chip-name">{fileNameOf(path)}</span>
              <button
                type="button"
                className="attached-file-chip-remove"
                onClick={() => onDetachFile?.(path)}
                title={t('chat.detachFile')}
                aria-label={t('chat.detachFile')}
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="input-stack">
        {pickerOpen && (
          <div className="at-file-picker" ref={pickerRef} data-testid="chat-file-picker">
            {searching && hits.length === 0 ? (
              <div className="at-file-picker-empty">{t('chat.filePickerSearching')}</div>
            ) : hits.length === 0 ? (
              <div className="at-file-picker-empty">
                {atQuery.query ? t('chat.filePickerEmpty') : t('chat.filePickerHint')}
              </div>
            ) : (
              hits.map((hit, idx) => (
                <button
                  type="button"
                  key={hit.path}
                  className={`at-file-picker-item${idx === activeHit ? ' active' : ''}`}
                  onMouseDown={(e) => { e.preventDefault(); pickHit(hit) }}
                >
                  <span className="at-file-picker-name">{hit.name}</span>
                  <span className="at-file-picker-path">{hit.path}</span>
                </button>
              ))
            )}
          </div>
        )}
        <div className="input-wrapper">
          <textarea
            ref={ref}
            value={value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            onClick={(e) => syncAtQuery(e.target.value, e.target.selectionStart)}
            onKeyUp={(e) => syncAtQuery(e.target.value, e.target.selectionStart)}
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
            data-testid="chat-input"
          />
          <div className="input-actions">
            {!pendingInterrupt && (
              <button
                type="button"
                className="attach-file-btn"
                onClick={handleAtButton}
                title={t('chat.attachFileTitle')}
                data-testid="chat-attach"
              >
                <AtSign size={18} />
              </button>
            )}
            {pendingInterrupt ? (
              <button
                className="send-btn pending-btn"
                onClick={onCancelPendingInterrupt}
                title={t('chat.title.cancelPending')}
                data-testid="chat-stop"
              >
                <RotateCcw size={18} />
              </button>
            ) : isStreaming && !canSend ? (
              <button className="stop-btn" onClick={(e) => { if (!isPrimaryClick(e)) return; onStop() }} title={t('chat.title.stop')} data-testid="chat-stop">
                <div className="stop-icon" />
              </button>
            ) : isStreaming && canSend ? (
              <button
                className="send-btn interrupt-btn"
                onClick={(e) => { if (!isPrimaryClick(e)) return; onInterruptSend() }}
                title={t('chat.title.interrupt')}
                data-testid="chat-send"
              >
                <Send size={20} />
              </button>
            ) : (
              <button
                className="send-btn"
                onClick={(e) => { if (!isPrimaryClick(e)) return; onSend() }}
                disabled={!canSend}
                data-testid="chat-send"
              >
                <Send size={20} />
              </button>
            )}
          </div>
        </div>
      </div>
      <div className="input-hint">
        {pendingInterrupt
          ? t('chat.interruptQueued', { msg: queuedPreview() })
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
              : t('chat.hint.attach')}
      </div>
    </div>
  )
})

ChatInput.displayName = 'ChatInput'
export default ChatInput
