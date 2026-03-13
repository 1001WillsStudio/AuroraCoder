import React, { useState, useEffect } from 'react'
import { History, FolderOpen, Clock, Check, AlertCircle, X, RefreshCw } from 'lucide-react'
import { listSessions, loadSession, getCurrentSession } from '../services/api'

/**
 * Format a date string to a human-readable format
 */
function formatDate(dateString) {
  if (!dateString) return 'Unknown'
  try {
    const date = new Date(dateString)
    const now = new Date()
    const diffMs = now - date
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
    
    if (diffDays === 0) {
      // Today - show time
      return `Today at ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    } else if (diffDays === 1) {
      return 'Yesterday'
    } else if (diffDays < 7) {
      return `${diffDays} days ago`
    } else {
      return date.toLocaleDateString()
    }
  } catch {
    return dateString
  }
}

/**
 * Extract a friendly display name from the full session name
 */
function getDisplayName(sessionInfo) {
  const fullName = sessionInfo.session_name || ''
  // Format: "name_YYYYMMDD_HHMMSS_id" or "session_YYYYMMDD_HHMMSS_id"
  const parts = fullName.split('_')
  
  if (parts.length >= 4) {
    // Remove the timestamp parts and ID, keep the name
    const nameParts = parts.slice(0, -3)
    if (nameParts.length > 0 && nameParts[0] !== 'session') {
      return nameParts.join(' ')
    }
  }
  
  // Fallback: use session ID
  return `Session ${sessionInfo.session_id || 'Unknown'}`
}

function SessionPicker({ isOpen, onClose, onSessionLoaded, currentSessionId }) {
  const [sessions, setSessions] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [loadingSessionId, setLoadingSessionId] = useState(null)
  const [error, setError] = useState(null)

  // Fetch sessions when modal opens
  useEffect(() => {
    if (isOpen) {
      fetchSessions()
    }
  }, [isOpen])

  const fetchSessions = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const data = await listSessions(true) // Only loadable sessions
      setSessions(data.sessions || [])
    } catch (err) {
      console.error('Failed to fetch sessions:', err)
      setError('Failed to load sessions')
    } finally {
      setIsLoading(false)
    }
  }

  const handleLoadSession = async (sessionId) => {
    setLoadingSessionId(sessionId)
    setError(null)
    try {
      const result = await loadSession(sessionId)
      console.log('Session loaded:', result)
      onSessionLoaded?.(result.session)
      onClose()
    } catch (err) {
      console.error('Failed to load session:', err)
      setError(err.message || 'Failed to load session')
    } finally {
      setLoadingSessionId(null)
    }
  }

  if (!isOpen) return null

  return (
    <div className="session-picker-overlay" onClick={onClose}>
      <div className="session-picker-modal" onClick={e => e.stopPropagation()}>
        <div className="session-picker-header">
          <div className="session-picker-title">
            <History size={20} />
            <h2>Load Previous Session</h2>
          </div>
          <button className="session-picker-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        {error && (
          <div className="session-picker-error">
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        )}

        <div className="session-picker-content">
          {isLoading ? (
            <div className="session-picker-loading">
              <RefreshCw size={24} className="spinning" />
              <span>Loading sessions...</span>
            </div>
          ) : sessions.length === 0 ? (
            <div className="session-picker-empty">
              <FolderOpen size={48} />
              <p>No previous sessions found</p>
              <span>Sessions with active environments will appear here</span>
            </div>
          ) : (
            <div className="session-list">
              {sessions.map(session => (
                <button
                  key={session.session_id}
                  className={`session-item ${session.session_id === currentSessionId ? 'current' : ''}`}
                  onClick={() => handleLoadSession(session.session_id)}
                  disabled={loadingSessionId !== null || session.session_id === currentSessionId}
                >
                  <div className="session-item-icon">
                    <FolderOpen size={20} />
                  </div>
                  <div className="session-item-info">
                    <div className="session-item-name">
                      {getDisplayName(session)}
                      {session.session_id === currentSessionId && (
                        <span className="session-current-badge">
                          <Check size={12} /> Current
                        </span>
                      )}
                    </div>
                    <div className="session-item-meta">
                      <Clock size={12} />
                      <span>{formatDate(session.created_at)}</span>
                      <span className="session-item-id">ID: {session.session_id}</span>
                    </div>
                  </div>
                  {loadingSessionId === session.session_id && (
                    <div className="session-item-loading">
                      <RefreshCw size={16} className="spinning" />
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="session-picker-footer">
          <button className="session-picker-refresh" onClick={fetchSessions} disabled={isLoading}>
            <RefreshCw size={16} className={isLoading ? 'spinning' : ''} />
            Refresh
          </button>
          <p className="session-picker-hint">
            Only sessions with active conda environments can be loaded
          </p>
        </div>
      </div>
    </div>
  )
}

export default SessionPicker
