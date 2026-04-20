import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Send, RotateCcw, Terminal, Search, FileCode, Globe, Sun, Moon, PanelLeftClose, PanelLeft, ChevronDown, History, Upload } from 'lucide-react'
import ChatMessage from './components/ChatMessage'
import WelcomeScreen from './components/WelcomeScreen'
import CodePanel from './components/CodePanel'
import FileTree from './components/FileTree'
import SessionPicker from './components/SessionPicker'
import { streamChat, getProviders, getCurrentSession, uploadWorkspace } from './services/api'

// Debug: log message structure
const DEBUG = true

// Code-related tool names that trigger the code panel (only create/edit, not read)
const CODE_TOOLS = ['write_file', 'edit_file']

/**
 * Check if the raw message list is in a safe state to interrupt.
 * 
 * Safe to interrupt when:
 * - Model is generating reasoning/content (no tool_calls, or tool_calls with no valid IDs yet)
 * - All tool calls have their corresponding tool results
 * 
 * NOT safe to interrupt when:
 * - Model has requested tool calls (valid IDs present) but tool responses haven't all arrived
 * 
 * @param {array} messages - Raw backend format messages
 * @returns {boolean} - True if safe to interrupt
 */
function isInterruptible(messages) {
  if (!messages || messages.length === 0) {
    console.log('[isInterruptible] No messages, safe to interrupt')
    return true
  }
  
  // Check the last message to understand current state
  const lastMessage = messages[messages.length - 1]
  console.log('[isInterruptible] Last message role:', lastMessage.role)
  
  // If the last message is a tool response, we just completed a tool call - SAFE
  if (lastMessage.role === 'tool') {
    // But we need to check if ALL tool calls from the preceding assistant have responses
    // Find the assistant message that made these tool calls
    let assistantIndex = -1
    for (let i = messages.length - 2; i >= 0; i--) {
      if (messages[i].role === 'assistant' && messages[i].tool_calls?.length > 0) {
        assistantIndex = i
        break
      }
    }
    
    if (assistantIndex === -1) {
      console.log('[isInterruptible] Tool response but no assistant with tool_calls found, safe')
      return true
    }
    
    const assistant = messages[assistantIndex]
    const expectedIds = new Set(assistant.tool_calls.filter(tc => tc.id).map(tc => tc.id))
    const receivedIds = new Set()
    
    for (let i = assistantIndex + 1; i < messages.length; i++) {
      if (messages[i].role === 'tool' && messages[i].tool_call_id) {
        receivedIds.add(messages[i].tool_call_id)
      }
    }
    
    const allReceived = [...expectedIds].every(id => receivedIds.has(id))
    console.log('[isInterruptible] Tool responses - expected:', expectedIds.size, 'received:', receivedIds.size, 'allReceived:', allReceived)
    return allReceived
  }
  
  // If the last message is an assistant message
  if (lastMessage.role === 'assistant') {
    // Check if it has tool_calls with valid IDs
    const toolCalls = lastMessage.tool_calls || []
    const validToolCalls = toolCalls.filter(tc => tc.id && tc.id.length > 0)
    
    console.log('[isInterruptible] Assistant message - total tool_calls:', toolCalls.length, 'with valid IDs:', validToolCalls.length)
    
    // If no valid tool call IDs, model is still generating content/reasoning or tool call is being built - SAFE
    // (We'll catch it once the tool call ID arrives)
    if (validToolCalls.length === 0) {
      console.log('[isInterruptible] No valid tool call IDs, safe to interrupt (generating content/reasoning)')
      return true
    }
    
    // Has valid tool calls but no tool responses yet - NOT SAFE
    console.log('[isInterruptible] Has tool calls with IDs but no responses yet, NOT safe')
    return false
  }
  
  // If last message is user or system, safe to interrupt
  console.log('[isInterruptible] Last message is', lastMessage.role, ', safe to interrupt')
  return true
}

// Tools that modify the file system and should trigger a file tree refresh
const FILE_SYSTEM_TOOLS = [
  'write_file',           // Creates or overwrites files
  'edit_file',            // Edits existing files  
  'delete_file',          // Deletes files
  'run_terminal_command'  // Terminal commands may create/modify files
]

