import { STATUS } from '../constants'
import { isInterruptible } from '../utils/streamUtils'

/**
 * Plain factory (NOT a hook).  Called inside handler bodies so closures
 * over the enclosing handler work naturally (e.g. handleSend → onMessages
 * → handleSend for interrupt resend).
 */
export function createStreamCallbacks({
  setMessages, setRawMessages, setConversationId, setCanContinue,
  setIsStreaming, setHistoryRefreshTrigger, setSubagentChildIds,
  handleSend, handleLoadConversation,
  pendingInterruptRef, continuationNavigatedRef, abortControllerRef,
  withInterrupt = false,
  withRetry = false,
  overrides = {},
  onFirstSse = null,
  onStreamEnd = null,
  onInterruptFired = null,
  ensureAssistantTail = false,
}) {
  let _firstMessage = true
  const onMessages = (frontendMessages, _status, data) => {
    if (_firstMessage) { _firstMessage = false; onFirstSse?.() }
    if (ensureAssistantTail) {
      const tail = frontendMessages.length > 0 ? frontendMessages[frontendMessages.length - 1] : null
      setMessages(tail?.role === 'assistant' ? frontendMessages : [...frontendMessages, { role: 'assistant', content: '' }])
    } else {
      setMessages(frontendMessages)
    }
    if (data?.raw_messages) {
      setRawMessages(data.raw_messages)
      if (withInterrupt && pendingInterruptRef?.current && isInterruptible(data.raw_messages)) {
        const msg = pendingInterruptRef.current.message
        const raw = data.raw_messages
        pendingInterruptRef.current = null
        onInterruptFired?.()
        if (abortControllerRef?.current) abortControllerRef.current.abort()
        setTimeout(() => handleSend?.(raw, msg), 50)
      }
    }
    if (data?.conversation_id) setConversationId(data.conversation_id)
    if (data?.new_conversation_id && !continuationNavigatedRef?.current.has(data.new_conversation_id)) {
      continuationNavigatedRef?.current.add(data.new_conversation_id)
      setTimeout(() => handleLoadConversation?.(data.new_conversation_id), 500)
    }
  }

  const onDone = (data) => {
    setConversationId(data.conversation_id)
    setCanContinue(data.status === STATUS.MAX_ITERATIONS_REACHED)
    setIsStreaming(false)
    if (data.messages) setMessages(data.messages)
    if (data.raw_messages) setRawMessages(data.raw_messages)
    setHistoryRefreshTrigger(prev => prev + 1)
  }

  const onError = (error) => {
    if (withRetry) {
      const isTimeout = error.message?.toLowerCase().includes('timeout') ||
        error.type === 'TimeoutError' || error.message?.toLowerCase().includes('timed out') ||
        error.message?.toLowerCase().includes('504') || error.message?.toLowerCase().includes('gateway timeout')
      setMessages(prev => [...prev, {
        role: 'assistant', content: `Error: ${error.message}`,
        isError: true, isTimeout, canRetry: true
      }])
    }
    setIsStreaming(false)
    setHistoryRefreshTrigger(prev => prev + 1)
    onStreamEnd?.()
  }

  const onSubagentEvent = (evt) => {
    if (evt.child_id && evt.tool_call_id) {
      setSubagentChildIds(prev => ({ ...prev, [evt.tool_call_id]: evt.child_id }))
    } else if (evt.child_id) {
      setSubagentChildIds(prev => {
        const arr = prev._fallback || []
        return { ...prev, _fallback: arr.includes(evt.child_id) ? arr : [...arr, evt.child_id] }
      })
    }
    setHistoryRefreshTrigger(prev => prev + 1)
  }

  return {
    onMessages: overrides.onMessages || onMessages,
    onDone: overrides.onDone || onDone,
    onError: overrides.onError || onError,
    onSubagentEvent: overrides.onSubagentEvent || onSubagentEvent,
  }
}
