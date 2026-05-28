import React, { useState, useRef, useEffect, useCallback } from 'react'
import { RotateCcw, X, ArrowDown } from 'lucide-react'
import ChatMessage from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import LoginScreen from './components/LoginScreen'
import { isAuthenticated, checkAuth, isAuthRequired } from './utils/auth.js'
import CodePanel from './components/CodePanel'
import Sidebar from './components/Sidebar'
import WelcomeScreen from './components/WelcomeScreen'
import SettingsPanel from './components/SettingsPanel'
import { streamChat, getProviders, cancelConversation, getConversation, getActiveStreams, resumeStream } from './services/api'
import { isInterruptible, TASK_MARKER_START, TASK_MARKER_END, formatElapsedTime } from './utils/streamUtils'
import { useFileTracking } from './hooks/useFileTracking'
import useLanguage from './hooks/useLanguage'

function App() {
  const { t } = useLanguage()

  // ── Auth state ───────────────────────────────────────────────────────
  const [authState, setAuthState] = useState({
    checked: false,
    required: false,
    authenticated: false,
  })

  const [messages, setMessages] = useState([])
  const [rawMessages, setRawMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [conversationId, setConversationId] = useState(null)
  const [canContinue, setCanContinue] = useState(false)
  const [pendingInterrupt, setPendingInterrupt] = useState(null)
  
  // Provider state
  const [providers, setProviders] = useState([])
  const [selectedProvider, setSelectedProvider] = useState(null)
  const [showProviderDropdown, setShowProviderDropdown] = useState(false)
  
  // Theme state
  const [theme, setTheme] = useState(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('theme')
      if (saved) return saved
      return 'light'
    }
    return 'light'
  })
  
  // ── File tracking hook ──
  const {
    editedFiles, activeFileId, setActiveFileId,
    showCodePanel, setShowCodePanel,
    isLoadingFiles,
    fileTreeRefreshTrigger, setFileTreeRefreshTrigger,
    isUploading, uploadInputRef,
    handleFileClose, handleCloseCodePanel,
    handleRefreshFiles, handleFileTreeClick, handleUploadProject,
    setEditedFiles, setClosedFiles,
  } = useFileTracking(conversationId, messages, isStreaming)

  // Other state
  const [lastRequest, setLastRequest] = useState(null)
  const [showTaskInstructions, setShowTaskInstructions] = useState(false)
  const taskInstructionsRef = useRef(null)
  const taskInstructionsBtnRef = useRef(null)
  const [historyCloseTrigger, setHistoryCloseTrigger] = useState(0)
  const [systemPrompt, setSystemPrompt] = useState(() => {
    try { return localStorage.getItem('systemPrompt') || '' } catch { return '' }
  })
  const [historyRefreshTrigger, setHistoryRefreshTrigger] = useState(0)
  const [activeConvoWarning, setActiveConvoWarning] = useState(false)
  const draftInputsRef = useRef(new Map())
  const [viewMode, setViewMode] = useState('main')
  const [parentConversationId, setParentConversationId] = useState(null)
  // Map from tool_call_id → child_id for accurate correlation between
  // subagent_event notifications and their originating tool calls.
  const [subagentChildIds, setSubagentChildIds] = useState({})
  const [showSettings, setShowSettings] = useState(false)
  const [forkWarning, setForkWarning] = useState(null)
  const messagesEndRef = useRef(null)
  const chatContainerRef = useRef(null)
  const inputRef = useRef(null)
  const abortControllerRef = useRef(null)
  const pendingInterruptRef = useRef(null)
  const conversationIdRef = useRef(null)
  const continuationNavigatedRef = useRef(new Set())
  const forkClickRef = useRef({ time: 0, idx: -1 })

  // ── Effects ─────────────────────────────────────────────────────────────

  // ── Auth check (runs before everything else) ────────────────────────
  useEffect(() => {
    let cancelled = false
    async function check() {
      const needed = await isAuthRequired()
      if (cancelled) return
      if (!needed) {
        setAuthState({ checked: true, required: false, authenticated: true })
        return
      }
      const authed = await checkAuth()
      if (cancelled) return
      setAuthState({ checked: true, required: true, authenticated: authed })
    }
    check()
    return () => { cancelled = true }
  }, [])

  const handleLoginSuccess = useCallback(() => {
    setAuthState(prev => ({ ...prev, authenticated: true }))
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  useEffect(() => {
    const handleBeforeUnload = () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
      if (abortControllerRef.current) abortControllerRef.current.abort()
    }
  }, [])

  useEffect(() => {
    async function loadProviders() {
      try {
        const data = await getProviders()
        setProviders(data.providers || [])
        const savedProvider = localStorage.getItem('selectedProvider')
        if (savedProvider && data.providers?.find(p => p.id === savedProvider)) {
          setSelectedProvider(savedProvider)
        } else {
          setSelectedProvider(data.default || data.providers?.[0]?.id)
        }
      } catch {
        setProviders([{ id: 'deepseek', name: 'DeepSeek V4 Pro', description: 'Default model' }])
        setSelectedProvider('deepseek')
      }
    }
    loadProviders()
  }, [])

  useEffect(() => {
    if (selectedProvider) localStorage.setItem('selectedProvider', selectedProvider)
  }, [selectedProvider])

  // Keep conversationIdRef in sync so useCallback handlers always have the latest value
  useEffect(() => {
    conversationIdRef.current = conversationId
  }, [conversationId])

  useEffect(() => {
    if (!showTaskInstructions) return
    function handleClick(e) {
      if (taskInstructionsBtnRef.current?.contains(e.target)) return
      if (taskInstructionsRef.current && !taskInstructionsRef.current.contains(e.target)) {
        setShowTaskInstructions(false)
      }
    }
    function handleKey(e) { if (e.key === 'Escape') setShowTaskInstructions(false) }
    const timer = setTimeout(() => document.addEventListener('mousedown', handleClick), 0)
    document.addEventListener('keydown', handleKey)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [showTaskInstructions])

  const [isUserScrolledUp, setIsUserScrolledUp] = useState(false)
  const [showScrollButton, setShowScrollButton] = useState(false)

  const scrollToBottom = useCallback((smooth = true) => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTo({
        top: chatContainerRef.current.scrollHeight,
        behavior: smooth ? 'smooth' : 'auto'
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

  // ── Handlers ────────────────────────────────────────────────────────────

  const toggleTheme = () => setTheme(prev => prev === 'dark' ? 'light' : 'dark')

  const handleSend = async (interruptMessages = null, overrideMessage = null, options = {}) => {
    const sendT0 = performance.now()
    const log = (label) => console.log(`[timing][handleSend] ${performance.now().toFixed(1)}ms | ${label} (+${(performance.now() - sendT0).toFixed(1)}ms)`)
    log('entered')

    const messageToSend = overrideMessage || inputValue.trim()
    if (!messageToSend) return

    if (!conversationId && !interruptMessages) {
      log('getActiveStreams check start')
      try {
        const { active } = await getActiveStreams()
        log('getActiveStreams check done')
        if (active && active.length > 0) {
          setActiveConvoWarning(true)
          return
        }
      } catch { /* server unreachable — allow send */ }
    }
    setActiveConvoWarning(false)

    const userMessageText = messageToSend
    const apiMessage = (systemPrompt.trim() && !conversationId)
      ? `${TASK_MARKER_START}\n${systemPrompt.trim()}\n${TASK_MARKER_END}\n\n${userMessageText}`
      : userMessageText

    const isInterrupt = interruptMessages !== null && interruptMessages.length > 0

    if (abortControllerRef.current) {
      log('aborting previous controller')
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      await new Promise(resolve => setTimeout(resolve, 100))
      log('abort settle done')
    }

    log('setState batch (messages, streaming, etc.)')
    setMessages(prev => [...prev, { role: 'user', content: userMessageText }])
    setInputValue('')
    setIsStreaming(true)
    setCanContinue(false)
    setHistoryRefreshTrigger(prev => prev + 1)

    let messagesToSend = null
    if (isInterrupt) {
      messagesToSend = interruptMessages
    } else if (conversationId && rawMessages.length > 0) {
      messagesToSend = rawMessages
    }

    setLastRequest({ message: userMessageText, conversationId, provider: selectedProvider, existingMessages: messagesToSend })

    log('about to call streamChat()')
    try {
      abortControllerRef.current = new AbortController()
      await streamChat(apiMessage, conversationId, {
        onMessages: (frontendMessages, status, data) => {
          setMessages(frontendMessages)
          if (data?.raw_messages) {
            setRawMessages(data.raw_messages)
            if (pendingInterruptRef.current && isInterruptible(data.raw_messages)) {
              const interruptMessage = pendingInterruptRef.current.message
              const messagesForInterrupt = data.raw_messages
              setPendingInterrupt(null)
              pendingInterruptRef.current = null
              if (abortControllerRef.current) abortControllerRef.current.abort()
              setTimeout(() => { handleSend(messagesForInterrupt, interruptMessage) }, 50)
            }
          }
          if (data?.conversation_id) setConversationId(data.conversation_id)
          if (data?.new_conversation_id && !continuationNavigatedRef.current.has(data.new_conversation_id)) {
            continuationNavigatedRef.current.add(data.new_conversation_id)
            setTimeout(() => { handleLoadConversation(data.new_conversation_id) }, 500)
          }
        },
        onDone: (data) => {
          setConversationId(data.conversation_id)
          setCanContinue(data.status === 'max_iterations_reached')
          setIsStreaming(false)
          if (data.messages) setMessages(data.messages)
          if (data.raw_messages) setRawMessages(data.raw_messages)
          setHistoryRefreshTrigger(prev => prev + 1)
        },
        onError: (error) => {
          const isTimeout = error.message?.toLowerCase().includes('timeout') ||
            error.type === 'TimeoutError' || error.message?.toLowerCase().includes('timed out') ||
            error.message?.toLowerCase().includes('504') || error.message?.toLowerCase().includes('gateway timeout')
          setMessages(prev => [...prev, {
            role: 'assistant', content: `Error: ${error.message}`,
            isError: true, isTimeout, canRetry: true
          }])
          setIsStreaming(false)
          setHistoryRefreshTrigger(prev => prev + 1)
        },
        onSubagentEvent: (evt) => {
          if (evt.child_id && evt.tool_call_id) {
            setSubagentChildIds(prev => ({ ...prev, [evt.tool_call_id]: evt.child_id }))
          } else if (evt.child_id) {
            // Fallback: use index-based mapping for events without tool_call_id
            setSubagentChildIds(prev => {
              const arr = prev._fallback || []
              return { ...prev, _fallback: arr.includes(evt.child_id) ? arr : [...arr, evt.child_id] }
            })
          }
          setHistoryRefreshTrigger(prev => prev + 1)
        }
      }, abortControllerRef.current.signal, messagesToSend, selectedProvider, options)
    } catch (error) {
      if (error.name !== 'AbortError') console.error('Chat error:', error)
      setIsStreaming(false)
    }
  }

  const handleInterruptSend = () => {
    if (!inputValue.trim()) return
    if (!isStreaming) { handleSend(); return }
    if (isInterruptible(rawMessages)) {
      handleSend(rawMessages)
    } else {
      const interruptData = { message: inputValue.trim(), rawMessages }
      setPendingInterrupt(interruptData)
      pendingInterruptRef.current = interruptData
      setInputValue('')
    }
  }

  const handleContinue = async () => {
    if (!conversationId || isStreaming || rawMessages.length === 0) return
    setIsStreaming(true)
    setCanContinue(false)
    try {
      abortControllerRef.current = new AbortController()
      await streamChat(null, conversationId, {
        onMessages: (frontendMessages, status, data) => {
          setMessages(frontendMessages)
          if (data?.raw_messages) setRawMessages(data.raw_messages)
          if (data?.new_conversation_id && !continuationNavigatedRef.current.has(data.new_conversation_id)) {
            continuationNavigatedRef.current.add(data.new_conversation_id)
            setTimeout(() => { handleLoadConversation(data.new_conversation_id) }, 500)
          }
        },
        onDone: (data) => {
          setConversationId(data.conversation_id)
          setCanContinue(data.status === 'max_iterations_reached')
          setIsStreaming(false)
          if (data.messages) setMessages(data.messages)
          if (data.raw_messages) setRawMessages(data.raw_messages)
          setHistoryRefreshTrigger(prev => prev + 1)
        },
        onError: () => {
          setIsStreaming(false)
          setHistoryRefreshTrigger(prev => prev + 1)
        },
        onSubagentEvent: (evt) => {
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
      }, abortControllerRef.current.signal, rawMessages, selectedProvider)
    } catch (error) {
      if (error.name !== 'AbortError') console.error('Continue error:', error)
      setIsStreaming(false)
    }
  }
  // ── Fork helpers ─────────────────────────────────────────────────

  /** Find the raw-message index of the Nth user message in frontend messages */
  const findForkPoint = useCallback((frontendUserMsgIdx) => {
    // Count user messages in frontend up to (but not including) the fork point.
    // This correctly handles frontend messages with multiple consecutive
    // assistant entries (thinking, tool_calls, content emitted as separate
    // messages), which break the even-index assumption.
    let userRound = 0
    for (let i = 0; i < frontendUserMsgIdx; i++) {
      if (messages[i].role === 'user') userRound++
    }
    let userCount = 0
    for (let i = 0; i < rawMessages.length; i++) {
      if (rawMessages[i].role === 'user') {
        if (userCount === userRound) return i
        userCount++
      }
    }
    return rawMessages.length
  }, [rawMessages, messages])

  const handleForkConversation = useCallback((frontendMsgIdx, skipWarning = false) => {
    const rawIdx = findForkPoint(frontendMsgIdx)
    const CODE_MUTATING = ['run_terminal_command', 'write_file', 'edit_file', 'delete_file']
    const toolsAfterFork = []
    for (let i = rawIdx; i < rawMessages.length; i++) {
      for (const tc of rawMessages[i].tool_calls || []) {
        const name = tc.function?.name || ''
        if (CODE_MUTATING.includes(name)) toolsAfterFork.push({ name, idx: i })
      }
    }
    if (toolsAfterFork.length > 0 && !skipWarning) {
      setForkWarning({ frontendMsgIdx, rawIdx, toolsAfterFork })
      return
    }
    if (abortControllerRef.current) { abortControllerRef.current.abort(); abortControllerRef.current = null }
    if (inputValue.trim()) draftInputsRef.current.set(conversationId ?? '__new__', inputValue)
    setConversationId(crypto.randomUUID())
    setRawMessages(rawMessages.slice(0, rawIdx))
    setMessages(messages.slice(0, frontendMsgIdx))
    setIsStreaming(false)
    setCanContinue(false)
    setForkWarning(null)
    setEditedFiles([])
    setClosedFiles(new Set())
    setViewMode('main')
    setInputValue('')
    setHistoryRefreshTrigger(prev => prev + 1)
  }, [rawMessages, messages, conversationId, inputValue, findForkPoint])

  const handleClear = () => {
    if (inputValue.trim()) draftInputsRef.current.set(conversationId ?? '__new__', inputValue)
    if (abortControllerRef.current) abortControllerRef.current.abort()
    setMessages([])
    setRawMessages([])
    setConversationId(null)
    setIsStreaming(false)
    setCanContinue(false)
    setPendingInterrupt(null)
    pendingInterruptRef.current = null
    setEditedFiles([])
    setClosedFiles(new Set())
    setActiveConvoWarning(false)
    setViewMode('main')
    setParentConversationId(null)
    setSubagentChildIds({})
    setForkWarning(null)
    setHistoryRefreshTrigger(prev => prev + 1)
    const draft = draftInputsRef.current.get('__new__') || ''
    setInputValue(draft)
    inputRef.current?.focus()
  }

  const handleStop = async () => {
    if (abortControllerRef.current) abortControllerRef.current.abort()
    if (conversationId) {
      try {
        await cancelConversation(conversationId)
      } catch { /* ignore — best-effort cancel */ }
    }

    // Inject synthetic tool responses for any orphan tool_calls so the
    // local rawMessages stay valid for immediate continuation (the backend
    // middleware does the same for persisted state in its finally block).
    setRawMessages(prev => {
      const respondedIds = new Set()
      for (const m of prev) {
        if (m.role === 'tool' && m.tool_call_id) respondedIds.add(m.tool_call_id)
      }
      let injected = false
      const fixed = [...prev]
      for (const m of prev) {
        if (m.role !== 'assistant') continue
        for (const tc of m.tool_calls || []) {
          if (tc.id && !respondedIds.has(tc.id)) {
            const toolName = tc.function?.name || tc.name || 'unknown'
            fixed.push({
              role: 'tool',
              tool_call_id: tc.id,
              name: toolName,
              content: JSON.stringify({ status: 'stopped', message: `Tool "${toolName}" was stopped by the user.` }),
            })
            injected = true
          }
        }
      }
      return injected ? fixed : prev
    })

    setIsStreaming(false)
    setPendingInterrupt(null)
    pendingInterruptRef.current = null
    setActiveConvoWarning(false)
    setHistoryRefreshTrigger(prev => prev + 1)
  }

  const handleStopTool = useCallback((toolInfo) => {
    if (abortControllerRef.current) abortControllerRef.current.abort()
    const cid = conversationIdRef.current
    if (cid) cancelConversation(cid)

    const terminationMessage = `Tool terminated by user after ${formatElapsedTime(toolInfo.elapsedSeconds)}`

    setRawMessages(prev => {
      const newRawMessages = [...prev]
      let lastAssistantIdx = -1
      for (let i = newRawMessages.length - 1; i >= 0; i--) {
        if (newRawMessages[i].role === 'assistant' && newRawMessages[i].tool_calls?.length > 0) {
          lastAssistantIdx = i; break
        }
      }
      if (lastAssistantIdx === -1) return prev
      const toolCalls = newRawMessages[lastAssistantIdx].tool_calls || []
      const existingToolResponseIds = new Set()
      for (let i = lastAssistantIdx + 1; i < newRawMessages.length; i++) {
        if (newRawMessages[i].role === 'tool' && newRawMessages[i].tool_call_id) {
          existingToolResponseIds.add(newRawMessages[i].tool_call_id)
        }
      }
      for (const tc of toolCalls) {
        if (tc.id && !existingToolResponseIds.has(tc.id)) {
          newRawMessages.push({ role: 'tool', tool_call_id: tc.id, content: terminationMessage })
        }
      }
      return newRawMessages
    })

    setMessages(prev => {
      const newMessages = [...prev]
      const lastIdx = newMessages.length - 1
      if (lastIdx >= 0 && newMessages[lastIdx].role === 'assistant') {
        const lastMsg = { ...newMessages[lastIdx] }
        const activities = [...(lastMsg.activities || [])]
        activities.push({ type: 'tool_result', tool_call_id: toolInfo.toolCall.id, content: terminationMessage, isTerminated: true })
        lastMsg.activities = activities
        const terminationNote = `\n\n---\n**${t('app.toolStopped')}** ${t('app.toolStoppedByUser', { tool: toolInfo.config?.label || toolInfo.toolName, time: formatElapsedTime(toolInfo.elapsedSeconds) })}`
        lastMsg.content = (lastMsg.content || '') + terminationNote
        newMessages[lastIdx] = lastMsg
      }
      return newMessages
    })

    setIsStreaming(false)
    setPendingInterrupt(null)
    pendingInterruptRef.current = null
  }, [])

  const handleRetry = useCallback(() => {
    if (!lastRequest || isStreaming) return
    setMessages(prev => {
      const lastMsg = prev[prev.length - 1]
      if (lastMsg?.isError) return prev.slice(0, -1)
      return prev
    })
    handleSend(lastRequest.existingMessages, lastRequest.message)
  }, [lastRequest, isStreaming])

  const handleLoadConversation = useCallback(async (targetConversationId) => {
    if (inputValue.trim()) draftInputsRef.current.set(conversationId ?? '__new__', inputValue)
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    try {
      const conv = await getConversation(targetConversationId)
      const isSubagent = conv.type === 'subagent'
      setConversationId(targetConversationId)
      setRawMessages(conv.messages || [])
      setMessages(conv.frontend_messages || [])
      setCanContinue(!isSubagent && conv.status === 'max_iterations_reached')
      setIsStreaming(false)
      setActiveConvoWarning(false)
      setEditedFiles([])
      setClosedFiles(new Set())
      setViewMode(isSubagent ? 'subagent' : 'main')
      setParentConversationId(isSubagent ? conv.parent_id : null)
      const draft = isSubagent ? '' : (draftInputsRef.current.get(targetConversationId) || '')
      setInputValue(draft)
      if (conv.status === 'running') {
        setIsStreaming(true)
        abortControllerRef.current = new AbortController()
        try {
          await resumeStream(targetConversationId, {
            onMessages: (frontendMessages, status, data) => {
              setMessages(frontendMessages)
              if (data?.raw_messages) setRawMessages(data.raw_messages)
              if (data?.new_conversation_id && !continuationNavigatedRef.current.has(data.new_conversation_id)) {
                continuationNavigatedRef.current.add(data.new_conversation_id)
                setTimeout(() => { handleLoadConversation(data.new_conversation_id) }, 500)
              }
            },
            onDone: (data) => {
              setIsStreaming(false)
              if (data.messages) setMessages(data.messages)
              if (data.raw_messages) setRawMessages(data.raw_messages)
              setCanContinue(!isSubagent && data.status === 'max_iterations_reached')
              setHistoryRefreshTrigger(prev => prev + 1)
            },
            onError: () => { setIsStreaming(false); setHistoryRefreshTrigger(prev => prev + 1) },
            onSubagentEvent: (evt) => {
              if (evt.child_id && evt.tool_call_id) {
                setSubagentChildIds(prev => ({ ...prev, [evt.tool_call_id]: evt.child_id }))
              } else if (evt.child_id) {
                setSubagentChildIds(prev => {
                  const arr = prev._fallback || []
                  return { ...prev, _fallback: arr.includes(evt.child_id) ? arr : [...arr, evt.child_id] }
                })
              }
              setHistoryRefreshTrigger(prev => prev + 1)
            },
          }, abortControllerRef.current.signal)
        } catch (err) {
          if (err.name !== 'AbortError') console.error('[resume stream]', err)
          setIsStreaming(false)
        }
      }
    } catch (e) {
      console.error('[handleLoadConversation] Failed:', e)
    }
  }, [conversationId, inputValue])

  // ── Render ──────────────────────────────────────────────────────────────

  // Auth gating: show login if password-protected and not authenticated
  if (authState.checked && authState.required && !authState.authenticated) {
    return <LoginScreen onLoginSuccess={handleLoginSuccess} />
  }

  // Brief loading spinner while checking auth
  if (!authState.checked) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100vh', background: 'var(--bg-primary, #0d1117)',
      }}>
        <div style={{
          width: 32, height: 32,
          border: '3px solid var(--border-color, #30363d)',
          borderTopColor: 'var(--accent, #58a6ff)',
          borderRadius: '50%', animation: 'tlauthspin 0.7s linear infinite',
        }} />
        <style>{`@keyframes tlauthspin { to { transform: rotate(360deg); } }`}</style>
      </div>
    )
  }

  return (
    <div className={`app ${(showCodePanel && editedFiles.length > 0) ? 'code-mode' : ''}`}>
      <Sidebar
        theme={theme}
        onToggleTheme={toggleTheme}
        onNewChat={handleClear}
        uploadInputRef={uploadInputRef}
        isUploading={isUploading}
        onUploadProject={handleUploadProject}
        taskInstructionsBtnRef={taskInstructionsBtnRef}
        showTaskInstructions={showTaskInstructions}
        onToggleTaskInstructions={() => {
          const next = !showTaskInstructions
          setShowTaskInstructions(next)
          if (next) setHistoryCloseTrigger(prev => prev + 1)
        }}
        systemPrompt={systemPrompt}
        fileTreeRefreshTrigger={fileTreeRefreshTrigger}
        isStreaming={isStreaming}
        onFileClick={handleFileTreeClick}
        conversationId={conversationId}
        onLoadConversation={handleLoadConversation}
        historyRefreshTrigger={historyRefreshTrigger}
        historyCloseTrigger={historyCloseTrigger}
        onDrawerToggle={(open) => { if (open) setShowTaskInstructions(false) }}
        providers={providers}
        selectedProvider={selectedProvider}
        onSelectProvider={(id) => { setSelectedProvider(id); setShowProviderDropdown(false) }}
        showProviderDropdown={showProviderDropdown}
        onToggleProviderDropdown={() => setShowProviderDropdown(!showProviderDropdown)}
        onOpenSettings={() => setShowSettings(true)}
      />

      <main className="main-content">
        <div className="chat-container" ref={chatContainerRef}>
          {messages.length === 0 ? (
            <WelcomeScreen onExampleClick={(text) => setInputValue(text)} />
          ) : (
            <div className="messages-container">
              {messages.map((msg, idx) => (
                <ChatMessage
                  key={idx}
                  message={msg}
                  msgIdx={idx}
                  isLatest={idx === messages.length - 1}
                  isStreaming={isStreaming && idx === messages.length - 1 && msg.role === 'assistant'}
                  onRetry={msg.canRetry ? handleRetry : null}
                  onStopTool={handleStopTool}
                  onLoadConversation={handleLoadConversation}
                  subagentChildIds={subagentChildIds}
                  onForkConversation={handleForkConversation}
                  onForkDismiss={() => setForkWarning(null)}
                  forkWarning={forkWarning}
                  forkClickRef={forkClickRef}
                  messagesLength={messages.length}
                  appIsStreaming={isStreaming}
                  senderLabel={
                    viewMode === 'subagent'
                      ? (msg.role === 'user' ? t('app.mainAgent') : t('app.subagent'))
                      : null
                  }
                />
              ))}
              {/* Thinking bubble: show typing dots whenever streaming but no
                  assistant message exists yet.  ChatMessage already knows how
                  to render the three-dot indicator when isStreaming && !hasContent. */}
              {isStreaming && (messages.length === 0 || messages[messages.length - 1].role !== 'assistant') && (
                <ChatMessage
                  message={{ role: 'assistant', content: '' }}
                  isLatest={true}
                  isStreaming={true}
                  messagesLength={messages.length + 1}
                />
              )}
              <div ref={messagesEndRef} />
            </div>
          )}

          {canContinue && !isStreaming && (
            <div className="continue-container">
              <button className="continue-btn" onClick={handleContinue}>
                <RotateCcw size={18} />
                <span>{t('app.continueGeneration')}</span>
              </button>
            </div>
          )}
        </div>

        {showScrollButton && (
          <button className="scroll-to-bottom-btn" onClick={() => { scrollToBottom(true); setIsUserScrolledUp(false) }}>
            <ArrowDown size={18} />
          </button>
        )}

        {activeConvoWarning && (
          <div className="active-convo-warning">
            <span>{t('app.agentRunning')}</span>
            <button className="active-convo-warning-btn"
              onClick={async () => {
                try {
                  const { active } = await getActiveStreams()
                  if (active && active.length > 0) handleLoadConversation(active[0].conversation_id)
                } catch { /* ignore */ }
              }}>
              {t('app.viewActiveConversation')}
            </button>
          </div>
        )}

        {viewMode === 'subagent' && (
          <div className="subagent-view-bar">
            <span>{isStreaming ? t('app.subagentRunning') : t('app.subagentReadOnly')}</span>
            <button className="subagent-back-btn"
              onClick={() => {
                if (parentConversationId) handleLoadConversation(parentConversationId)
                else handleClear()
              }}>
              {t('app.backToParent')}
            </button>
          </div>
        )}

        {viewMode !== 'subagent' && (
          <ChatInput
            ref={inputRef}
            value={inputValue}
            onChange={setInputValue}
            isStreaming={isStreaming}
            pendingInterrupt={pendingInterrupt}
            messagesCount={messages.length}
            onSend={handleSend}
            onInterruptSend={handleInterruptSend}
            onStop={handleStop}
            onCancelPendingInterrupt={() => {
              setPendingInterrupt(null)
              pendingInterruptRef.current = null
            }}
            onContinueInNewChat={() => handleSend(null,
              'Please use the `continue_as_new_chat` tool to hand off this task to a new chat with fresh context. ' +
              'In your prompt, provide a comprehensive summary of: (1) what has been accomplished so far, ' +
              '(2) what remains to be done, (3) key files and decisions made, and ' +
              '(4) any important context the next agent needs to continue effectively.',
              { tools: 'force_continuation' }
            )}
          />
        )}
      </main>

      {showCodePanel && editedFiles.length > 0 && (
        <CodePanel
          files={editedFiles}
          activeFileId={activeFileId}
          onFileSelect={setActiveFileId}
          onFileClose={handleFileClose}
          onClose={handleCloseCodePanel}
          onRefresh={handleRefreshFiles}
          isLoading={isLoadingFiles}
        />
      )}

      {showTaskInstructions && (
        <div className="task-instructions-drawer" ref={taskInstructionsRef}>
          <div className="history-drawer-header">
            <h3>{t('app.taskInstructions')}</h3>
            <button className="history-drawer-close" onClick={() => setShowTaskInstructions(false)}>
              <X size={16} />
            </button>
          </div>
          <div className="task-instructions-body">
            <p className="task-instructions-desc">
              {t('app.taskInstructionsDesc')}
            </p>
            <textarea
              className="task-instructions-textarea"
              value={systemPrompt}
              onChange={(e) => {
                const value = e.target.value
                setSystemPrompt(value)
                try { localStorage.setItem('systemPrompt', value) } catch { /* ignore */ }
              }}
              placeholder={t('app.taskInstructionsPlaceholder')}
              autoFocus
            />
          </div>
        </div>
      )}

      <SettingsPanel isOpen={showSettings} onClose={() => setShowSettings(false)} />
    </div>
  )
}

export default App
