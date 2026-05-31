import { useState, useEffect, useRef, useCallback } from 'react'

export function useAutoScroll(messages, isStreaming) {
  const chatContainerRef = useRef(null)
  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false)
  const [showScrollButton, setShowScrollButton] = useState(false)
  // Ref for zero-lag intent capture by the messages effect
  const scrolledUpRef = useRef(false)
  // Flag: true while a programmatic scrollToBottom() is in flight so the
  // scroll handler doesn't mistake it for a manual scroll-up.
  const autoScrollingRef = useRef(false)
  // Previous distance — used to detect "scrolled up" vs "scrolled down"
  const prevDistRef = useRef(0)

  const scrollToBottom = useCallback((smooth = true) => {
    const container = chatContainerRef.current
    if (!container) return
    scrolledUpRef.current = false
    autoScrollingRef.current = true
    container.scrollTo({
      top: container.scrollHeight,
      behavior: smooth ? 'smooth' : 'auto',
    })
  }, [])

  // ── wheel event: capture scroll intent *before* the browser moves ──
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
  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const dist = container.scrollHeight - container.scrollTop - container.clientHeight

      // If the scroll was triggered by scrollToBottom(), ignore it —
      // otherwise fast streaming can trigger dist > prev and false-disable.
      if (autoScrollingRef.current) {
        if (dist <= 2) autoScrollingRef.current = false
        prevDistRef.current = dist
        return
      }

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

      // Show button whenever user is not at the bottom — even outside streaming
      setShowScrollButton(dist > 2)
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [isStreaming])

  // Auto-scroll when messages update — reads the ref so the wheel handler
  // takes effect instantly, avoiding the scroll-event race.
  useEffect(() => {
    if (!scrolledUpRef.current) {
      scrollToBottom(true)  // smooth scroll
    }
  }, [messages, scrollToBottom])

  // When streaming state changes
  useEffect(() => {
    if (isStreaming) {
      scrolledUpRef.current = false
      setIsUserScrolledUp(false)
      scrollToBottom(false)
    }
    // Don't hide the scroll button on stream end — the user might still
    // want to jump to the bottom of a long conversation.
  }, [isStreaming, scrollToBottom])

  return { chatContainerRef, scrollToBottom, isUserScrolledUp, setIsUserScrolledUp, showScrollButton }
}
