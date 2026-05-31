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

  // ── scroll event: position check for scrollbar / keyboard ──
  // Track previous distance to detect "scrolled up" (dist increased)
  // vs "scrolled to bottom" (dist ≈ 0).  This avoids using a pixel
  // threshold that would override the wheel handler.
  const prevDistRef = useRef(0)
  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const dist = container.scrollHeight - container.scrollTop - container.clientHeight
      const prev = prevDistRef.current
      prevDistRef.current = dist

      // dist INCREASED → user scrolled UP (zero threshold)
      if (dist > prev) {
        scrolledUpRef.current = true
        setIsUserScrolledUp(true)
      }
      // dist DECREASED to bottom → user scrolled DOWN to bottom → re-enable
      else if (dist <= 2 && dist < prev) {
        scrolledUpRef.current = false
        setIsUserScrolledUp(false)
      }

      setShowScrollButton(dist > 2 && isStreaming)
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
