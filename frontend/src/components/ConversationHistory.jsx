import React, { useState, useEffect, useRef } from 'react'
import { History, X, Search } from 'lucide-react'
import { listConversations, getActiveStreams } from '../services/api'
import useLanguage from '../hooks/useLanguage'

function relativeTime(isoString, t) {
  if (!isoString) return ''
  const now = Date.now()
  const then = new Date(isoString).getTime()
  const diff = Math.max(0, now - then)
  const seconds = Math.floor(diff / 1000)
  if (seconds < 60) return t('history.justNow')
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return t('history.minutesAgo', { n: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('history.hoursAgo', { n: hours })
  const days = Math.floor(hours / 24)
  if (days < 7) return t('history.daysAgo', { n: days })
  return new Date(isoString).toLocaleDateString()
}

function statusDotClass(status, isActive) {
  if (isActive) return 'status-dot active'
  if (status === 'error' || status === 'interrupted') return 'status-dot error'
  return ''
}

function groupConversations(conversations) {
  const mainConvs = []
  const childrenByParent = new Map()

  for (const conv of conversations) {
    if (conv.parent_id) {
      const list = childrenByParent.get(conv.parent_id) || []
      list.push(conv)
      childrenByParent.set(conv.parent_id, list)
    } else {
      mainConvs.push(conv)
    }
  }

  for (const list of childrenByParent.values()) {
    list.sort((a, b) => (a.updated_at || '').localeCompare(b.updated_at || ''))
  }

  const result = []
  for (const main of mainConvs) {
    result.push(main)
    const children = childrenByParent.get(main.id)
    if (children) {
      for (const child of children) {
        result.push(child)
      }
    }
  }
  return result
}

// ─── Inline "current session" view (always visible in sidebar) ──────────
function CurrentSession({ currentConversationId, conversations, activeIds, onSelect, t }) {
  if (!currentConversationId) return null

  const current = conversations.find(c => c.id === currentConversationId)
  if (!current) return null

  const mainId = current.parent_id || current.id
  const main = current.parent_id
    ? conversations.find(c => c.id === mainId)
    : current

  if (!main) return null

  const children = conversations.filter(c => c.parent_id === mainId)
  const mainActive = activeIds.has(mainId)

  return (
    <div className="current-session">
      <div className="current-session-header">
        <span className="current-session-label">{t('history.current')}</span>
        {mainActive && <span className="status-dot active" />}
      </div>
      <button
        className={`current-session-main${mainId === currentConversationId ? ' selected' : ''}`}
        onClick={() => onSelect(mainId)}
        title={main.title}
      >
        {main.title || t('history.untitled')}
      </button>

      {children.length > 0 && (
        <div className="current-session-children">
          {children.map(child => {
            const childActive = activeIds.has(child.id)
            const isSelected = child.id === currentConversationId
            return (
              <button
                key={child.id}
                className={`current-session-child${childActive ? ' active' : ''}${isSelected ? ' selected' : ''}`}
                onClick={() => onSelect(child.id)}
                title={child.title}
              >
                <span className="subagent-prefix">↳</span>
                <span className="current-session-child-title">
                  {child.title || t('history.subagent')}
                </span>
                {childActive && <span className="status-dot active" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── Full history drawer (opens as a second panel) ──────────────────────
function HistoryDrawer({ conversations, activeIds, currentConversationId, onSelect, onClose, triggerRef, t }) {
  const [searchQuery, setSearchQuery] = useState('')
  const searchRef = useRef(null)
  const panelRef = useRef(null)

  useEffect(() => {
    searchRef.current?.focus()
  }, [])

  useEffect(() => {
    function handleClick(e) {
      if (triggerRef?.current?.contains(e.target)) return
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        onClose()
      }
    }
    const timer = setTimeout(() => document.addEventListener('mousedown', handleClick), 0)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handleClick)
    }
  }, [onClose])

  useEffect(() => {
    function handleKey(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [onClose])

  const grouped = groupConversations(conversations)

  const filtered = searchQuery.trim()
    ? grouped.filter(c => (c.title || '').toLowerCase().includes(searchQuery.toLowerCase()))
    : grouped

  return (
    <div className="history-drawer" ref={panelRef}>
      <div className="history-drawer-header">
        <h3>{t('history.history')}</h3>
        <button className="history-drawer-close" onClick={onClose}>
          <X size={16} />
        </button>
      </div>

      <div className="history-drawer-search">
        <Search size={14} />
        <input
          ref={searchRef}
          type="text"
          placeholder={t('history.search')}
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
        />
      </div>

      <div className="history-drawer-list">
        {filtered.length === 0 ? (
          <div className="history-drawer-empty">
            {searchQuery ? t('history.noMatches') : t('history.noConversations')}
          </div>
        ) : (
          filtered.map(conv => {
            const isActive = activeIds.has(conv.id)
            const isCurrent = conv.id === currentConversationId
            const dotClass = statusDotClass(conv.status, isActive)
            const isSubagent = !!conv.parent_id

            return (
              <button
                key={conv.id}
                className={`history-drawer-item${isCurrent ? ' current' : ''}${isSubagent ? ' subagent' : ''}`}
                onClick={() => { onSelect(conv.id); onClose() }}
              >
                {dotClass && <span className={dotClass} />}
                <div className="history-drawer-item-body">
                  <span className="history-drawer-item-title">
                    {isSubagent && <span className="subagent-prefix">↳ </span>}
                    {conv.title || t('history.untitled')}
                  </span>
                  <span className="history-drawer-item-meta">
                    {isActive && <span className="history-drawer-item-status">{t('history.running')}</span>}
                    <span>{relativeTime(conv.updated_at, t)}</span>
                  </span>
                </div>
              </button>
            )
          })
        )}
      </div>
    </div>
  )
}

// ─── Exported wrapper ───────────────────────────────────────────────────
export default function ConversationHistory({ currentConversationId, onSelect, refreshTrigger, onDrawerToggle, closeTrigger }) {
  const { t } = useLanguage()
  const [conversations, setConversations] = useState([])
  const [activeIds, setActiveIds] = useState(new Set())
  const [loading, setLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const triggerRef = useRef(null)

  const loadTimerRef = useRef(null)
  const loadAbortRef = useRef(null)

  useEffect(() => {
    if (loadTimerRef.current) clearTimeout(loadTimerRef.current)
    if (loadAbortRef.current) loadAbortRef.current.abort()

    loadTimerRef.current = setTimeout(() => {
      const controller = new AbortController()
      loadAbortRef.current = controller

      setLoading(true)
      Promise.all([
        listConversations(),
        getActiveStreams(),
      ]).then(([convResult, activeResult]) => {
        if (controller.signal.aborted) return
        setConversations(convResult.conversations || [])
        setActiveIds(new Set((activeResult.active || []).map(a => a.conversation_id)))
      }).catch(e => {
        if (controller.signal.aborted) return
        console.warn('[ConversationHistory] Failed to load:', e.message)
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    }, 400)

    return () => {
      clearTimeout(loadTimerRef.current)
      if (loadAbortRef.current) loadAbortRef.current.abort()
    }
  }, [refreshTrigger])

  useEffect(() => {
    setDrawerOpen(false)
  }, [closeTrigger])

  const mainCount = conversations.filter(c => !c.parent_id).length

  return (
    <>
      <CurrentSession
        currentConversationId={currentConversationId}
        conversations={conversations}
        activeIds={activeIds}
        onSelect={onSelect}
        t={t}
      />

      <button
        ref={triggerRef}
        className={`history-trigger${drawerOpen ? ' active' : ''}`}
        onClick={() => {
          const next = !drawerOpen
          setDrawerOpen(next)
          if (onDrawerToggle) onDrawerToggle(next)
        }}
      >
        <History size={16} />
        <span className="history-trigger-label">{t('history.allHistory')}</span>
        {mainCount > 0 && <span className="history-trigger-count">{mainCount}</span>}
      </button>

      {drawerOpen && (
        <HistoryDrawer
          conversations={conversations}
          activeIds={activeIds}
          currentConversationId={currentConversationId}
          onSelect={onSelect}
          onClose={() => setDrawerOpen(false)}
          triggerRef={triggerRef}
          t={t}
        />
      )}
    </>
  )
}
