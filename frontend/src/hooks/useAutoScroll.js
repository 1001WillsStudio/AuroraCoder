import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Manages chat auto-scroll behaviour.
 *
 * - Auto-scrolls when messages change (unless user has scrolled up).
 * - Forces scroll to bottom when streaming starts.
 * - Tracks user scroll position to disable auto-scroll while reading history.
 */
export function useAutoScroll(messages, isStreaming) {
  const chatContainerRef = useRef(null)
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false)
  const [showScrollButton, setShowScrollButton] = useState(false)

  const scrollToBottom = useCallback((smooth = true) => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTo({
        top: chatContainerRef.current.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      })
    }
  }, [])

  // Track user scroll position on the chat container
  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return
    const handleScroll = () => {
      const threshold = 100
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
      const scrolledUp = distanceFromBottom > threshold
      setIsUserScrolledUp(scrolledUp)
      setShowScrollButton(scrolledUp && isStreaming)
    }
    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [isStreaming])

  // Auto-scroll when messages update, but only if user hasn't scrolled up
  useEffect(() => {
    if (!isUserScrolledUp) {
      scrollToBottom()
    }
  }, [messages, isUserScrolledUp, scrollToBottom])

  // When streaming state changes, force scroll to bottom and reset state
  useEffect(() => {
    if (isStreaming) {
      setIsUserScrolledUp(false)
      scrollToBottom(false)
    } else {
      setShowScrollButton(false)
    }
  }, [isStreaming, scrollToBottom])

  return { chatContainerRef, scrollToBottom, isUserScrolledUp, setIsUserScrolledUp, showScrollButton }
}
