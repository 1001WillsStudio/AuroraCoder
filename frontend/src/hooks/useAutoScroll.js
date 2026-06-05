import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * Two-state AutoScroll hook.
 *
 * States:
 *   FOLLOWING  – user is at the bottom; auto-scroll keeps new content visible.
 *   BROWSING   – user has scrolled up; auto-scroll is paused.
 *
 * Transitions:
 *   FOLLOWING → BROWSING: any user-initiated scroll-up (wheel ΔY<0, key ArrowUp/
 *                 PageUp/Home, touch-swipe up, scrollbar drag up).
 *   BROWSING  → FOLLOWING: user sends a message, clicks the ↓ button, or manually
 *                 scrolls to the very bottom.
 */

const FOLLOWING = 'following'
const BROWSING = 'browsing'
const BOTTOM_THRESHOLD = 4 // px — treat as "at bottom"

export function useAutoScroll(messages, isStreaming) {
  const chatContainerRef = useRef(null)
  const [mode, setMode] = useState(FOLLOWING)
  const [showScrollButton, setShowScrollButton] = useState(false)

  // Ref mirror so event handlers (stable across renders) read latest mode
  const modeRef = useRef(FOLLOWING)

  // True while a programmatic scrollTo() is in flight — suppress scroll-handler
  // transitions so we don't mistake our own scroll for user input.
  const programmaticScrollRef = useRef(false)

  // ── helpers ──────────────────────────────────────────────────────────

  const isAtBottom = useCallback(() => {
    const c = chatContainerRef.current
    if (!c) return true
    return c.scrollHeight - c.scrollTop - c.clientHeight <= BOTTOM_THRESHOLD
  }, [])

  const scrollToBottom = useCallback((smooth = true) => {
    const c = chatContainerRef.current
    if (!c) return
    programmaticScrollRef.current = true
    c.scrollTo({ top: c.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
    // If we're already at bottom (e.g., content fits viewport) or the browser
    // doesn't fire a scroll event, clear the flag after the animation frame
    // so user input detection isn't permanently blocked.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (isAtBottom()) programmaticScrollRef.current = false
      })
    })
  }, [isAtBottom])

  /**
   * Public API – called from App when user sends a message or clicks the ↓ button.
   */
  const resetToFollowing = useCallback(() => {
    modeRef.current = FOLLOWING
    setMode(FOLLOWING)
    setShowScrollButton(false)
    scrollToBottom(false) // instant scroll – user just sent a message
  }, [scrollToBottom])

  // ── update showScrollButton whenever mode changes ──
  useEffect(() => {
    if (mode === BROWSING) {
      // Show button immediately, but verify current position
      setShowScrollButton(!isAtBottom())
    } else {
      setShowScrollButton(false)
    }
  }, [mode, isAtBottom])

  // ── 1. wheel event – detect scroll-up intent ────────────────────────

  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const onWheel = (e) => {
      // User input is always authoritative — never suppress.
      if (e.deltaY < 0) {
        // User scrolled up → BROWSING.
        // Show button immediately (don't wait for the mode effect — the
        // browser may not have processed the scroll yet, so isAtBottom()
        // could still be true at that point).
        modeRef.current = BROWSING
        setMode(BROWSING)
        setShowScrollButton(true)
      }
      // deltaY > 0 (scrolling down): let the scroll handler decide
      // when user actually reaches bottom
    }

    container.addEventListener('wheel', onWheel, { passive: true })
    return () => container.removeEventListener('wheel', onWheel)
  }, []) // stable – never needs re-binding

  // ── 2. keydown event – catch keyboard navigation ────────────────────
  // NOTE: Redundant — wheel + scroll handler already cover mouse/trackpad.
  // Can be removed upon request.

  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const onKeyDown = (e) => {
      // Only handle keys when the chat container has focus (or is the active area)
      // ArrowUp / PageUp / Home → user scrolled up → BROWSING
      if (e.key === 'ArrowUp' || e.key === 'PageUp' || e.key === 'Home') {
        // Small delay so the browser's native scroll happens first,
        // then we check if user actually moved up
        requestAnimationFrame(() => {
          if (!isAtBottom()) {
            modeRef.current = BROWSING
            setMode(BROWSING)
          }
        })
        return
      }

      // ArrowDown / PageDown / End → if reaching bottom → FOLLOWING
      if (e.key === 'ArrowDown' || e.key === 'PageDown' || e.key === 'End') {
        requestAnimationFrame(() => {
          if (isAtBottom()) {
            modeRef.current = FOLLOWING
            setMode(FOLLOWING)
          }
        })
      }
    }

    // Listen on the document so it works even when an inner element has focus
    // within the chat container. We filter with a target check.
    const onKeyDownFiltered = (e) => {
      if (!chatContainerRef.current) return
      // Only process if the event target is inside the chat container
      // (not in an input, textarea, or settings panel)
      const target = e.target
      if (!target) return
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return
      if (!chatContainerRef.current.contains(target)) return
      onKeyDown(e)
    }

    document.addEventListener('keydown', onKeyDownFiltered)
    return () => document.removeEventListener('keydown', onKeyDownFiltered)
  }, [isAtBottom])

  // ── 3. touch events – mobile scroll detection ───────────────────────
  // NOTE: Redundant — wheel + scroll handler already cover most devices.
  // Can be removed upon request.

  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    let touchStartY = 0

    const onTouchStart = (e) => {
      if (e.touches.length === 1) {
        touchStartY = e.touches[0].clientY
      }
    }

    const onTouchMove = (e) => {
      // User input is always authoritative — never suppress.
      if (e.touches.length !== 1) return
      const deltaY = touchStartY - e.touches[0].clientY
      if (deltaY < -5) {
        // User swiped up → BROWSING (show button immediately).
        modeRef.current = BROWSING
        setMode(BROWSING)
        setShowScrollButton(true)
      }
      // deltaY > 5: swiping down — let scroll handler decide
    }

    container.addEventListener('touchstart', onTouchStart, { passive: true })
    container.addEventListener('touchmove', onTouchMove, { passive: true })
    return () => {
      container.removeEventListener('touchstart', onTouchStart)
      container.removeEventListener('touchmove', onTouchMove)
    }
  }, [])

  // ── 4. scroll event – reach-bottom detection & scrollbar tracking ───

  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const dist = container.scrollHeight - container.scrollTop - container.clientHeight

      // If programmatic scroll is in flight and we're at bottom, clear flag
      if (programmaticScrollRef.current) {
        if (dist <= BOTTOM_THRESHOLD) programmaticScrollRef.current = false
        return // don't process for user-intent detection
      }

      // Update button visibility
      setShowScrollButton(dist > BOTTOM_THRESHOLD)

      // Only transition TO BROWSING from FOLLOWING if the user scrolled up
      // AND the scroll wasn't programmatic. Since we can't directly distinguish
      // scrollbar-drag from programmatic scroll in the scroll event, we rely on
      // programmaticScrollRef and the wheel/keyboard/touch handlers to set BROWSING.
      // For scrollbar drag: if mode is FOLLOWING and dist exceeds threshold,
      // we treat it as BROWSING.
      if (modeRef.current === FOLLOWING && dist > BOTTOM_THRESHOLD) {
        // Only transition to BROWSING if we've been at bottom before.
        // This prevents false positives from layout shifts during initial load.
        modeRef.current = BROWSING
        setMode(BROWSING)
        setShowScrollButton(true)
      }

      // Transition TO FOLLOWING: user scrolled all the way down
      if (modeRef.current === BROWSING && dist <= BOTTOM_THRESHOLD) {
        modeRef.current = FOLLOWING
        setMode(FOLLOWING)
        setShowScrollButton(false)
      }
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, []) // stable

  // ── 5. ResizeObserver – auto-scroll when content grows in FOLLOWING ──

  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const ro = new ResizeObserver(() => {
      if (modeRef.current === FOLLOWING) {
        scrollToBottom(false) // instant — smooth scroll fights rapid streaming
      }
    })

    ro.observe(container)
    return () => ro.disconnect()
  }, [scrollToBottom])

  // ── 6. Show/hide scroll button based on position ────────────────────

  // When streaming starts, if we're already FOLLOWING, ensure we're at bottom
  useEffect(() => {
    if (isStreaming && modeRef.current === FOLLOWING) {
      scrollToBottom(false) // instant
    }
    // NOTE: we do NOT reset to FOLLOWING when streaming starts if user is BROWSING
    // The user may be reading older messages while a new stream runs.
  }, [isStreaming, scrollToBottom])

  return {
    chatContainerRef,
    scrollToBottom,
    resetToFollowing,
    isUserScrolledUp: mode === BROWSING,
    setIsUserScrolledUp: (val) => {
      const next = val ? BROWSING : FOLLOWING
      modeRef.current = next
      setMode(next)
      if (next === FOLLOWING) setShowScrollButton(false)
    },
    showScrollButton,
    mode,
  }
}
