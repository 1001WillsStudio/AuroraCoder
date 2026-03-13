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
  const { onMessages, onDone, onError } = callbacks
  
  const requestBody = {
    message,
    conversation_id: conversationId || null,
    messages: existingMessages,  // Include raw messages for interrupt/resume
    provider: provider  // Model provider selection
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
              // Full message list update - pass full data including raw_messages for interrupt/resume
              onMessages?.(event.data.messages, event.data.status, event.data)
              break
              
            case 'done':
              // Done event also includes raw_messages for final state
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
    console.error('[streamChat] Error:', error)
    if (error.name === 'AbortError') {
      throw error
    }
    onError?.({ message: error.message, type: error.name })
    throw error
  }
}

/**
 * Continue a paused conversation
 */
export async function continueChat(conversationId, callbacks, signal) {
  return streamChat('', conversationId, callbacks, signal)
}

/**
 * Get conversation details
 */
export async function getConversation(conversationId) {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * List all conversations
 */
export async function listConversations() {
  const response = await fetch(`${API_BASE}/conversations`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * Delete a conversation
 */
export async function deleteConversation(conversationId) {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
    method: 'DELETE'
  })
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
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
// Workspace API (Docker mode)
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
 * Upload a zip file to the workspace
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

/**
 * Download the workspace as a zip file
 */
export async function exportWorkspace() {
  const response = await fetch(`${API_BASE}/workspace/export`)
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'workspace.zip'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/**
 * Reset the workspace (delete all files)
 */
export async function resetWorkspace() {
  const response = await fetch(`${API_BASE}/workspace`, { method: 'DELETE' })
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}
