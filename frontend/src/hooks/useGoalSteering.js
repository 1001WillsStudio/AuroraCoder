import { useCallback, useEffect, useRef, useState } from 'react'
import { submitTaskSteering } from '../services/api'
import { newConversationId } from '../utils/uuid'

const STORAGE_KEY = 'auroracoder:goal-steering-outbox'

function readOutbox() {
  try {
    const entries = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(entries) ? entries : []
  } catch {
    return []
  }
}

export default function useGoalSteering(conversationId, onUndelivered) {
  const [outbox, setOutbox] = useState(readOutbox)
  const [received, setReceived] = useState(null)
  const inFlight = useRef(new Set())
  const entries = useRef(outbox)
  const onUndeliveredRef = useRef(onUndelivered)
  onUndeliveredRef.current = onUndelivered

  const persist = useCallback(next => {
    entries.current = next
    setOutbox(next)
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // The gateway still owns any submission it acknowledged.
    }
  }, [])

  const deliver = useCallback(async (entry, fallback = false) => {
    if (inFlight.current.has(entry.receipt)) return
    inFlight.current.add(entry.receipt)
    try {
      let result
      try {
        result = await submitTaskSteering(entry.conversationId, entry)
      } catch {
        // A lost acknowledgement must retry the same receipt and payload.
        result = await submitTaskSteering(entry.conversationId, entry)
      }
      persist(entries.current.filter(saved => saved.receipt !== entry.receipt))
      if (result === 'accepted') setReceived(entry.conversationId)
      else onUndeliveredRef.current(entry, fallback && result === 'no-goal', result)
    } catch {
      // Keep the outbox entry distinct from draft text until receipt is known.
    } finally {
      inFlight.current.delete(entry.receipt)
    }
  }, [persist])

  useEffect(() => {
    const retryOutbox = () => {
      for (const entry of entries.current) deliver(entry)
    }
    retryOutbox()
    window.addEventListener('online', retryOutbox)
    return () => window.removeEventListener('online', retryOutbox)
  }, [deliver])

  const pending = outbox.find(entry => entry.conversationId === conversationId)
  const send = (cid, message, attachedFiles) => {
    if (!cid || entries.current.some(entry => entry.conversationId === cid)) return false
    const entry = { conversationId: cid, message, attachedFiles, receipt: newConversationId() }
    setReceived(null)
    persist([...entries.current, entry])
    deliver(entry, true)
    return true
  }

  return {
    pending, send, received: received === conversationId && !!conversationId,
    retry: () => pending && deliver(pending, true),
  }
}
