import { useState, useEffect, useRef, useCallback } from 'react'

export function useAutoScroll(messages, isStreaming) {
  const chatContainerRef = useRef(null)
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false)
  const [showScrollButton, setShowScrollButton] = useState(false)
  // Ref so the messages effect reads scroll intent *before* React re-renders
  const scrolledUpRef = useRef(false)

  const scrollToBottom = useCallback((smooth = true) => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTo({
        top: chatContainerRef.current.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto',
      })
    }
  }, [])

  // ── wheel event: capture scroll intent *before* the browser moves ──
  // Setting the ref synchronously eliminates the old race where a content
  // update arrived between a scroll-up and the async 'scroll' handler.
  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const onWheel = (e) => {
      if (e.deltaY < 0) {
        scrolledUpRef.current = true
        setIsUserScrolledUp(true)
      }
    }

    container.addEventListener('wheel', onWheel, { passive: true })
    return () => container.removeEventListener('wheel', onWheel)
  }, [isStreaming])

  // ── scroll event: normal position check ──
  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const threshold = 100
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
      const scrolledUp = distanceFromBottom > threshold
      scrolledUpRef.current = scrolledUp
      setIsUserScrolledUp(scrolledUp)
      if (isStreaming && scrolledUp) {
        setShowScrollButton(true)
      } else if (!scrolledUp) {
        setShowScrollButton(false)
      }
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [isStreaming])

  // Auto-scroll when messages update — reads the ref so the wheel handler
  // takes effect instantly, avoiding the scroll-event race.
  useEffect(() => {
    if (!scrolledUpRef.current) {
      scrollToBottom()
    }
  }, [messages, scrollToBottom])

  // When streaming state changes
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
