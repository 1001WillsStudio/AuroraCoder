import { useState, useEffect, useCallback, useRef } from 'react'
import { CODE_TOOLS, FILE_SYSTEM_TOOLS } from '../utils/streamUtils'

/**
 * IMPORTANT — closedFiles closure trap:
 * fetchFileDiffs is an async useCallback whose closure captures
 * closedFiles at definition time.  When the code‑result effect fires
 * it first calls setClosedFiles(new Set()) and then fetchFileDiffs() —
 * but fetchFileDiffs still has the *previous* closedFiles.  We MUST
 * read closedFiles through a ref so the async handler always sees the
 * latest state that was applied before the HTTP response arrives.
 */
import { uploadWorkspace } from '../services/api'

/**
 * Manages all file-related state: edited files (code panel), file tree refresh
 * triggers, file diff polling, and auto-show/auto-close of the code panel.
 */
export function useFileTracking(conversationId, messages, isStreaming) {
  const [editedFiles, setEditedFiles] = useState([])
  const [activeFileId, setActiveFileId] = useState(null)
  const [closedFiles, setClosedFiles] = useState(new Set())
  const closedFilesRef = useRef(closedFiles)
  closedFilesRef.current = closedFiles  // keep in sync on every render
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [showCodePanel, setShowCodePanel] = useState(false)
  const [fileTreeRefreshTrigger, setFileTreeRefreshTrigger] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const uploadInputRef = useRef(null)
  const diffAbortRef = useRef(null)

  // ── fetchFileDiffs ──────────────────────────────────────────────────────

  const fetchFileDiffs = useCallback(async () => {
    if (!conversationId) return

    if (diffAbortRef.current) diffAbortRef.current.abort()
    const controller = new AbortController()
    diffAbortRef.current = controller

    setIsLoadingFiles(true)
    try {
      const response = await fetch(
        `/api/files/diff?conversation_id=${encodeURIComponent(conversationId)}`,
        { signal: controller.signal }
      )
      const data = await response.json()

      const cf = closedFilesRef.current  // ALWAYS latest — see comment at top of file

      setEditedFiles(prevFiles => {
        const existingFiles = prevFiles.filter(f => !cf.has(f.id))

        if (!data.files || data.files.length === 0) {
          return existingFiles
        }

        const apiFiles = data.files.filter(f => !cf.has(f.id))
        const apiFilesByPath = new Map(apiFiles.map(f => [f.path, f]))

        const mergedFiles = existingFiles.map(existingFile => {
          const apiFile = apiFilesByPath.get(existingFile.path)
          if (apiFile) {
            apiFilesByPath.delete(existingFile.path)
            return { ...apiFile, id: existingFile.id }
          }
          return existingFile
        })

        for (const [, apiFile] of apiFilesByPath) {
          mergedFiles.push(apiFile)
        }

        return mergedFiles
      })

      setActiveFileId(prevActiveId => {
        if (!prevActiveId && data.files?.[0] && !cf.has(data.files[0].id)) {
          return data.files[0].id
        }
        return prevActiveId
      })
    } catch (error) {
      if (error.name === 'AbortError') return
      console.error('Error fetching file diffs:', error)
    } finally {
      setIsLoadingFiles(false)
    }
  }, [conversationId])  // closedFiles read via ref — always latest

  // ── Count tool results for code tools and FS tools ─────────────────────

  function _countToolResults(msgs) {
    let codeResults = 0, codeCalls = 0, fsResults = 0
    for (const msg of msgs) {
      for (const a of msg.activities || []) {
        if (a.type === 'tool_call' && CODE_TOOLS.includes(a.name)) codeCalls++
        if (a.type === 'tool_result') {
          const tc = msg.activities?.find(t => t.type === 'tool_call' && t.id === a.tool_call_id)
          if (!tc) continue
          if (CODE_TOOLS.includes(tc.name)) codeResults++
          if (FILE_SYSTEM_TOOLS.includes(tc.name)) fsResults++
        }
      }
    }
    return { codeCalls, codeResults, fsResults }
  }

  // Reset counters when conversation changes (new chat or loaded conversation)
  const lastCodeCallCountRef = useRef(0)
  const lastCodeResultCountRef = useRef(0)
  const lastFsResultCountRef = useRef(0)
  const prevConversationIdRef = useRef(conversationId)

  useEffect(() => {
    if (conversationId !== prevConversationIdRef.current) {
      prevConversationIdRef.current = conversationId
      lastCodeCallCountRef.current = 0
      lastCodeResultCountRef.current = 0
      lastFsResultCountRef.current = 0
    }
  }, [conversationId])

  // ── Open code panel when a code tool call first appears ────────────────

  useEffect(() => {
    const { codeCalls } = _countToolResults(messages)
    if (codeCalls > lastCodeCallCountRef.current) {
      lastCodeCallCountRef.current = codeCalls
      setShowCodePanel(true)
    } else {
      lastCodeCallCountRef.current = codeCalls
    }
  }, [messages])

  // ── Fetch diffs only when a code tool RESULT arrives ───────────────────

  useEffect(() => {
    const { codeResults } = _countToolResults(messages)
    if (codeResults > lastCodeResultCountRef.current) {
      lastCodeResultCountRef.current = codeResults
      setClosedFiles(new Set())
      setShowCodePanel(true)
      if (conversationId) fetchFileDiffs()
    } else {
      lastCodeResultCountRef.current = codeResults
    }
  }, [messages, conversationId, fetchFileDiffs])

  // ── Refresh file tree only when an FS tool RESULT arrives ──────────────

  useEffect(() => {
    const { fsResults } = _countToolResults(messages)
    if (fsResults > lastFsResultCountRef.current) {
      lastFsResultCountRef.current = fsResults
      setFileTreeRefreshTrigger(prev => prev + 1)
    } else {
      lastFsResultCountRef.current = fsResults
    }
  }, [messages])

  // ── Retry diff fetch if panel should be visible but has no files ───────
  // Handles the race where tool_result arrives in the SSE stream before the
  // file system change is visible to the diff endpoint.

  const retryTimerRef = useRef(null)
  useEffect(() => {
    if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
    if (showCodePanel && editedFiles.length === 0 && conversationId) {
      retryTimerRef.current = setTimeout(fetchFileDiffs, 800)
      return () => clearTimeout(retryTimerRef.current)
    }
  }, [showCodePanel, editedFiles.length, conversationId, fetchFileDiffs])

  // ── Auto-close code panel when no files remain ──────────────────────────

  useEffect(() => {
    if (!isStreaming && editedFiles.length === 0 && showCodePanel) {
      setShowCodePanel(false)
      setActiveFileId(null)
    }
  }, [isStreaming, editedFiles.length, showCodePanel])

  // ── Handlers ────────────────────────────────────────────────────────────

  const handleFileClose = useCallback((fileId) => {
    setClosedFiles(prev => new Set([...prev, fileId]))
    setEditedFiles(prev => {
      const remaining = prev.filter(f => f.id !== fileId)
      if (fileId === activeFileId) {
        setActiveFileId(remaining[0]?.id || null)
      }
      return remaining
    })
  }, [activeFileId])

  const handleCloseCodePanel = useCallback(() => {
    setShowCodePanel(false)
  }, [])

  const handleRefreshFiles = useCallback(() => {
    fetchFileDiffs()
  }, [fetchFileDiffs])

  const handleFileTreeClick = useCallback(async (filePath) => {
    try {
      const response = await fetch(`/api/files/read?file_path=${encodeURIComponent(filePath)}`)
      if (!response.ok) {
        console.error('Failed to load file:', response.statusText)
        return
      }

      const data = await response.json()

      const fileEntry = {
        id: `view:${filePath}`,
        path: filePath,
        isNew: false,
        hasChanges: false,
        isViewOnly: true,
        lines: data.content.split('\n').map((content, idx) => ({
          lineNumber: idx + 1,
          content,
          type: null
        }))
      }

      setEditedFiles(prev => {
        const existing = prev.find(f => f.id === fileEntry.id)
        if (existing) {
          return prev.map(f => f.id === fileEntry.id ? fileEntry : f)
        }
        return [...prev, fileEntry]
      })

      setActiveFileId(fileEntry.id)
      setShowCodePanel(true)

      setClosedFiles(prev => {
        const next = new Set(prev)
        next.delete(fileEntry.id)
        return next
      })
    } catch (error) {
      console.error('Error loading file:', error)
    }
  }, [])

  const handleUploadProject = useCallback(async (e) => {
    const fileList = e.target.files
    if (!fileList || fileList.length === 0) return
    setIsUploading(true)
    try {
      await uploadWorkspace(fileList)
      setFileTreeRefreshTrigger(prev => prev + 1)
    } catch (err) {
      console.error('Upload failed:', err)
      alert('Upload failed: ' + err.message)
    } finally {
      setIsUploading(false)
      if (uploadInputRef.current) uploadInputRef.current.value = ''
    }
  }, [])

  return {
    // State
    editedFiles,
    activeFileId,
    setActiveFileId,     // CodePanel needs to set active tab
    showCodePanel,
    setShowCodePanel,    // App may need this for layout toggle
    isLoadingFiles,
    fileTreeRefreshTrigger,
    isUploading,
    uploadInputRef,
    // Handlers
    handleFileClose,
    handleCloseCodePanel,
    handleRefreshFiles,
    handleFileTreeClick,
    handleUploadProject,
    // Actions
    setEditedFiles,      // handleStopTool needs to clear files
    setClosedFiles,      // handleClear / handleLoadConversation need to clear
    setFileTreeRefreshTrigger,  // handleClear / handleSessionLoaded need to refresh
  }
}
