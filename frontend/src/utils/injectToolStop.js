/**
 * Pure helpers for injecting tool-stop termination data.
 *
 * Extracted from App.jsx handleStopTool — these are pure data transformations
 * with no side effects, making them trivially testable.
 */

/**
 * Append synthetic tool responses for any orphan tool_calls in rawMessages
 * that don't already have a matching tool response.
 */
export function injectOrphanToolStops(rawMessages, terminationMessage) {
  const found = [...rawMessages]
  let lastAssistantIdx = -1
  for (let i = found.length - 1; i >= 0; i--) {
    if (found[i].role === 'assistant' && found[i].tool_calls?.length > 0) {
      lastAssistantIdx = i
      break
    }
  }
  if (lastAssistantIdx === -1) return rawMessages

  const toolCalls = found[lastAssistantIdx].tool_calls || []
  const existingIds = new Set()
  for (let i = lastAssistantIdx + 1; i < found.length; i++) {
    if (found[i].role === 'tool' && found[i].tool_call_id) {
      existingIds.add(found[i].tool_call_id)
    }
  }
  for (const tc of toolCalls) {
    if (tc.id && !existingIds.has(tc.id)) {
      found.push({ role: 'tool', tool_call_id: tc.id, content: terminationMessage })
    }
  }
  return found
}

/**
 * Append a termination activity and note to the last assistant message
 * in the frontend messages array.
 */
export function injectToolStopActivity(messages, toolCallId, content, note) {
  const found = [...messages]
  const lastIdx = found.length - 1
  if (lastIdx >= 0 && found[lastIdx].role === 'assistant') {
    const lastMsg = { ...found[lastIdx] }
    const activities = [...(lastMsg.activities || [])]
    activities.push({
      type: 'tool_result',
      tool_call_id: toolCallId,
      content,
      isTerminated: true,
    })
    lastMsg.activities = activities
    lastMsg.content = (lastMsg.content || '') + note
    found[lastIdx] = lastMsg
  }
  return found
}