function App() {
  const [messages, setMessages] = useState([])
  const [rawMessages, setRawMessages] = useState([])  // Backend format messages for interrupt/resume
  const [inputValue, setInputValue] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const [conversationId, setConversationId] = useState(null)
  const [canContinue, setCanContinue] = useState(false)
  const [pendingInterrupt, setPendingInterrupt] = useState(null)  // Stores interrupt message when waiting for safe point
  
  // Provider state
  const [providers, setProviders] = useState([])
  const [selectedProvider, setSelectedProvider] = useState(null)
  const [showProviderDropdown, setShowProviderDropdown] = useState(false)
  
  // Theme state
  const [theme, setTheme] = useState(() => {
    // Check localStorage or system preference
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('theme')
      if (saved) return saved
      return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
    }
    return 'dark'
  })
  
  // Code panel state
  const [showCodePanel, setShowCodePanel] = useState(false)
  const [editedFiles, setEditedFiles] = useState([])
  const [activeFileId, setActiveFileId] = useState(null)
  const [closedFiles, setClosedFiles] = useState(new Set())
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  
  // Sidebar collapse state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  
  // File tree refresh trigger - increments when file system operations are detected
  const [fileTreeRefreshTrigger, setFileTreeRefreshTrigger] = useState(0)
  
  // Session picker state
  const [showSessionPicker, setShowSessionPicker] = useState(false)
  const [currentSession, setCurrentSession] = useState(null)
  
  // Last request info for retry functionality
  const [lastRequest, setLastRequest] = useState(null)

  // Workspace upload state
  const [isUploading, setIsUploading] = useState(false)
  const uploadInputRef = useRef(null)

  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const abortControllerRef = useRef(null)
  const pendingInterruptRef = useRef(null)  // Ref for closure-safe access to pending interrupt

  // Apply theme to document
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  // Abort ongoing requests when page is refreshed or closed
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (abortControllerRef.current) {
        console.log('[beforeunload] Aborting ongoing request')
        abortControllerRef.current.abort()
      }
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
      // Also abort on component unmount
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [])

  // Fetch available providers on mount
  useEffect(() => {
    async function loadProviders() {
      try {
        const data = await getProviders()
        setProviders(data.providers || [])
        // Set default provider or first available
        const savedProvider = localStorage.getItem('selectedProvider')
        if (savedProvider && data.providers?.find(p => p.id === savedProvider)) {
          setSelectedProvider(savedProvider)
        } else {
          setSelectedProvider(data.default || data.providers?.[0]?.id)
        }
      } catch (error) {
        console.error('Failed to load providers:', error)
        // Fallback
        setProviders([{ id: 'deepseek', name: 'DeepSeek Reasoner', description: 'Default model' }])
        setSelectedProvider('deepseek')
      }
    }
    loadProviders()
  }, [])

  // Save selected provider to localStorage
  useEffect(() => {
    if (selectedProvider) {
      localStorage.setItem('selectedProvider', selectedProvider)
    }
  }, [selectedProvider])

  // Fetch current session on mount
  useEffect(() => {
    async function fetchCurrentSession() {
      try {
        const session = await getCurrentSession()
        if (session.status === 'active') {
          setCurrentSession(session)
        }
      } catch (error) {
        console.error('Failed to fetch current session:', error)
      }
    }
    fetchCurrentSession()
  }, [])

  const handleUploadProject = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setIsUploading(true)
    try {
      await uploadWorkspace(file)
      setFileTreeRefreshTrigger(prev => prev + 1)
    } catch (err) {
      console.error('Upload failed:', err)
      alert('Upload failed: ' + err.message)
    } finally {
      setIsUploading(false)
      if (uploadInputRef.current) uploadInputRef.current.value = ''
    }
  }

  // Handle session loaded from picker
  const handleSessionLoaded = (sessionInfo) => {
    console.log('Session loaded:', sessionInfo)
    setCurrentSession(sessionInfo)
    // Clear current chat state when loading a new session
    setMessages([])
    setRawMessages([])
    setConversationId(null)
    setCanContinue(false)
    setShowCodePanel(false)
    setActiveFileId(null)
    setEditedFiles([])
    setClosedFiles(new Set())
    // Trigger file tree refresh
    setFileTreeRefreshTrigger(prev => prev + 1)
  }

  const toggleTheme = () => {
    setTheme(prev => prev === 'dark' ? 'light' : 'dark')
  }

  // Fetch file diffs from the API
  // Files are never auto-removed - only user can close files
  // But diff content updates each round when API provides new data
  const fetchFileDiffs = useCallback(async () => {
    if (!conversationId) return
    
    setIsLoadingFiles(true)
    try {
      const response = await fetch(`/api/files/diff?conversation_id=${encodeURIComponent(conversationId)}`)
      const data = await response.json()
      
      // Merge API files with existing files - never remove files automatically
      setEditedFiles(prevFiles => {
        // Start with all existing files that aren't closed
        const existingFiles = prevFiles.filter(f => !closedFiles.has(f.id))
        
        if (!data.files || data.files.length === 0) {
          // No new files from API, keep existing files as-is
          return existingFiles
        }
        
        // Filter API files to exclude closed ones
        const apiFiles = data.files.filter(f => !closedFiles.has(f.id))
        
        // Build a map of API files by path for quick lookup
        const apiFilesByPath = new Map(apiFiles.map(f => [f.path, f]))
        
        // Update existing files with new content from API, keep files not in API
        const mergedFiles = existingFiles.map(existingFile => {
          const apiFile = apiFilesByPath.get(existingFile.path)
          if (apiFile) {
            // API has updated content for this file - update it
            // Keep the existing ID to maintain tab state, but update content
            apiFilesByPath.delete(existingFile.path) // Mark as processed
            return {
              ...apiFile,
              id: existingFile.id, // Preserve original ID for tab stability
            }
          }
          // No update from API, keep existing file as-is
          return existingFile
        })
        
        // Add any new files from API that weren't in existing files
        for (const [path, apiFile] of apiFilesByPath) {
          mergedFiles.push(apiFile)
        }
        
        return mergedFiles
      })
      
      // Set active file if none selected
      setActiveFileId(prevActiveId => {
        if (!prevActiveId) {
          // No active file, try to select the first one from API or existing
          const firstApiFile = data.files?.[0]
          if (firstApiFile && !closedFiles.has(firstApiFile.id)) {
            return firstApiFile.id
          }
        }
        return prevActiveId
      })
    } catch (error) {
      console.error('Error fetching file diffs:', error)
    } finally {
      setIsLoadingFiles(false)
    }
  }, [conversationId, closedFiles])

  // Check if any code tools were used and auto-show panel
  useEffect(() => {
    const hasCodeActivity = messages.some(msg => 
      msg.activities?.some(a => 
        a.type === 'tool_call' && CODE_TOOLS.includes(a.name)
      )
    )
    
    // Show panel immediately when code tools are detected
    if (hasCodeActivity) {
      setShowCodePanel(true)
    }
  }, [messages])

  // Track file system operations to trigger file tree refresh
  const lastToolCountRef = useRef(0)
  useEffect(() => {
    // Count file system tool results (completed operations)
    let fsToolCount = 0
    messages.forEach(msg => {
      msg.activities?.forEach(a => {
        if (a.type === 'tool_result') {
          // Find the corresponding tool call
          const toolCall = msg.activities?.find(
            tc => tc.type === 'tool_call' && tc.id === a.tool_call_id
          )
          if (toolCall && FILE_SYSTEM_TOOLS.includes(toolCall.name)) {
            fsToolCount++
          }
        }
        // Also count tool calls directly if they match file system tools
        if (a.type === 'tool_call' && FILE_SYSTEM_TOOLS.includes(a.name)) {
          fsToolCount++
        }
      })
    })
    
    // If count increased, trigger a refresh
    if (fsToolCount > lastToolCountRef.current) {
      lastToolCountRef.current = fsToolCount
      // Debounce the refresh slightly to batch rapid operations
      const timer = setTimeout(() => {
        setFileTreeRefreshTrigger(prev => prev + 1)
      }, 300)
      return () => clearTimeout(timer)
    }
  }, [messages])

  // Poll for file diffs during streaming (real-time updates)
  useEffect(() => {
    if (!conversationId) return
    
    // Fetch immediately when conversationId becomes available and panel is shown
    if (showCodePanel) {
      fetchFileDiffs()
    }
    
    // During streaming, poll every 1.5 seconds for real-time file updates
    if (isStreaming && showCodePanel) {
      const pollInterval = setInterval(() => {
        fetchFileDiffs()
      }, 1500)
      
      return () => clearInterval(pollInterval)
    }
  }, [isStreaming, showCodePanel, conversationId, fetchFileDiffs])

  // Final refresh when streaming stops
  useEffect(() => {
    if (!isStreaming && showCodePanel && conversationId) {
      // Small delay to ensure final files are written
      const timer = setTimeout(fetchFileDiffs, 300)
      return () => clearTimeout(timer)
    }
  }, [isStreaming, showCodePanel, conversationId, fetchFileDiffs])

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  const handleSend = async (interruptMessages = null, overrideMessage = null) => {
    const messageToSend = overrideMessage || inputValue.trim()
    if (!messageToSend) return

    const userMessageText = messageToSend
    
    // If interruptMessages is provided, this is an interrupt/resume scenario
    const isInterrupt = interruptMessages !== null && interruptMessages.length > 0
    
    console.log('[handleSend] Sending message, conversationId:', conversationId, 'isInterrupt:', isInterrupt, 'interruptMessages count:', interruptMessages?.length || 0)

    // ALWAYS abort any existing stream before starting a new one
    // This prevents multiple agents from running in parallel
    if (abortControllerRef.current) {
      console.log('[handleSend] Aborting previous stream before starting new request')
      abortControllerRef.current.abort()
      abortControllerRef.current = null
      // Small delay to ensure abort is processed
      await new Promise(resolve => setTimeout(resolve, 100))
    }

    // Optimistically add user message to UI
    setMessages(prev => [...prev, { role: 'user', content: userMessageText }])
    setInputValue('')
    setIsStreaming(true)
    setCanContinue(false)
    
    // Determine which messages to send to backend
    // Priority: explicit interrupt messages > current rawMessages (for continuation) > null (new conversation)
    let messagesToSend = null
    if (isInterrupt) {
      // Explicit interrupt - use provided messages
      messagesToSend = interruptMessages
    } else if (conversationId && rawMessages.length > 0) {
      // Continuing existing conversation - use current rawMessages
      // This is important after stopping a tool, as rawMessages contains the termination responses
      messagesToSend = rawMessages
    }
    
    // Store request info for potential retry
    setLastRequest({
      message: userMessageText,
      conversationId,
      provider: selectedProvider,
      existingMessages: messagesToSend
    })

    try {
      abortControllerRef.current = new AbortController()
      
      await streamChat(
        userMessageText,
        conversationId,
        {
          onMessages: (frontendMessages, status, data) => {
            // Backend sends full message list - use it directly
            console.log('[onMessages] Received', frontendMessages.length, 'messages, status:', status)
            setMessages(frontendMessages)
            // Store raw messages for potential interrupt
            if (data?.raw_messages) {
              setRawMessages(data.raw_messages)
              
              // Check if there's a pending interrupt and we're now in a safe state
              // Use ref for closure-safe access to pending interrupt
              if (pendingInterruptRef.current && isInterruptible(data.raw_messages)) {
                console.log('[onMessages] Pending interrupt can now be executed')
                const interruptMessage = pendingInterruptRef.current.message
                const messagesForInterrupt = data.raw_messages
                setPendingInterrupt(null)
                pendingInterruptRef.current = null
                
                // Abort current stream and send interrupt
                if (abortControllerRef.current) {
                  abortControllerRef.current.abort()
                }
                
                // Small delay to ensure abort completes, then send with message override
                setTimeout(() => {
                  handleSend(messagesForInterrupt, interruptMessage)
                }, 50)
              }
            }
            // Capture conversation_id early if available
            if (data?.conversation_id) {
              setConversationId(data.conversation_id)
            }
          },
          onDone: (data) => {
            console.log('[onDone] Conversation:', data.conversation_id, 'Status:', data.status)
            setConversationId(data.conversation_id)
            setCanContinue(data.status === 'max_iterations_reached')
            setIsStreaming(false)
            // Use final messages from done event if available
            if (data.messages) {
              setMessages(data.messages)
            }
            // Store final raw messages
            if (data.raw_messages) {
              setRawMessages(data.raw_messages)
            }
          },
          onError: (error) => {
            console.error('[onError]', error)
            // Check if it's a timeout error
            const isTimeout = error.message?.toLowerCase().includes('timeout') ||
                             error.type === 'TimeoutError' ||
                             error.message?.toLowerCase().includes('timed out') ||
                             error.message?.toLowerCase().includes('504') ||
                             error.message?.toLowerCase().includes('gateway timeout')
            setMessages(prev => [
              ...prev,
              { 
                role: 'assistant', 
                content: `Error: ${error.message}`, 
                isError: true,
                isTimeout,
                canRetry: true  // Allow retry for all errors
              }
            ])
            setIsStreaming(false)
          }
        },
        abortControllerRef.current.signal,
        messagesToSend,
        selectedProvider  // Pass the selected provider
      )
    } catch (error) {
      if (error.name !== 'AbortError') {
        console.error('Chat error:', error)
      }
      setIsStreaming(false)
    }
  }

  // Handle sending while streaming (interrupt)
  const handleInterruptSend = () => {
    if (!inputValue.trim()) return
    
    console.log('[handleInterruptSend] isStreaming:', isStreaming, 'rawMessages count:', rawMessages.length)
    
    // If not actually streaming, just do a normal send
    if (!isStreaming) {
      console.log('[handleInterruptSend] Not streaming, doing normal send')
      handleSend()
      return
    }
    
    console.log('[handleInterruptSend] Last 3 rawMessages:', JSON.stringify(rawMessages.slice(-3).map(m => ({
      role: m.role,
      tool_calls: m.tool_calls?.length,
      tool_call_id: m.tool_call_id,
      content_len: m.content?.length
    })), null, 2))
    
    // Check if we're in a safe state to interrupt
    if (isInterruptible(rawMessages)) {
      console.log('[handleInterruptSend] Safe to interrupt, proceeding immediately')
      handleSend(rawMessages)
    } else {
      // Not safe - store the interrupt request and wait for safe point
      console.log('[handleInterruptSend] Not safe to interrupt, waiting for tool calls to complete')
      const interruptData = {
        message: inputValue.trim(),
        rawMessages: rawMessages
      }
      setPendingInterrupt(interruptData)
      pendingInterruptRef.current = interruptData  // Keep ref in sync for closure access
      // Clear input to show the message is "queued"
      setInputValue('')
    }
  }
  

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (isStreaming) {
        handleInterruptSend()
      } else {
        handleSend()
      }
    }
  }

  const handleContinue = async () => {
    if (!conversationId || isStreaming || rawMessages.length === 0) return
    
    setIsStreaming(true)
    setCanContinue(false)

    try {
      abortControllerRef.current = new AbortController()
      
      await streamChat(
        null,
        conversationId,
        {
          onMessages: (frontendMessages, status, data) => {
            setMessages(frontendMessages)
            if (data?.raw_messages) setRawMessages(data.raw_messages)
          },
          onDone: (data) => {
            setConversationId(data.conversation_id)
            setCanContinue(data.status === 'max_iterations_reached')
            setIsStreaming(false)
            if (data.messages) setMessages(data.messages)
            if (data.raw_messages) setRawMessages(data.raw_messages)
          },
          onError: (error) => {
            console.error('[handleContinue] Error:', error)
            setIsStreaming(false)
          }
        },
        abortControllerRef.current.signal,
        rawMessages,
        selectedProvider
      )
    } catch (error) {
      if (error.name !== 'AbortError') {
        console.error('Continue error:', error)
      }
      setIsStreaming(false)
    }
  }

  const handleClear = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    setMessages([])
    setRawMessages([])  // Clear backend format messages too
    setConversationId(null)
    setIsStreaming(false)
    setCanContinue(false)
    setPendingInterrupt(null)  // Clear any pending interrupt
    pendingInterruptRef.current = null
    setShowCodePanel(false)
    setActiveFileId(null)
    setEditedFiles([])
    setClosedFiles(new Set())
    inputRef.current?.focus()
  }

  const handleStop = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    setIsStreaming(false)
    setPendingInterrupt(null)  // Clear any pending interrupt
    pendingInterruptRef.current = null
  }

  // Handle stopping a specific tool call
  const handleStopTool = useCallback((toolInfo) => {
    console.log('[handleStopTool] Stopping tool:', toolInfo.toolName, 'after', toolInfo.elapsedSeconds, 'seconds')
    
    // Abort the stream
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    
    const terminationMessage = `Tool terminated by user after ${formatElapsedTime(toolInfo.elapsedSeconds)}`
    
    // Update rawMessages to add tool response for any pending tool calls
    // This is critical - the API requires tool responses for all tool_calls
    setRawMessages(prev => {
      const newRawMessages = [...prev]
      
      // Find the last assistant message with tool_calls
      let lastAssistantIdx = -1
      for (let i = newRawMessages.length - 1; i >= 0; i--) {
        if (newRawMessages[i].role === 'assistant' && newRawMessages[i].tool_calls?.length > 0) {
          lastAssistantIdx = i
          break
        }
      }
      
      if (lastAssistantIdx === -1) return prev
      
      const assistantMsg = newRawMessages[lastAssistantIdx]
      const toolCalls = assistantMsg.tool_calls || []
      
      // Collect existing tool response IDs
      const existingToolResponseIds = new Set()
      for (let i = lastAssistantIdx + 1; i < newRawMessages.length; i++) {
        if (newRawMessages[i].role === 'tool' && newRawMessages[i].tool_call_id) {
          existingToolResponseIds.add(newRawMessages[i].tool_call_id)
        }
      }
      
      // Add tool response messages for any tool calls that don't have responses yet
      for (const tc of toolCalls) {
        if (tc.id && !existingToolResponseIds.has(tc.id)) {
          newRawMessages.push({
            role: 'tool',
            tool_call_id: tc.id,
            content: terminationMessage
          })
        }
      }
      
      return newRawMessages
    })
    
    // Update frontend display messages
    setMessages(prev => {
      const newMessages = [...prev]
      const lastIdx = newMessages.length - 1
      
      if (lastIdx >= 0 && newMessages[lastIdx].role === 'assistant') {
        // Update the last assistant message to include termination info
        const lastMsg = { ...newMessages[lastIdx] }
        const activities = [...(lastMsg.activities || [])]
        
        // Add a termination result for the stopped tool
        activities.push({
          type: 'tool_result',
          tool_call_id: toolInfo.toolCall.id,
          content: terminationMessage,
          isTerminated: true
        })
        
        lastMsg.activities = activities
        
        // Add a note to the content
        const terminationNote = `\n\n---\n**Tool Stopped:** The ${toolInfo.config?.label || toolInfo.toolName} operation was terminated by user after running for ${formatElapsedTime(toolInfo.elapsedSeconds)}.`
        lastMsg.content = (lastMsg.content || '') + terminationNote
        
        newMessages[lastIdx] = lastMsg
      }
      
      return newMessages
    })
    
    setIsStreaming(false)
    setPendingInterrupt(null)
    pendingInterruptRef.current = null
  }, [])

  // Format elapsed time for display
  const formatElapsedTime = (seconds) => {
    if (seconds < 60) return `${seconds} second${seconds !== 1 ? 's' : ''}`
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (secs === 0) return `${mins} minute${mins !== 1 ? 's' : ''}`
    return `${mins} minute${mins !== 1 ? 's' : ''} ${secs} second${secs !== 1 ? 's' : ''}`
  }

  // Retry the last failed request
  const handleRetry = useCallback(() => {
    if (!lastRequest || isStreaming) return
    
    console.log('[handleRetry] Retrying last request:', lastRequest.message.substring(0, 50))
    
    // Remove the last error message
    setMessages(prev => {
      const lastMsg = prev[prev.length - 1]
      if (lastMsg?.isError) {
        return prev.slice(0, -1)
      }
      return prev
    })
    
    // Re-send the request
    handleSend(lastRequest.existingMessages, lastRequest.message)
  }, [lastRequest, isStreaming])

  const handleFileClose = (fileId) => {
    setClosedFiles(prev => new Set([...prev, fileId]))
    
    // Update editedFiles immediately
    const remaining = editedFiles.filter(f => f.id !== fileId)
    setEditedFiles(remaining)
    
    // If no files left, close the whole panel
    if (remaining.length === 0) {
      setShowCodePanel(false)
      setActiveFileId(null)
      return
    }
    
    // If closing active file, switch to another
    if (fileId === activeFileId) {
      setActiveFileId(remaining[0]?.id || null)
    }
  }

  const handleCloseCodePanel = () => {
    setShowCodePanel(false)
  }

  const handleRefreshFiles = () => {
    fetchFileDiffs()
  }

  // Toggle sidebar collapse
  const toggleSidebar = () => {
    setSidebarCollapsed(prev => !prev)
  }

  // Handle file click from FileTree - open file in code panel
  const handleFileTreeClick = async (filePath) => {
    try {
      // Fetch file content from API
      const response = await fetch(`/api/files/read?file_path=${encodeURIComponent(filePath)}`)
      
      if (!response.ok) {
        console.error('Failed to load file:', response.statusText)
        return
      }
      
      const data = await response.json()
      
      // Create a file entry for the code panel (view mode - no diff)
      const fileEntry = {
        id: `view:${filePath}`,
        path: filePath,
        isNew: false,
        hasChanges: false,
        isViewOnly: true,
        lines: data.content.split('\n').map((content, idx) => ({
          lineNumber: idx + 1,
          content,
          type: null // No diff highlighting for viewed files
        }))
      }
      
      // Add to edited files if not already there, or update existing
      setEditedFiles(prev => {
        const existing = prev.find(f => f.id === fileEntry.id)
        if (existing) {
          return prev.map(f => f.id === fileEntry.id ? fileEntry : f)
        }
        return [...prev, fileEntry]
      })
      
      // Set as active and show panel
      setActiveFileId(fileEntry.id)
      setShowCodePanel(true)
      
      // Remove from closed files set if it was there
      setClosedFiles(prev => {
        const next = new Set(prev)
        next.delete(fileEntry.id)
        return next
      })
      
    } catch (error) {
      console.error('Error loading file:', error)
    }
  }

  return (
    <div className={`app ${showCodePanel ? 'code-mode' : ''} ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      {/* Sidebar */}
      <aside className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          {!sidebarCollapsed && (
            <div className="logo">
              <img src="/assets/logo.png" alt="1001 Wills AI Lab" className="logo-image" />
              <span className="logo-text">AuroraCoder</span>
            </div>
          )}
          <div className="sidebar-header-actions">
            <button 
              className="theme-toggle" 
              onClick={toggleTheme}
              title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            >
              {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button 
              className="sidebar-toggle" 
              onClick={toggleSidebar}
              title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {sidebarCollapsed ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
            </button>
          </div>
        </div>
        
        {!sidebarCollapsed && (
          <>
            <div className="sidebar-actions">
              <button className="new-chat-btn" onClick={handleClear}>
                <span>+ New Chat</span>
              </button>
              <button 
                className="load-session-btn" 
                onClick={() => setShowSessionPicker(true)}
                title="Load a previous session"
              >
                <History size={16} />
                <span>Load Session</span>
              </button>
              <button
                className="load-session-btn"
                onClick={() => uploadInputRef.current?.click()}
                disabled={isUploading}
                title="Upload a zip file into the workspace"
              >
                <Upload size={16} />
                <span>{isUploading ? 'Uploading...' : 'Upload Project'}</span>
              </button>
              <input
                ref={uploadInputRef}
                type="file"
                accept=".zip"
                style={{ display: 'none' }}
                onChange={handleUploadProject}
              />
            </div>

            {/* Current Session Info */}
            {currentSession && (
              <div className="current-session-info">
                <span className="current-session-label">Session:</span>
                <span className="current-session-id" title={currentSession.session_name}>
                  {currentSession.session_id}
                </span>
              </div>
            )}

            {/* File Tree - Workspace Explorer */}
            <div className="sidebar-section file-tree-section">
              <FileTree 
                onFileClick={handleFileTreeClick}
                isStreaming={isStreaming}
                refreshTrigger={fileTreeRefreshTrigger}
              />
            </div>

            <div className="sidebar-section">
              <h3 className="sidebar-title">Capabilities</h3>
              <div className="capability-list">
                <div className="capability-item">
                  <Search size={16} />
                  <span>Web Search</span>
                </div>
                <div className="capability-item">
                  <Globe size={16} />
                  <span>Browse URLs</span>
                </div>
                <div className="capability-item">
                  <FileCode size={16} />
                  <span>Code Analysis</span>
                </div>
                <div className="capability-item">
                  <Terminal size={16} />
                  <span>Execute Commands</span>
                </div>
              </div>
            </div>

            <div className="sidebar-footer">
              <div className="model-selector">
                <span className="model-label">Model</span>
                <div className="provider-dropdown-container">
                  <button 
                    className="provider-dropdown-btn"
                    onClick={() => setShowProviderDropdown(!showProviderDropdown)}
                    disabled={isStreaming}
                  >
                    <span className="provider-name">
                      {providers.find(p => p.id === selectedProvider)?.name || 'Select Model'}
                    </span>
                    <ChevronDown size={16} className={showProviderDropdown ? 'rotated' : ''} />
                  </button>
                  {showProviderDropdown && (
                    <div className="provider-dropdown-menu">
                      {providers.map(provider => (
                        <button
                          key={provider.id}
                          className={`provider-option ${provider.id === selectedProvider ? 'selected' : ''}`}
                          onClick={() => {
                            setSelectedProvider(provider.id)
                            setShowProviderDropdown(false)
                          }}
                        >
                          <div className="provider-option-name">{provider.name}</div>
                          <div className="provider-option-desc">{provider.description}</div>
                          {provider.supports_thinking && (
                            <span className="provider-badge">Thinking</span>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </div>
          </>
        )}
      </aside>

      {/* Main Chat Area */}
      <main className="main-content">
        <div className="chat-container">
          {messages.length === 0 ? (
            <WelcomeScreen onExampleClick={(text) => setInputValue(text)} />
          ) : (
            <div className="messages-container">
              {messages.map((msg, idx) => (
                <ChatMessage 
                  key={idx} 
                  message={msg} 
                  isLatest={idx === messages.length - 1}
                  isStreaming={isStreaming && idx === messages.length - 1 && msg.role === 'assistant'}
                  onRetry={msg.canRetry ? handleRetry : null}
                  onStopTool={handleStopTool}
                />
              ))}
              
              <div ref={messagesEndRef} />
            </div>
          )}

          {/* Continue Button */}
          {canContinue && !isStreaming && (
            <div className="continue-container">
              <button className="continue-btn" onClick={handleContinue}>
                <RotateCcw size={18} />
                <span>Continue Generation</span>
              </button>
            </div>
          )}
        </div>

        {/* Input Area */}
        <div className="input-container">
          <div className="input-wrapper">
            <textarea
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                pendingInterrupt 
                  ? "Interrupt queued - waiting for safe point..."
                  : isStreaming 
                    ? "Type to interrupt and redirect the agent..." 
                    : "Ask me anything..."
              }
              rows={1}
              disabled={!!pendingInterrupt}
              className={`chat-input ${isStreaming ? 'streaming-mode' : ''} ${pendingInterrupt ? 'pending-interrupt' : ''}`}
            />
            <div className="input-actions">
              {pendingInterrupt ? (
                <button 
                  className="send-btn pending-btn" 
                  onClick={() => {
                    setPendingInterrupt(null)
                    pendingInterruptRef.current = null
                  }}
                  title="Cancel pending interrupt"
                >
                  <RotateCcw size={18} />
                </button>
              ) : isStreaming && !inputValue.trim() ? (
                <button className="stop-btn" onClick={handleStop} title="Stop generation">
                  <div className="stop-icon" />
                </button>
              ) : isStreaming && inputValue.trim() ? (
                <button 
                  className="send-btn interrupt-btn" 
                  onClick={handleInterruptSend}
                  title="Send and interrupt current generation"
                >
                  <Send size={20} />
                </button>
              ) : (
                <button 
                  className="send-btn" 
                  onClick={() => handleSend()}
                  disabled={!inputValue.trim()}
                >
                  <Send size={20} />
                </button>
              )}
            </div>
          </div>
          <p className="input-hint">
            {pendingInterrupt
              ? `Interrupt queued: "${pendingInterrupt.message.substring(0, 50)}${pendingInterrupt.message.length > 50 ? '...' : ''}" - Waiting for tool calls to complete...`
              : isStreaming 
                ? "Type a message to interrupt and redirect the agent with your new instructions."
                : "AuroraCoder can search, browse, write code, and execute commands."
            }
          </p>
        </div>
      </main>

      {/* Code Panel - Shows when code tools are used */}
      {showCodePanel && (
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

      {/* Session Picker Modal */}
      <SessionPicker
        isOpen={showSessionPicker}
        onClose={() => setShowSessionPicker(false)}
        onSessionLoaded={handleSessionLoaded}
        currentSessionId={currentSession?.session_id}
      />
    </div>
  )
}

export default App
