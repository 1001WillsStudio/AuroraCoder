/**
 * API Service for ThinkWithTool
 * 
 * Handles communication with the FastAPI backend, including SSE streaming.
 */

const API_BASE = '/api'

/**
 * Parse SSE events from a text chunk
 */
function parseSSEEvents(text) {
  const events = []
  const lines = text.split('\n')
  
  let currentEvent = null
  
  for (const line of lines) {
    if (line.startsWith('event:')) {
      currentEvent = { type: line.slice(6).trim(), data: null }
    } else if (line.startsWith('data:')) {
      try {
        const data = JSON.parse(line.slice(5).trim())
        if (currentEvent) {
          currentEvent.data = data
          events.push(currentEvent)
          currentEvent = null
        } else {
          events.push({ type: 'message', data })
        }
      } catch (e) {
        // Ignore parse errors for partial data
      }
    }
  }
  
  return events
}

/**
 * Stream chat responses from the API
 * 
 * @param {string} message - User message
 * @param {string|null} conversationId - Optional conversation ID to continue
 * @param {object} callbacks - Event callbacks
 * @param {AbortSignal} signal - Optional abort signal
 * @param {array|null} existingMessages - Optional: existing raw messages to continue from (for interrupt/resume)
 * @param {string|null} provider - Optional: model provider to use
 */
export async function streamChat(message, conversationId, callbacks, signal, existingMessages = null, provider = null) {
  const { onMessages, onDone, onError, onSubagentEvent } = callbacks
  
  const requestBody = {
    message,
    conversation_id: conversationId || null,
    messages: existingMessages,
    provider: provider
  }
  console.log('[streamChat] Request:', JSON.stringify(requestBody))
  
  try {
    const response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(requestBody),
      signal
    })

    console.log('[streamChat] Response status:', response.status)
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      
      if (done) {
        console.log('[streamChat] Stream done')
        break
      }
      
      buffer += decoder.decode(value, { stream: true })
      
      // Split on double newlines (SSE event separator)
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      
      for (const part of parts) {
        if (!part.trim()) continue
        
        const events = parseSSEEvents(part)
        
        for (const event of events) {
          console.log('[streamChat] Event:', event.type, 
            event.type === 'messages' ? `(${event.data?.messages?.length} msgs)` : '')
          
          switch (event.type) {
            case 'messages':
              onMessages?.(event.data.messages, event.data.status, event.data)
              break
              
            case 'done':
              onDone?.(event.data)
              break
              
            case 'error':
              onError?.(event.data)
              break

            case 'subagent_event':
              onSubagentEvent?.(event.data)
              break
          }
        }
      }
    }
    
  } catch (error) {
    console.error('[streamChat] Error:', error)
    if (error.name === 'AbortError') {
      throw error
    }
    onError?.({ message: error.message, type: error.name })
    throw error
  }
}

/**
 * Health check
 */
export async function healthCheck() {
  const response = await fetch(`${API_BASE}/health`)
  return response.json()
}

/**
 * Get available model providers
 */
export async function getProviders() {
  const response = await fetch(`${API_BASE}/providers`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

// ============================================================================
// Conversation History API (served by the conversation server)
// ============================================================================

/**
 * Cancel an active stream on the conversation server.
 * This actually stops the backend generation (not just the frontend connection).
 * @param {string} conversationId
 */
export async function cancelConversation(conversationId) {
  try {
    const response = await fetch(`${API_BASE}/conversations/${conversationId}/cancel`, {
      method: 'POST',
    })
    if (!response.ok && response.status !== 404) {
      console.warn('[cancelConversation] Failed:', response.status)
    }
  } catch (error) {
    console.warn('[cancelConversation] Error:', error.message)
  }
}

/**
 * List past conversations (metadata only).
 * @param {object} filters - Optional { type, session_id, parent_id }
 */
export async function listConversations(filters = {}) {
  const params = new URLSearchParams()
  if (filters.type) params.append('type', filters.type)
  if (filters.session_id) params.append('session_id', filters.session_id)
  if (filters.parent_id) params.append('parent_id', filters.parent_id)

  const response = await fetch(`${API_BASE}/conversations?${params}`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * Get a full conversation (metadata + messages).
 * @param {string} conversationId
 */
export async function getConversation(conversationId) {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * List currently active (streaming) conversations.
 */
export async function getActiveStreams() {
  const response = await fetch(`${API_BASE}/conversations/active`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * Attach to an in-progress stream for mid-stream resume.
 * Returns the same SSE format as streamChat.
 * @param {string} conversationId
 * @param {object} callbacks - { onMessages, onDone, onError }
 * @param {AbortSignal} signal
 */
export async function resumeStream(conversationId, callbacks, signal) {
  const { onMessages, onDone, onError } = callbacks

  try {
    const response = await fetch(`${API_BASE}/conversations/${conversationId}/stream`, { signal })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''

      for (const part of parts) {
        if (!part.trim()) continue
        const events = parseSSEEvents(part)
        for (const event of events) {
          switch (event.type) {
            case 'messages':
              onMessages?.(event.data.messages, event.data.status, event.data)
              break
            case 'done':
              onDone?.(event.data)
              break
            case 'error':
              onError?.(event.data)
              break
          }
        }
      }
    }
  } catch (error) {
    if (error.name === 'AbortError') throw error
    onError?.({ message: error.message, type: error.name })
    throw error
  }
}


// ============================================================================
// Session Management API
// ============================================================================

/**
 * List available sessions
 * @param {boolean} loadableOnly - If true, only return sessions that can be loaded
 */
export async function listSessions(loadableOnly = true) {
  const response = await fetch(`${API_BASE}/sessions?loadable_only=${loadableOnly}`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * Load a previous session
 * @param {string} sessionId - Session ID to load
 * @param {string} sessionName - Session name to load (alternative to sessionId)
 */
export async function loadSession(sessionId = null, sessionName = null) {
  const response = await fetch(`${API_BASE}/sessions/load`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: sessionId,
      session_name: sessionName
    })
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to load session' }))
    throw new Error(error.detail || `HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * Get current session info
 */
export async function getCurrentSession() {
  const response = await fetch(`${API_BASE}/sessions/current`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * Create a new session
 * @param {string} sessionName - Optional name for the session
 */
export async function createNewSession(sessionName = null) {
  const params = new URLSearchParams()
  if (sessionName) params.append('session_name', sessionName)
  
  const response = await fetch(`${API_BASE}/sessions/new?${params}`, {
    method: 'POST'
  })
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

// ============================================================================
// Workspace API
// ============================================================================

/**
 * Get workspace info (docker mode, path, file count)
 */
export async function getWorkspaceInfo() {
  const response = await fetch(`${API_BASE}/workspace/info`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * Upload a zip file to the workspace (for loading local code repos into Docker)
 * @param {File} zipFile - The zip file to upload
 * @param {boolean} clear - Clear existing workspace before extracting
 */
export async function uploadWorkspace(zipFile, clear = true) {
  const url = `${API_BASE}/workspace/upload?clear=${clear}`
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/zip' },
    body: zipFile,
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Upload failed' }))
    throw new Error(err.detail || `HTTP error! status: ${response.status}`)
  }
  return response.json()
}
