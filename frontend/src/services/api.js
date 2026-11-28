/**
 * API Service for ThinkWithTool
 *
 * Handles communication with the FastAPI backend, including SSE streaming.
 */
import { getAuthHeader, clearToken } from '../utils/auth.js';
import { newConversationId } from '../utils/uuid.js';

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

class IncompleteStreamError extends Error {
  constructor(cause) {
    super('The connection ended before AuroraCoder reported the result.', { cause })
    this.name = 'IncompleteStreamError'
  }
}

async function readConversationEvents(response, callbacks) {
  if (!response.body) throw new IncompleteStreamError()
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastSeq = 0
  let needsFullRefresh = false
  try {
    while (true) {
      let chunk
      try {
        chunk = await reader.read()
      } catch (error) {
        if (error.name === 'AbortError') throw error
        throw new IncompleteStreamError(error)
      }
      if (chunk.done) throw new IncompleteStreamError()
      buffer += decoder.decode(chunk.value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        for (const { type, data } of parseSSEEvents(part)) {
          switch (type) {
            case 'delta':
              if (data.seq && data.seq !== lastSeq + 1) needsFullRefresh = true
              lastSeq = data.seq || lastSeq
              if (!needsFullRefresh) callbacks.onDelta?.(data.delta, data.status)
              break
            case 'messages':
              lastSeq = data.seq || lastSeq
              needsFullRefresh = false
              callbacks.onMessages?.(data.messages, data.status, data)
              break
            case 'subagent_event':
              callbacks.onSubagentEvent?.(data)
              break
            case 'done':
            case 'error':
              if (type === 'done') callbacks.onDone?.(data)
              else callbacks.onError?.(data)
              await reader.cancel().catch(() => {})
              return
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

async function attachConversationStream(conversationId, callbacks, signal, { turnId, confirm = false } = {}) {
  const suffix = turnId ? `?turn_id=${encodeURIComponent(turnId)}` : ''
  const response = await fetch(`${API_BASE}/conversations/${encodeURIComponent(conversationId)}/stream${suffix}`, {
    signal, headers: _headers(),
  })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  if (confirm) callbacks.onConversationId?.(response.headers.get('X-Conversation-ID') || conversationId)
  await readConversationEvents(response, callbacks)
}

function canReattach(error, conversationId, signal) {
  return error instanceof IncompleteStreamError && conversationId && !signal?.aborted
}

/**
 * Stream chat responses from the API
 * 
 * @param {string} message - User message
 * @param {string|null} conversationId - Optional conversation ID to continue
 * @param {object} callbacks - Event callbacks
 * @param {AbortSignal} signal - Optional abort signal
 * @param {array|null} existingMessages - Optional raw history for a follow-up or resume
 * @param {string|null} provider - Optional: model provider to use
 */
export async function streamChat(message, conversationId, callbacks, signal, existingMessages = null, provider = null, options = {}) {
  const seq = ++_reqSeq
  const t0 = performance.now()
  _tlog('streamChat() called', seq)

  const { onError, onConversationId } = callbacks
  const { turnId = newConversationId(), ...requestOptions } = options
  
  const requestBody = {
    message,
    conversation_id: conversationId || newConversationId(),
    messages: existingMessages,
    prefer_saved_history: true,
    wait_for_current_turn: true,
    provider: provider,
    provider_id: options.provider_id || null,
    model: options.model || null,
    ...requestOptions
  }

  _tlog('request body built, JSON.stringify next', seq, t0)
  const bodyJson = JSON.stringify(requestBody)
  _tlog(`JSON.stringify done (${(bodyJson.length / 1024).toFixed(1)}KB)`, seq, t0)

  const recoverBeforeHeaders = async error => {
    if (signal?.aborted) throw error
    try {
      await attachConversationStream(requestBody.conversation_id, callbacks, signal, { turnId, confirm: true })
    } catch (reconnectError) {
      throw signal?.aborted ? reconnectError : error
    }
  }

  try {
    _tlog('fetch() about to fire', seq, t0)
    let response
    try {
      response = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: _headers({ 'Content-Type': 'application/json', 'X-Aurora-Turn-ID': turnId }),
        body: bodyJson,
        signal,
      })
    } catch (error) {
      if (!(error instanceof TypeError)) throw error
      await recoverBeforeHeaders(error)
      return
    }
    _tlog(`fetch() resolved, status=${response.status}`, seq, t0)
    console.log('[streamChat] Response status:', response.status)
    const headerCid = response.headers.get('X-Conversation-ID')
    if (headerCid) onConversationId?.(headerCid)
    
    if (!response.ok) {
      if (response.status === 409 && requestOptions.retry_receipt) {
        const result = await response.json().catch(() => ({}))
        if (typeof result.detail === 'string') throw new Error(result.detail)
      }
      const error = new Error(`HTTP error! status: ${response.status}`)
      if (response.status !== 502 && response.status !== 504) throw error
      await recoverBeforeHeaders(error)
      return
    }

    try {
      await readConversationEvents(response, callbacks)
    } catch (error) {
      const acceptedId = headerCid || requestBody.conversation_id
      if (!canReattach(error, acceptedId, signal)) throw error
      await attachConversationStream(acceptedId, callbacks, signal, { turnId, confirm: !headerCid })
    }
    
  } catch (error) {
    _tlog(`error: ${error.name} — ${error.message}`, seq, t0)
    console.error('[streamChat] Error:', error)
    if (error.name === 'AbortError' || signal?.aborted) {
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
export async function searchConversationHistory(query, signal) {
  const params = new URLSearchParams({ query })
  const response = await fetch(`${API_BASE}/conversation-browser/search?${params}`, { headers: _headers(), signal })
  if (!response.ok) throw new Error(`History search failed (${response.status})`)
  return response.json()
}

export async function forkConversation(id, beforeUserMessage) {
  const response = await fetch(`${API_BASE}/conversations/${encodeURIComponent(id)}/fork`, {
    method: 'POST',
    headers: _headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ before_user_message: beforeUserMessage }),
  })
  if (!response.ok) throw new Error(`Could not fork the conversation (${response.status})`)
  return response.json()
}

export async function getConversation(conversationId, signal) {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`, { headers: _headers(), signal })
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }
  return response.json()
}

export async function readTask(conversationId, signal) {
  const response = await fetch(`${API_BASE}/conversations/${encodeURIComponent(conversationId)}/task`, {
    signal, headers: _headers(),
  })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

export async function submitTaskSteering(conversationId, { receipt, message, attachedFiles }) {
  const response = await fetch(`${API_BASE}/conversations/${encodeURIComponent(conversationId)}/task/steering`, {
    method: 'POST',
    headers: _headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ receipt, message, attached_files: attachedFiles }),
  })
  const result = await response.json().catch(() => null)
  if (response.ok && result?.receipt === receipt) return 'accepted'
  if (response.status === 409 && result?.detail?.code === 'no_active_goal') return 'no-goal'
  if (response.status === 400 || response.status === 422) return 'rejected'
  throw new Error('Could not confirm Goal steering receipt.')
}

/**
 * Delete a conversation and its descendant subagent chats.
 * @param {string} conversationId
 */
export async function deleteConversation(conversationId) {
  const response = await fetch(`${API_BASE}/conversations/${encodeURIComponent(conversationId)}`, {
    method: 'DELETE',
    headers: _headers(),
  })
  let data = {}
  try {
    data = await response.json()
  } catch {
    data = {}
  }
  if (!response.ok) {
    const detail = data.detail || `HTTP error! status: ${response.status}`
    throw new Error(typeof detail === 'string' ? detail : `HTTP error! status: ${response.status}`)
  }
  return data
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
  try {
    try {
      await attachConversationStream(conversationId, callbacks, signal)
    } catch (error) {
      if (!canReattach(error, conversationId, signal)) throw error
      await attachConversationStream(conversationId, callbacks, signal)
    }
  } catch (error) {
    if (error.name === 'AbortError' || signal?.aborted) throw error
    callbacks.onError?.({ message: error.message, type: error.name })
    throw error
  }
}


// ============================================================================
// Workspace API
// ============================================================================

/**
 * Find workspace files by name or path (not capped at tree depth).
 */
export async function searchWorkspaceFiles(query, limit = 24) {
  const params = new URLSearchParams({ q: query || '', limit: String(limit) })
  const response = await fetch(`${API_BASE}/files/search?${params}`, { headers: _headers() })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

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

async function _readErrorDetail(response, fallback) {
  const err = await response.json().catch(() => ({}))
  const detail = err.detail || err.error
  return typeof detail === 'string' && detail
    ? detail
    : (fallback || `HTTP error! status: ${response.status}`)
}

/**
 * Query string for GET /api/files/tree. First paint is max_depth=5;
 * a folder click uses path + max_depth=1.
 */
export function fileTreeUrl({ path, maxDepth } = {}) {
  const params = new URLSearchParams()
  if (path) params.set('path', path)
  if (maxDepth != null) params.set('max_depth', String(maxDepth))
  const qs = params.toString()
  return `${API_BASE}/files/tree${qs ? `?${qs}` : ''}`
}

/** List the workspace tree. Always sends the auth header when a token exists. */
export async function getFileTree(options = {}) {
  const response = await fetch(fileTreeUrl(options), { headers: _headers() })
  if (!response.ok) throw new Error(await _readErrorDetail(response, 'Failed to load file tree'))
  return response.json()
}

/** Read a workspace file for the code panel. */
export async function readWorkspaceFile(filePath) {
  const response = await fetch(
    `${API_BASE}/files/read?file_path=${encodeURIComponent(filePath)}`,
    { headers: _headers() },
  )
  if (!response.ok) throw new Error(await _readErrorDetail(response, 'Failed to read file'))
  return response.json()
}

/** Agent diffs for the conversation's touched files. */
export async function getFileDiffs(conversationId, signal) {
  const response = await fetch(
    `${API_BASE}/files/diff?conversation_id=${encodeURIComponent(conversationId)}`,
    { signal, headers: _headers() },
  )
  if (!response.ok) throw new Error(await _readErrorDetail(response, 'Failed to load file diffs'))
  return response.json()
}

/** Delete a file or folder in the workspace. */
export async function deleteWorkspacePath(path) {
  const response = await fetch(`${API_BASE}/files/delete`, {
    method: 'POST',
    headers: _headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ path }),
  })
  if (!response.ok) throw new Error(await _readErrorDetail(response, 'Failed to delete'))
  return response.json()
}

function triggerBrowserDownload(blob, filename) {
  const objectUrl = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = objectUrl
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

async function downloadAuthorizedFile(url, filename, fallback) {
  const response = await fetch(url, { headers: _headers() })
  if (!response.ok) throw new Error(await _readErrorDetail(response, fallback))
  triggerBrowserDownload(await response.blob(), filename)
}

/**
 * Download a workspace file. A raw ``<a href>`` cannot send the Bearer
 * token, so this fetches a blob with auth headers and saves it locally.
 */
export async function downloadWorkspaceFile(filePath, filename) {
  await downloadAuthorizedFile(
    `${API_BASE}/files/download?file_path=${encodeURIComponent(filePath)}`,
    filename || 'download',
    'Download failed',
  )
}

/** Export a workspace folder as a zip (same blob+auth path as download). */
export async function exportWorkspaceFolder(folderPath, filename) {
  await downloadAuthorizedFile(
    `${API_BASE}/files/export?folder_path=${encodeURIComponent(folderPath)}`,
    filename || 'folder.zip',
    'Export failed',
  )
}

/** Discover models for a provider. Key is resolved server-side. */
export async function discoverProviderModels(providerId) {
  const response = await fetch(
    `${API_BASE}/discover-models?provider_id=${encodeURIComponent(providerId)}`,
    { headers: _headers() },
  )
  if (!response.ok) throw new Error(await _readErrorDetail(response, `HTTP ${response.status}`))
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

// ============================================================================
// Memory Browser (Settings panel)
// ============================================================================

/** List stored memories (full content) — works even while memory is disabled. */
export async function getMemories() {
  const response = await fetch(`${API_BASE}/memory`, { headers: _headers() })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}

/** Permanently delete one memory by id. */
export async function deleteMemory(memoryId) {
  const response = await fetch(`${API_BASE}/memory/${encodeURIComponent(memoryId)}`, {
    method: 'DELETE',
    headers: _headers(),
  })
  if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`)
  return response.json()
}
