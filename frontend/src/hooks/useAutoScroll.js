import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Manages chat auto-scroll behaviour.
 *
 * - Auto-scrolls when messages change (unless user has scrolled up).
 * - Forces scroll to bottom when streaming starts.
 * - Captures scroll intent via ``wheel`` to avoid races with content updates.
 */
export function useAutoScroll(messages, isStreaming) {
  const chatContainerRef = useRef(null)
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false)
  const [showScrollButton, setShowScrollButton] = useState(false)
  // Refs mirror the state so wheel/scroll handlers always read the latest
  // values even though their effect closures are stable.
  const scrollingRef = useRef(isStreaming)
  scrollingRef.current = isStreaming
  const scrolledUpRef = useRef(isUserScrolledUp)
  scrolledUpRef.current = isUserScrolledUp

  const scrollToBottom = useCallback((smooth = true) => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTo({
        top: chatContainerRef.current.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      })
    }
  }, [])

  // ── wheel event: capture scroll intent *before* the browser moves ──
  // This eliminates the race where a content update fires scrollToBottom()
  // before the async 'scroll' handler has had a chance to set scrolledUp.
  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const handleWheel = (e) => {
      if (e.deltaY < 0) {
        // Scrolling UP — user wants to read history.  Set the ref
        // *synchronously* so the messages effect sees it instantly.
        if (!scrolledUpRef.current) {
          scrolledUpRef.current = true
          setIsUserScrolledUp(true)
          if (scrollingRef.current) setShowScrollButton(true)
        }
      } else if (e.deltaY > 0) {
        // Scrolling DOWN — if already at the very bottom, re-enable.
        const dist = container.scrollHeight - container.scrollTop - container.clientHeight
        if (dist <= 2) {
          scrolledUpRef.current = false
          setIsUserScrolledUp(false)
          setShowScrollButton(false)
        }
      }
    }

    container.addEventListener('wheel', handleWheel, { passive: true })
    return () => container.removeEventListener('wheel', handleWheel)
  }, [])

  // ── scroll event: final position check (handles scrollbar drag, keyboard, etc.) ──
  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const threshold = 100
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
      const scrolledUp = distanceFromBottom > threshold
      scrolledUpRef.current = scrolledUp
      setIsUserScrolledUp(scrolledUp)
      setShowScrollButton(scrolledUp && scrollingRef.current)
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [])

  // Auto-scroll when messages update, but only if user hasn't scrolled up.
  // Reads the ref (not state) so it never lags behind the wheel handler.
  useEffect(() => {
    if (!scrolledUpRef.current) {
      scrollToBottom()
    }
  }, [messages, isUserScrolledUp, scrollToBottom])

  // When streaming state changes, force scroll to bottom and reset state
  useEffect(() => {
    if (isStreaming) {
      scrolledUpRef.current = false
      setIsUserScrolledUp(false)
      scrollToBottom(false)
    } else {
      setShowScrollButton(false)
    }
  }, [isStreaming, scrollToBottom])

  return { chatContainerRef, scrollToBottom, isUserScrolledUp, setIsUserScrolledUp, showScrollButton }
}
