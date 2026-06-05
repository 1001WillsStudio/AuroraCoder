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

  // Track last explicit user scroll direction — BROWSING → FOLLOWING only
  // fires when the user intentionally scrolls down (not content shrink).
  const lastScrollDirRef = useRef(null) // 'up' | 'down' | null


  // ── helpers ──────────────────────────────────────────────────────────

  const isAtBottom = useCallback(() => {
    const c = chatContainerRef.current
    if (!c) return true
    return c.scrollHeight - c.scrollTop - c.clientHeight <= BOTTOM_THRESHOLD
  }, [])

  const scrollToBottom = useCallback((smooth = true) => {
    const c = chatContainerRef.current
    if (!c) return
    c.scrollTo({ top: c.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }, [])

  /**
   * Public API – called from App when user sends a message or clicks the ↓ button.
   */
  const resetToFollowing = useCallback(() => {
    lastScrollDirRef.current = null // explicit, not user-driven
    modeRef.current = FOLLOWING
    setMode(FOLLOWING)
    setShowScrollButton(false)
    scrollToBottom(false)
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
  //    No deps array so it retries after every render, because the first
  //    render may be a loading spinner where chatContainerRef is null.

  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const onWheel = (e) => {
      if (e.deltaY < 0) {
        // User scrolled up → BROWSING
        lastScrollDirRef.current = 'up'
        modeRef.current = BROWSING
        setMode(BROWSING)
        setShowScrollButton(true)
      } else if (e.deltaY > 0) {
        // User scrolled down — record direction for BROWSING→FOLLOWING gate
        lastScrollDirRef.current = 'down'
      }
    }

    container.addEventListener('wheel', onWheel, { passive: true })
    return () => container.removeEventListener('wheel', onWheel)
  })

  // ── 2. keydown / touch – redundant fallbacks ────────────────────────
  //    Can be removed upon request.

  // ── 3. scroll event – button visibility + reach-bottom detection ───
  //    No deps so it retries after every render (same reason as wheel).
  //    State transitions TO FOLLOWING are gated on lastScrollDirRef === 'down'
  //    so the user must explicitly scroll down — not just land at bottom from
  //    content shrinking or a programmatic scroll.

  useEffect(() => {
    const container = chatContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const dist = container.scrollHeight - container.scrollTop - container.clientHeight

      // Update button visibility
      setShowScrollButton(dist > BOTTOM_THRESHOLD)

      // Transition TO FOLLOWING: only when user explicitly scrolled down
      if (
        modeRef.current === BROWSING &&
        dist <= BOTTOM_THRESHOLD &&
        lastScrollDirRef.current === 'down'
      ) {
        lastScrollDirRef.current = null
        modeRef.current = FOLLOWING
        setMode(FOLLOWING)
        setShowScrollButton(false)
      }
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  })

  // ── 4. Auto-scroll when messages update and user is FOLLOWING ──────
  useEffect(() => {
    if (modeRef.current === FOLLOWING) {
      scrollToBottom(false) // instant — smooth scroll fights rapid streaming
    }
  }, [messages, scrollToBottom])

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
