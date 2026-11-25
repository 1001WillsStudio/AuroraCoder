import { STATUS } from '../constants'
import { hasCompleteToolBatch } from '../utils/streamUtils'

/**
 * Plain factory (NOT a hook).  Called inside handler bodies so closures
 * over the enclosing handler work naturally.
 */
export function createStreamCallbacks({
  setMessages, setRawMessages, setConversationId, setCanContinue,
  setIsStreaming, setHistoryRefreshTrigger, setSubagentChildIds,
  handleSend, handleLoadConversation,
  queuedInputRef, continuationNavigatedRef,
  withQueuedInput = false,
  withRetry = false,
  overrides = {},
  onMessagesRefresh = null,
  onFirstSse = null,
  onStreamEnd = null,
  onQueuedInputSent = null,
  ensureAssistantTail = false,
  isCurrentView = () => true,
}) {
  let _firstMessage = true
  let scheduled = null
  const dispatchPending = (rawMessages) => {
    const pending = queuedInputRef?.current
    if (!withQueuedInput || !pending || !handleSend || !rawMessages?.length || !hasCompleteToolBatch(rawMessages)) {
      return false
    }
    if (scheduled?.pending === pending) return true
    const next = { pending }
    scheduled = next
    setTimeout(() => {
      if (scheduled !== next) return
      scheduled = null
      if (!isCurrentView() || queuedInputRef.current !== pending) return
      queuedInputRef.current = null
      onQueuedInputSent?.()
      handleSend(rawMessages, pending.message, {
        attachedFiles: pending.attachedFiles || [],
      })
    }, 50)
    return true
  }

  const followContinuation = (data) => {
    const nextId = data?.new_conversation_id
    if (!nextId) return
    scheduled = null
    if (continuationNavigatedRef?.current.has(nextId)) return
    continuationNavigatedRef?.current.add(nextId)
    setTimeout(() => {
      if (isCurrentView()) {
        handleLoadConversation?.(nextId, { carryPendingSteering: true, followContinuation: true })
      }
    }, 500)
  }

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
    }
    if (data?.conversation_id) setConversationId(data.conversation_id)
    followContinuation(data)
    // Trigger a diff refresh on every full-state messages event so the
    // code panel always reflects the latest file state after a round.
    onMessagesRefresh?.()
  }

  const onDone = (data) => {
    setConversationId(data.conversation_id)
    setCanContinue(data.status === STATUS.MAX_ITERATIONS_REACHED)
    if (!data.new_conversation_id) setIsStreaming(false)
    if (data.messages) setMessages(data.messages)
    if (data.raw_messages) setRawMessages(data.raw_messages)
    setHistoryRefreshTrigger(prev => prev + 1)
    const finished = [STATUS.COMPLETED, STATUS.MAX_ITERATIONS_REACHED].includes(data.status)
    if (data.new_conversation_id) {
      followContinuation(data)
    } else if (!finished || !dispatchPending(data.raw_messages)) {
      onStreamEnd?.()
    }
  }

  const onError = (error) => {
    if (error?.conversation_id) setConversationId(error.conversation_id)
    if (error?.raw_messages) setRawMessages(error.raw_messages)
    const stableMessages = Array.isArray(error?.messages) ? error.messages : null
    if (withRetry) {
      const isTimeout = error.message?.toLowerCase().includes('timeout') ||
        error.type === 'TimeoutError' || error.message?.toLowerCase().includes('timed out') ||
        error.message?.toLowerCase().includes('504') || error.message?.toLowerCase().includes('gateway timeout')
      setMessages(prev => [...(stableMessages ?? prev), {
        role: 'assistant', content: `Error: ${error.message}`,
        isError: true, isTimeout, canRetry: true
      }])
    } else if (stableMessages) {
      setMessages(stableMessages)
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

 const onDelta = (delta, _status) => {
   if (!delta || (!delta.content && !delta.reasoning_content && !delta.tool_calls)) return;
   setMessages(prevMessages => {
     const newMessages = [...prevMessages];
     if (newMessages.length === 0) {
       newMessages.push({ role: 'assistant', content: delta.content || '', activities: [] });
       return newMessages;
     }
     const msg = { ...newMessages[newMessages.length - 1] };
     if (delta.content) {
       msg.content = (msg.content || '') + delta.content;
     }
     if (delta.reasoning_content) {
       msg.activities = msg.activities ? [...msg.activities] : [];
       const lastAct = msg.activities.length > 0 ? msg.activities[msg.activities.length - 1] : null;
       if (lastAct && lastAct.type === 'thinking') {
         msg.activities[msg.activities.length - 1] = { ...lastAct, content: (lastAct.content || '') + delta.reasoning_content };
       } else {
         msg.activities.push({ type: 'thinking', content: delta.reasoning_content });
       }
     }
     if (delta.tool_calls && delta.tool_calls.length > 0) {
       msg.activities = msg.activities ? [...msg.activities] : [];
       for (const tc of delta.tool_calls) {
         const existingIdx = msg.activities.findIndex(a => a.type === 'tool_call' && a.id === tc.id);
         if (existingIdx >= 0) {
           msg.activities[existingIdx] = { ...msg.activities[existingIdx], name: tc.name, arguments: tc.arguments };
         } else {
           msg.activities.push({ type: 'tool_call', id: tc.id, name: tc.name, arguments: tc.arguments });
         }
       }
     }
     newMessages[newMessages.length - 1] = msg;
     return newMessages;
   });
 };

  const forCurrentView = (callback) => (...args) => {
    if (isCurrentView()) callback(...args)
  }
  return {
    onMessages: forCurrentView(overrides.onMessages || onMessages),
    onDone: forCurrentView(overrides.onDone || onDone),
    onError: forCurrentView(overrides.onError || onError),
    onSubagentEvent: forCurrentView(overrides.onSubagentEvent || onSubagentEvent),
    onDelta: forCurrentView(overrides.onDelta || onDelta),
    onConversationId: forCurrentView(overrides.onConversationId || ((id) => { if (id) setConversationId(id) })),
  };
}
