import React, { useState, useRef, useEffect, useCallback } from 'react'
import { STATUS } from './constants'
import { useAutoScroll } from './hooks/useAutoScroll'
import { RotateCcw, X, ArrowDown } from 'lucide-react'
import ChatMessage from './components/ChatMessage'
import ChatInput from './components/ChatInput'
import LoginScreen from './components/LoginScreen'
import Sidebar from './components/Sidebar'
import WelcomeScreen from './components/WelcomeScreen'
import SettingsPanel from './components/SettingsPanel'
import { streamChat, getProviders, cancelConversation, getConversation, getActiveStreams, resumeStream, getTaskInstruction, setTaskInstruction, getInstanceInfo } from './services/api'
import { isInterruptible, TASK_MARKER_START, TASK_MARKER_END } from './utils/streamUtils'
import { checkAuth, isAuthRequired } from './utils/auth.js'
import CodePanel from './components/CodePanel'
import { createStreamCallbacks } from './hooks/createStreamCallbacks'
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

  // ── Auth check (runs once) ──
  useEffect(() => {
    async function check() {
      const required = await isAuthRequired()
      if (!required) { setAuthState({ checked: true, required: false, authenticated: true }); return }
      let authed = false
      try { authed = await checkAuth() } catch { authed = false }
      setAuthState({ checked: true, required: true, authenticated: authed })
    }
    check()
  }, [])

  const handleLoginSuccess = useCallback(() => {
    setAuthState({ checked: true, required: true, authenticated: true })
  }, [])

  const [messages, setMessages] = useState([])
  const [rawMessages, setRawMessages] = useState([])
  const [inputValue, setInputValue] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [conversationId, setConversationId] = useState(null)
  const [canContinue, setCanContinue] = useState(false)
  const [pendingInterrupt, setPendingInterrupt] = useState(null)
  const [sseReceived, setSseReceived] = useState(false)
  
  // Provider state
  const [providers, setProviders] = useState([])
  const [providersLoading, setProvidersLoading] = useState(true)
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
    const [systemPrompt, setSystemPrompt] = useState('')
    const [instanceType, setInstanceType] = useState('normal') // "normal" or "gpu"
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
  const inputRef = useRef(null)
    const inputValueRef = useRef(inputValue)
    inputValueRef.current = inputValue
  const abortControllerRef = useRef(null)
  const pendingInterruptRef = useRef(null)
  const conversationIdRef = useRef(null)
  const continuationNavigatedRef = useRef(new Set())
  const forkClickRef = useRef({ time: 0, idx: -1 })

  // ── Effects ─────────────────────────────────────────────────────────────


  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  // ── Load task instruction from server (not localStorage — follows the instance, not the port) ──
  useEffect(() => {
    getTaskInstruction().then(data => {
      if (data?.instruction) setSystemPrompt(data.instruction)
    }).catch(() => { /* ignore — server may not be ready yet */ })
  }, [])

  // ── Fetch instance type and set browser tab title ──
  useEffect(() => {
    getInstanceInfo().then(data => {
      setInstanceType(data.type)
      document.title = data.type === 'gpu' ? 'AuroraCoder GPU' : 'AuroraCoder'
    }).catch(() => { /* ignore — keep defaults */ })
  }, [])

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
    let retryTimer = null
    let cancelled = false

    async function loadProviders() {
      try {
        const data = await getProviders()
        if (cancelled) return
        setProviders(data.providers || [])
        setProvidersLoading(false)
        const savedProvider = localStorage.getItem('selectedProvider')
        if (savedProvider && data.providers?.find(p => p.id === savedProvider)) {
          setSelectedProvider(savedProvider)
        } else {
          setSelectedProvider(data.default || data.providers?.[0]?.id)
        }
      } catch {
        if (cancelled) return
        // Keep providersLoading=true so the dropdown shows "Loading…"
        // rather than a stale fallback entry.  Only set selectedProvider
        // so the button label is meaningful while we wait.
        const savedProvider = localStorage.getItem('selectedProvider')
        if (savedProvider) {
          setSelectedProvider(prev => prev || savedProvider)
        } else {
          setSelectedProvider(prev => prev || 'deepseek')
        }
        // Retry every 3 s until the backend becomes available
        retryTimer = setTimeout(loadProviders, 3000)
      }
    }
    loadProviders()

    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
    }
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

  const { chatContainerRef, resetToFollowing, showScrollButton } = useAutoScroll(messages, isStreaming)

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
      log('abort done')
    }

    // When interrupting, call cancelConversation.  The gateway cancels
    // the old stream (triggering final persistence in its finally block)
    // and returns the latest raw_messages — including content that was
    // only delivered via deltas and never made it into the frontend's
    // rawMessages state.  Without this the backend receives a stale
    // history with the assistant's ``content`` still at ``""``.
    let latestRawMessages = null
    if (isInterrupt && conversationId) {
      try {
        const result = await cancelConversation(conversationId)
        if (result?.raw_messages?.length > 0) {
          latestRawMessages = result.raw_messages
        }
        log('cancel returned latest messages')
      } catch (e) {
        console.warn('[handleSend] Cancel fetch failed, using stale messages:', e.message)
      }
    }

    log('setState batch (messages, streaming, etc.)')
    setMessages(prev => [...prev, { role: 'user', content: userMessageText }, { role: 'assistant', content: '' }])
    setInputValue('')
    setIsStreaming(true)
    setSseReceived(false)
    setCanContinue(false)
    setHistoryRefreshTrigger(prev => prev + 1)
    resetToFollowing()

    let messagesToSend = null
    if (isInterrupt) {
      messagesToSend = latestRawMessages || interruptMessages
      if (latestRawMessages) {
        setRawMessages(latestRawMessages)
      }
    } else if (conversationId && rawMessages.length > 0) {
      messagesToSend = rawMessages
    }

    setLastRequest({ message: userMessageText, conversationId, provider: selectedProvider, existingMessages: messagesToSend })

    log('about to call streamChat()')
    try {
      abortControllerRef.current = new AbortController()
      const callbacks = createStreamCallbacks({
        setMessages, setRawMessages, setConversationId, setCanContinue,
        setIsStreaming, setHistoryRefreshTrigger, setSubagentChildIds,
        handleSend, handleLoadConversation,
        pendingInterruptRef, continuationNavigatedRef, abortControllerRef,
        withInterrupt: true,
        withRetry: true,
        onFirstSse: () => setSseReceived(true),
        onStreamEnd: () => { setPendingInterrupt(null); pendingInterruptRef.current = null },
        onInterruptFired: () => setPendingInterrupt(null),
        ensureAssistantTail: true,
      })
      await streamChat(apiMessage, conversationId, callbacks, abortControllerRef.current.signal, messagesToSend, selectedProvider, options)
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
    setSseReceived(false)
    setCanContinue(false)
    try {
      abortControllerRef.current = new AbortController()
      const callbacks = createStreamCallbacks({
        setMessages, setRawMessages, setConversationId, setCanContinue,
        setIsStreaming, setHistoryRefreshTrigger, setSubagentChildIds,
        handleSend: null, handleLoadConversation,
        pendingInterruptRef: null, continuationNavigatedRef, abortControllerRef: null,
        withInterrupt: false,
        withRetry: false,
        onFirstSse: () => setSseReceived(true),
        ensureAssistantTail: true,
      })
      await streamChat(null, conversationId, callbacks, abortControllerRef.current.signal, rawMessages, selectedProvider)
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
    if (inputValueRef.current.trim()) draftInputsRef.current.set(conversationId ?? '__new__', inputValueRef.current)
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
  }, [rawMessages, messages, conversationId, findForkPoint])

  const handleForkDismiss = useCallback(() => setForkWarning(null), [])

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
        const result = await cancelConversation(conversationId)
        if (result?.raw_messages?.length > 0) {
          setRawMessages(result.raw_messages)
        }
      } catch { /* ignore — best-effort cancel */ }
    }

    setIsStreaming(false)
    setPendingInterrupt(null)
    pendingInterruptRef.current = null
    setActiveConvoWarning(false)
    setHistoryRefreshTrigger(prev => prev + 1)
  }

  const handleStopTool = useCallback(async () => {
    if (abortControllerRef.current) abortControllerRef.current.abort()
    const cid = conversationIdRef.current
    if (cid) {
      try {
        await cancelConversation(cid)
      } catch { /* ignore — best-effort cancel */ }
    }

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
  }, [lastRequest, isStreaming, selectedProvider])

  const handleLoadConversation = useCallback(async (targetConversationId) => {
    if (inputValueRef.current.trim()) draftInputsRef.current.set(conversationId ?? '__new__', inputValueRef.current)
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    let isSubagent = false // declared here so the finally block can read it
    try {
      const conv = await getConversation(targetConversationId)
      isSubagent = conv.type === 'subagent'
      setConversationId(targetConversationId)
      setRawMessages(conv.messages || [])
      setMessages(conv.frontend_messages || [])
      setCanContinue(!isSubagent && conv.status === STATUS.MAX_ITERATIONS_REACHED)
      setIsStreaming(false)
      setActiveConvoWarning(false)
      setEditedFiles([])
      setClosedFiles(new Set())
      setViewMode(isSubagent ? 'subagent' : 'main')
      setParentConversationId(isSubagent ? conv.parent_id : null)
      const draft = isSubagent ? '' : (draftInputsRef.current.get(targetConversationId) || '')
      setInputValue(draft)
      if (conv.status === STATUS.RUNNING) {
        setIsStreaming(true)
        setSseReceived(false)
        abortControllerRef.current = new AbortController()
        try {
          const callbacks = createStreamCallbacks({
            setMessages, setRawMessages, setConversationId, setCanContinue,
            setIsStreaming, setHistoryRefreshTrigger, setSubagentChildIds,
            handleSend: null, handleLoadConversation,
            pendingInterruptRef: null, continuationNavigatedRef, abortControllerRef: null,
            withInterrupt: false,
            withRetry: false,
            onFirstSse: () => setSseReceived(true),
            onStreamEnd: () => { setPendingInterrupt(null); pendingInterruptRef.current = null },
            ensureAssistantTail: true,
            overrides: {
              onDone: (data) => {
                setIsStreaming(false)
                if (data.messages) setMessages(data.messages)
                if (data.raw_messages) setRawMessages(data.raw_messages)
                setCanContinue(!isSubagent && data.status === STATUS.MAX_ITERATIONS_REACHED)
                setHistoryRefreshTrigger(prev => prev + 1)
              },
            },
          })
          await resumeStream(targetConversationId, callbacks, abortControllerRef.current.signal)
        } catch (err) {
          if (err.name !== 'AbortError') console.error('[resume stream]', err)
          setIsStreaming(false)
        }
      }
    } catch (e) {
      console.error('[handleLoadConversation] Failed:', e)
    } finally {
      // Scroll to bottom after loading a non-subagent conversation
      if (!isSubagent) {
        // Schedule after DOM commit so the container has the new content
        requestAnimationFrame(() => resetToFollowing())
      }
    }
  }, [conversationId, resetToFollowing])

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
        providersLoading={providersLoading}
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
                  onForkDismiss={handleForkDismiss}
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
          <button className="scroll-to-bottom-btn" onClick={resetToFollowing}>
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
            onContinueInNewChat={() => {
              const userText = inputValue.trim()
              const standardCommand = 'Please use the `continue_as_new_chat` tool to hand off this task to a new chat with fresh context. ' +
                'In your prompt, provide a comprehensive summary of: (1) what has been accomplished so far, ' +
                '(2) what remains to be done, (3) key files and decisions made, and ' +
                '(4) any important context the next agent needs to continue effectively.'
              const combinedMessage = userText
                ? `${userText}\n\n---\n\n${standardCommand}`
                : standardCommand
              handleSend(null, combinedMessage, { tools: 'force_continuation' })
            }}
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
                setTaskInstruction(value).catch(() => { /* ignore — best-effort server save */ })
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
