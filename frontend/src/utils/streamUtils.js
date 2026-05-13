// Debug: log message structure
const DEBUG = true

// Task instruction markers — wrap the instruction so the backend can strip it
// for conversation titles. Must match conversation_history/conversation_store.py.
export const TASK_MARKER_START = "[TASK INSTRUCTION]"
export const TASK_MARKER_END = "[/TASK INSTRUCTION]"

// Code-related tool names that trigger the code panel (only create/edit, not read)
export const CODE_TOOLS = ['write_file', 'edit_file']

// Tools that modify the file system and should trigger a file tree refresh
export const FILE_SYSTEM_TOOLS = [
  'write_file',           // Creates or overwrites files
  'edit_file',            // Edits existing files  
  'delete_file',          // Deletes files
  'run_terminal_command'  // Terminal commands may create/modify files
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
  if (!messages || messages.length === 0) {
    console.log('[isInterruptible] No messages, safe to interrupt')
    return true
  }
  
  // Check the last message to understand current state
  const lastMessage = messages[messages.length - 1]
  console.log('[isInterruptible] Last message role:', lastMessage.role)
  
  // If the last message is a tool response, we just completed a tool call - SAFE
  if (lastMessage.role === 'tool') {
    // But we need to check if ALL tool calls from the preceding assistant have responses
    // Find the assistant message that made these tool calls
    let assistantIndex = -1
    for (let i = messages.length - 2; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].tool_calls?.length > 0) {
        assistantIndex = i
        break
      }
    }
    
    if (assistantIndex === -1) {
      console.log('[isInterruptible] Tool response but no assistant with tool_calls found, safe')
      return true
    }
    
    const assistant = messages[assistantIndex]
    const expectedIds = new Set(assistant.tool_calls.filter(tc => tc.id).map(tc => tc.id))
    const receivedIds = new Set()
    
    for (let i = assistantIndex + 1; i < messages.length; i++) {
      if (messages[i].role === 'tool' && messages[i].tool_call_id) {
        receivedIds.add(messages[i].tool_call_id)
      }
    }
    
    const allReceived = [...expectedIds].every(id => receivedIds.has(id))
    console.log('[isInterruptible] Tool responses - expected:', expectedIds.size, 'received:', receivedIds.size, 'allReceived:', allReceived)
    return allReceived
  }
  
  // If the last message is an assistant message
  if (lastMessage.role === 'assistant') {
    // Check if it has tool_calls with valid IDs
    const toolCalls = lastMessage.tool_calls || []
    const validToolCalls = toolCalls.filter(tc => tc.id && tc.id.length > 0)
    
    console.log('[isInterruptible] Assistant message - total tool_calls:', toolCalls.length, 'with valid IDs:', validToolCalls.length)
    
    // If no valid tool call IDs, model is still generating content/reasoning or tool call is being built - SAFE
    // (We'll catch it once the tool call ID arrives)
    if (validToolCalls.length === 0) {
      console.log('[isInterruptible] No valid tool call IDs, safe to interrupt (generating content/reasoning)')
      return true
    }
    
    // Has valid tool calls but no tool responses yet - NOT SAFE
    console.log('[isInterruptible] Has tool calls with IDs but no responses yet, NOT safe')
    return false
  }
  
  // If last message is user or system, safe to interrupt
  console.log('[isInterruptible] Last message is', lastMessage.role, ', safe to interrupt')
  return true
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
