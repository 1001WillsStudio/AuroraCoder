/**
 * API Service for ThinkWithTool
 *
 * Handles communication with the FastAPI backend, including SSE streaming.
 */
import { getAuthHeader, clearToken } from '../utils/auth.js';

const API_BASE = '/api'

// ============================================================================
// Task Instruction Persistence (server-side — follows the instance, not the port)
// ============================================================================

/** Fetch the persisted task instruction from the gateway. */
export async function getTaskInstruction() {
  const response = await fetch(`${API_BASE}/task-instruction`, { headers: _headers() })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

/** Save a task instruction to the server filesystem. */
export async function setTaskInstruction(instruction) {
  const response = await fetch(`${API_BASE}/task-instruction`, {
    method: 'PUT',
    headers: _headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ instruction }),
  })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

// ============================================================================
// Instance Identity (GPU vs Normal — so the frontend can label tabs)
// ============================================================================

/** Return { type: "normal" | "gpu" } so the UI can distinguish instances. */
export async function getInstanceInfo() {
  const response = await fetch(`${API_BASE}/instance-info`, { headers: _headers() })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

let _reqSeq = 0
function _wall() {
  const d = new Date()
  const pad = (n, len = 2) => String(n).padStart(len, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
}
function _tlog(label, seq, startMs) {
  const elapsed = startMs != null ? ` (+${(performance.now() - startMs).toFixed(1)}ms)` : ''
  console.log(`[timing][#${seq}] ${_wall()} | ${label}${elapsed}`)
}

/** Build headers including auth token when available */
function _headers(extra = {}) {
  const h = { ...extra };
  const auth = getAuthHeader();
  if (auth) h['Authorization'] = auth;
  return h;
}

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
export async function streamChat(message, conversationId, callbacks, signal, existingMessages = null, provider = null, options = {}) {
  const seq = ++_reqSeq
  const t0 = performance.now()
  _tlog('streamChat() called', seq)

  const { onMessages, onDone, onError, onSubagentEvent, onDelta } = callbacks
  let _lastSeq = 0
  let _needsFullRefresh = false
  
  const requestBody = {
    message,
    conversation_id: conversationId || null,
    messages: existingMessages,
    provider: provider,
    ...options
  }

  _tlog('request body built, JSON.stringify next', seq, t0)
  const bodyJson = JSON.stringify(requestBody)
  _tlog(`JSON.stringify done (${(bodyJson.length / 1024).toFixed(1)}KB)`, seq, t0)

  try {
    _tlog('fetch() about to fire', seq, t0)
    const response = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: _headers({ 'Content-Type': 'application/json' }),
      body: bodyJson,
      signal
    })
    _tlog(`fetch() resolved, status=${response.status}`, seq, t0)
    console.log('[streamChat] Response status:', response.status)
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let firstChunk = true

    while (true) {
      const { done, value } = await reader.read()
      
      if (done) {
        _tlog('stream done', seq, t0)
        console.log('[streamChat] Stream done')
        break
      }

      if (firstChunk) {
        _tlog('first SSE chunk received', seq, t0)
        firstChunk = false
      }
      
      buffer += decoder.decode(value, { stream: true })
      
      // Split on double newlines (SSE event separator)
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      
      for (const part of parts) {
        if (!part.trim()) continue
        
        const events = parseSSEEvents(part)
        
        for (const event of events) {
          if (event.type !== 'messages') {
            console.log('[streamChat] Event:', event.type)
          }
          
          switch (event.type) {
            case 'delta':
              if (event.data.seq && event.data.seq !== _lastSeq + 1) {
                _needsFullRefresh = true;
              }
              _lastSeq = event.data.seq || _lastSeq;
              if (!_needsFullRefresh) {
                onDelta?.(event.data.delta, event.data.status);
              }
              break;

            case 'messages':
              _lastSeq = event.data.seq || _lastSeq;
              _needsFullRefresh = false;
              onMessages?.(event.data.messages, event.data.status, event.data);
              break;

            case 'done':
              onDone?.(event.data);
              break;

            case 'error':
              onError?.(event.data);
              break;

            case 'subagent_event':
              onSubagentEvent?.(event.data);
              break;
          }
        }
      }
    }
    
  } catch (error) {
    _tlog(`error: ${error.name} — ${error.message}`, seq, t0)
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
  const response = await fetch(`${API_BASE}/health`, { headers: _headers() })
  return response.json()
}

/**
 * Get available model providers
 */
export async function getProviders() {
  const response = await fetch(`${API_BASE}/providers`, { headers: _headers() })
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}
/**
 * Get user settings (API keys masked).
 */
export async function getSettings() {
  const response = await fetch(`${API_BASE}/settings`, { headers: _headers() })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

/**
 * Update user settings. Persisted to /app/data/settings.json.
 */
export async function updateSettings(payload) {
  const response = await fetch(`${API_BASE}/settings`, {
    method: 'PUT',
    headers: _headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

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
      headers: _headers(),
    })
    if (!response.ok) throw new Error(`Cancel error! status: ${response.status}`)
    return response.json()
  } catch (error) {
    console.warn('[cancelConversation] Error:', error.message)
    return null
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

  const response = await fetch(`${API_BASE}/conversations?${params}`, { headers: _headers() })
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
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`, { headers: _headers() })
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * List currently active (streaming) conversations.
 */
export async function getActiveStreams() {
  const t0 = performance.now()
  console.log(`[timing] getActiveStreams() called at ${_wall()}`)
  const response = await fetch(`${API_BASE}/conversations/active`, { headers: _headers() })
  console.log(`[timing] getActiveStreams() responded in ${(performance.now() - t0).toFixed(1)}ms`)
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
  const { onMessages, onDone, onError, onSubagentEvent, onDelta } = callbacks

  try {
    const response = await fetch(`${API_BASE}/conversations/${conversationId}/stream`, { signal, headers: _headers() })

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
            case 'delta':
              onDelta?.(event.data.delta, event.data.status);
              break;

            case 'messages':
              onMessages?.(event.data.messages, event.data.status, event.data);
              break;

            case 'done':
              onDone?.(event.data);
              break;

            case 'error':
              onError?.(event.data);
              break;

            case 'subagent_event':
              onSubagentEvent?.(event.data);
              break;
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
// Workspace API
// ============================================================================

/**
 * Get workspace info (docker mode, path, file count)
 */
export async function getWorkspaceInfo() {
  const response = await fetch(`${API_BASE}/workspace/info`, { headers: _headers() })
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

/**
 * Upload a folder to the workspace (the user selects a folder via webkitdirectory).
 *
 * The entire folder is compressed into a single zip archive client-side, then
 * sent as one file.  This avoids Starlette's per-request multipart limits
 * (max_files, max_part_size) and drastically cuts upload size/time.
 *
 * Respects .gitignore: if the selected folder contains a .gitignore,
 * matched paths are excluded.  .git/ is always included so the agent
 * can use git in the workspace.
 *
 * Multiple projects can be uploaded — each lands in its own subfolder
 * under the workspace (named after the selected folder).
 *
 * @param {FileList} fileList - Files from a webkitdirectory input
 */
export async function uploadWorkspace(fileList) {
  const [{ default: JSZip }, { default: ignore }] = await Promise.all([
    import('jszip'),
    import('ignore'),
  ])

  const files = Array.from(fileList)

  // Derive the project folder name from the first file's path
  // webkitRelativePath is "FolderName/sub/path/file.ext"
  const projectName = (files[0]?.webkitRelativePath || '').split('/')[0] || 'project'

  // Find root .gitignore
  let gitignoreContent = ''
  for (const file of files) {
    const parts = (file.webkitRelativePath || file.name).split('/')
    if (parts.length === 2 && parts[1] === '.gitignore') {
      gitignoreContent = await file.text()
      break
    }
  }

  const ig = ignore()
  if (gitignoreContent) ig.add(gitignoreContent)

  // Build zip — read files in parallel batches for speed
  const zip = new JSZip()
  const BATCH = 200
  const toZip = []

  for (const file of files) {
    const rel = file.webkitRelativePath || file.name
    const inProject = rel.split('/').slice(1).join('/')
    if (!inProject) continue

    // .git/ is always included so the agent has full git history
    if (!inProject.startsWith('.git/') && inProject !== '.git') {
      if (ig.ignores(inProject)) continue
    }

    toZip.push({ inProject, file })
  }

  for (let i = 0; i < toZip.length; i += BATCH) {
    const batch = toZip.slice(i, i + BATCH)
    const buffers = await Promise.all(batch.map(({ file }) => file.arrayBuffer()))
    for (let j = 0; j < batch.length; j++) {
      zip.file(batch[j].inProject, buffers[j])
    }
  }

  const blob = await zip.generateAsync({ type: 'blob', compression: 'DEFLATE', compressionOptions: { level: 1 } })

  const formData = new FormData()
  formData.append('project_name', projectName)
  formData.append('archive', blob, 'workspace.zip')

  const response = await fetch(`${API_BASE}/workspace/upload`, {
    method: 'POST',
    headers: _headers(),
    body: formData,
  })
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Upload failed' }))
    throw new Error(err.detail || `HTTP error! status: ${response.status}`)
  }
    return response.json()
}

// ============================================================================
// ToolStore API
// ============================================================================

/**
 * Get ToolStore status — tool counts and per-source breakdown.
 */
export async function getToolStoreStatus() {
  const response = await fetch(`${API_BASE}/toolstore/status`, { headers: _headers() })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

/**
 * Trigger a tool index refresh (`toolstore update`).
 */
export async function refreshToolStore() {
  const response = await fetch(`${API_BASE}/toolstore/refresh`, {
    method: 'POST',
    headers: _headers(),
  })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}
