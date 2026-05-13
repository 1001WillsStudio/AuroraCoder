import { useState, useEffect, useCallback, useRef } from 'react'
import { CODE_TOOLS, FILE_SYSTEM_TOOLS } from '../utils/streamUtils'

/**
 * Manages all file-related state: edited files (code panel), file tree refresh
 * triggers, file diff polling, and auto-show/auto-close of the code panel.
 */
export function useFileTracking(conversationId, messages, isStreaming) {
  const [editedFiles, setEditedFiles] = useState([])
  const [activeFileId, setActiveFileId] = useState(null)
  const [closedFiles, setClosedFiles] = useState(new Set())
  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [showCodePanel, setShowCodePanel] = useState(false)
  const [fileTreeRefreshTrigger, setFileTreeRefreshTrigger] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const uploadInputRef = useRef(null)

  // ── fetchFileDiffs ──────────────────────────────────────────────────────

  const fetchFileDiffs = useCallback(async () => {
    if (!conversationId) return

    setIsLoadingFiles(true)
    try {
      const response = await fetch(`/api/files/diff?conversation_id=${encodeURIComponent(conversationId)}`)
      const data = await response.json()

      setEditedFiles(prevFiles => {
        const existingFiles = prevFiles.filter(f => !closedFiles.has(f.id))

        if (!data.files || data.files.length === 0) {
          return existingFiles
        }

        const apiFiles = data.files.filter(f => !closedFiles.has(f.id))
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
        if (!prevActiveId && data.files?.[0] && !closedFiles.has(data.files[0].id)) {
          return data.files[0].id
        }
        return prevActiveId
      })
    } catch (error) {
      console.error('Error fetching file diffs:', error)
    } finally {
      setIsLoadingFiles(false)
    }
  }, [conversationId, closedFiles])

  // ── Auto-show code panel when code tools are detected ───────────────────

  useEffect(() => {
    const hasCodeActivity = messages.some(msg =>
      msg.activities?.some(a =>
        a.type === 'tool_call' && CODE_TOOLS.includes(a.name)
      )
    )
    if (hasCodeActivity) {
      setShowCodePanel(true)
    }
  }, [messages])

  // ── File tree refresh tracking ──────────────────────────────────────────

  const lastToolCountRef = useRef(0)
  useEffect(() => {
    let fsToolCount = 0
    messages.forEach(msg => {
      msg.activities?.forEach(a => {
        if (a.type === 'tool_result') {
          const toolCall = msg.activities?.find(
            tc => tc.type === 'tool_call' && tc.id === a.tool_call_id
          )
          if (toolCall && FILE_SYSTEM_TOOLS.includes(toolCall.name)) {
            fsToolCount++
          }
        }
        if (a.type === 'tool_call' && FILE_SYSTEM_TOOLS.includes(a.name)) {
          fsToolCount++
        }
      })
    })

    if (fsToolCount > lastToolCountRef.current) {
      lastToolCountRef.current = fsToolCount
      const timer = setTimeout(() => {
        setFileTreeRefreshTrigger(prev => prev + 1)
      }, 300)
      return () => clearTimeout(timer)
    }
  }, [messages])

  // ── File diff polling during streaming ──────────────────────────────────

  useEffect(() => {
    if (!conversationId) return
    if (showCodePanel) {
      fetchFileDiffs()
    }
    if (isStreaming && showCodePanel) {
      const pollInterval = setInterval(() => {
        fetchFileDiffs()
      }, 1500)
      return () => clearInterval(pollInterval)
    }
  }, [isStreaming, showCodePanel, conversationId, fetchFileDiffs])

  // ── Final refresh after streaming stops ─────────────────────────────────

  useEffect(() => {
    if (!isStreaming && showCodePanel && conversationId) {
      const timer = setTimeout(fetchFileDiffs, 300)
      return () => clearTimeout(timer)
    }
  }, [isStreaming, showCodePanel, conversationId, fetchFileDiffs])

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
      const { uploadWorkspace } = await import('../services/api')
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
