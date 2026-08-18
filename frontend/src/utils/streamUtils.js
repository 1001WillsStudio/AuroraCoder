// Task instruction markers — wrap the instruction so the backend can strip it
// for conversation titles. Must match conversation_history/conversation_store.py.
export const TASK_MARKER_START = "[TASK INSTRUCTION]"
export const TASK_MARKER_END = "[/TASK INSTRUCTION]"

// Code-related tool names that trigger the code panel (only create/edit, not read)
export const CODE_TOOLS = ['write_file', 'edit_file']

// Tools whose completion can change the file tree (files or folders appear/disappear).
// ── edit_file is purposely NOT included ── it only modifies file content, not the tree.
// ── run_terminal_command IS included because commands like git clone, mkdir, pip install
//    (and many others) can create or delete files.  The debounce in FileTree.jsx prevents
//    the storm of refreshes that used to happen when every shell command fired a tree fetch.
export const FILE_SYSTEM_TOOLS = [
  'write_file',           // Creates (or overwrites) files
  'delete_file',          // Deletes files
  'run_terminal_command', // Shell commands that may create / delete files
]

/**
 * Check if the raw message list is in a safe state to interrupt.
 * 
 * Safe to interrupt when:
 * - Model is generating reasoning/content (no tool_calls, or tool_calls with no valid IDs yet)
 * - All tool calls have their corresponding tool results
 * 
 * NOT safe to interrupt when:
 * - Model has requested tool calls (valid IDs present) but tool responses haven't all arrived
 * 
 * @param {array} messages - Raw backend format messages
 * @returns {boolean} - True if safe to interrupt
 */
export function isInterruptible(messages) {
  if (!messages || messages.length === 0) return true

  const lastMessage = messages[messages.length - 1]

  if (lastMessage.role === 'tool') {
    let assistantIndex = -1
    for (let i = messages.length - 2; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].tool_calls?.length > 0) {
        assistantIndex = i
        break
      }
    }

    if (assistantIndex === -1) return true

    const assistant = messages[assistantIndex]
    const expectedIds = new Set(assistant.tool_calls.filter(tc => tc.id).map(tc => tc.id))
    const receivedIds = new Set()

    for (let i = assistantIndex + 1; i < messages.length; i++) {
      if (messages[i].role === 'tool' && messages[i].tool_call_id) {
        receivedIds.add(messages[i].tool_call_id)
      }
    }

    return [...expectedIds].every(id => receivedIds.has(id))
  }

  if (lastMessage.role === 'assistant') {
    const toolCalls = lastMessage.tool_calls || []
    const validToolCalls = toolCalls.filter(tc => tc.id && tc.id.length > 0)
    if (validToolCalls.length === 0) return true
    return false
  }

  return true
}

/**
 * Allocate a conversation id without assuming ``crypto.randomUUID``.
 * Some browsers expose ``crypto`` but not ``randomUUID``; calling it
 * on first send aborted the request before the user bubble appeared.
 */
export function newConversationId(webCrypto) {
  const c = webCrypto !== undefined
    ? webCrypto
    : (typeof crypto !== 'undefined' ? crypto : undefined)
  try {
    if (c && typeof c.randomUUID === 'function') {
      return c.randomUUID()
    }
  } catch { /* fall through */ }
  const bytes = new Uint8Array(16)
  if (c && typeof c.getRandomValues === 'function') {
    c.getRandomValues(bytes)
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map(b => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/**
 * Format elapsed seconds into a human-readable string.
 * @param {number} seconds
 * @returns {string}
 */
export function formatElapsedTime(seconds) {
  if (seconds < 60) return `${seconds} second${seconds !== 1 ? 's' : ''}`
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  if (secs === 0) return `${mins} minute${mins !== 1 ? 's' : ''}`
  return `${mins} minute${mins !== 1 ? 's' : ''} ${secs} second${secs !== 1 ? 's' : ''}`
}
