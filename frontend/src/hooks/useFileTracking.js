import { useState, useEffect, useCallback, useRef } from 'react'
import { CODE_TOOLS, FILE_SYSTEM_TOOLS } from '../utils/streamUtils'
import { getFileDiffs, readWorkspaceFile, uploadWorkspace } from '../services/api'
import { closePanelFilesForDeletedPath, applyViewOnlyReadResults } from '../utils/panelFiles'

// ── Pure merge helper ─────────────────────────────────────────────────────

/**
 * Merge API diff files with the existing panel file list, preserving
 * files the user didn't close and updating matched files with fresh diff
 * data.
 */
function mergePanelFiles(prevFiles, apiFiles, closedFiles) {
  const existing = prevFiles.filter(f => !closedFiles.has(f.id))
  if (!apiFiles || apiFiles.length === 0) return existing

  const byPath = new Map(
    apiFiles.filter(f => !closedFiles.has(f.id)).map(f => [f.path, f])
  )
  const merged = existing.map(ef => {
    const af = byPath.get(ef.path)
    if (af) { byPath.delete(ef.path); return { ...af, id: ef.id } }
    return ef
  })
  for (const af of byPath.values()) merged.push(af)
  return merged
}

// ── Hook ───────────────────────────────────────────────────────────────────

export function useFileTracking(conversationId, messages, isStreaming) {
  const [editedFiles, setEditedFiles] = useState([])
  const [activeFileId, setActiveFileId] = useState(null)
  const [closedFiles, setClosedFiles] = useState(new Set())
  const closedFilesRef = useRef(closedFiles)
  closedFilesRef.current = closedFiles

  const [isLoadingFiles, setIsLoadingFiles] = useState(false)
  const [showCodePanel, setShowCodePanel] = useState(false)
  const [fileTreeRefreshTrigger, setFileTreeRefreshTrigger] = useState(0)
  const [isUploading, setIsUploading] = useState(false)
  const uploadInputRef = useRef(null)
  const diffAbortRef = useRef(null)
  const editedFilesRef = useRef(editedFiles)
  editedFilesRef.current = editedFiles

  // Track which tool‑result IDs have already triggered a diff / tree refresh
  const processedResultsRef = useRef(new Set())
  const prevConversationIdRef = useRef(conversationId)

  // ── fetchFileDiffs ──────────────────────────────────────────────────────

  const fetchFileDiffs = useCallback(async () => {
    if (!conversationId) return

    if (diffAbortRef.current) diffAbortRef.current.abort()
    const controller = new AbortController()
    diffAbortRef.current = controller

    setIsLoadingFiles(true)
    try {
      const data = await getFileDiffs(conversationId, controller.signal)
      const cf = closedFilesRef.current

      setEditedFiles(prev => mergePanelFiles(prev, data.files, cf))
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
  }, [conversationId])

  // ── One effect for all tool‑activity reactions ──────────────────────────

  useEffect(() => {
    // Reset on conversation switch
    if (conversationId !== prevConversationIdRef.current) {
      prevConversationIdRef.current = conversationId
      processedResultsRef.current = new Set()
    }

    let hasCodeCalls = false
    let shouldFetchDiffs = false
    let shouldRefreshTree = false

    for (const msg of messages) {
      for (const a of msg.activities || []) {
        // Open panel when any code tool call first appears
        if (a.type === 'tool_call' && CODE_TOOLS.includes(a.name)) {
          hasCodeCalls = true
        }

        // Detect new tool results
        if (a.type === 'tool_result') {
          if (processedResultsRef.current.has(a.tool_call_id)) continue

          const tc = msg.activities?.find(
            t => t.type === 'tool_call' && t.id === a.tool_call_id,
          )
          if (!tc) continue

          processedResultsRef.current.add(a.tool_call_id)

          if (CODE_TOOLS.includes(tc.name)) shouldFetchDiffs = true
          if (FILE_SYSTEM_TOOLS.includes(tc.name)) shouldRefreshTree = true
        }
      }
    }

    if (hasCodeCalls) setShowCodePanel(true)
    if (shouldFetchDiffs) {
      setClosedFiles(new Set())
      fetchFileDiffs()
    }
    if (shouldRefreshTree) {
      setFileTreeRefreshTrigger(prev => prev + 1)
    }
  }, [messages, conversationId, fetchFileDiffs])

  // ── Retry: panel open but empty → endpoint might not be ready yet ───────

  useEffect(() => {
    if (!showCodePanel || editedFiles.length > 0 || !conversationId) return
    const timer = setTimeout(fetchFileDiffs, 800)
    return () => clearTimeout(timer)
  }, [showCodePanel, editedFiles.length, conversationId, fetchFileDiffs])


  // ── Auto‑close panel when no files and streaming ends ───────────────────

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
        setActiveFileId(remaining[0]?.id ?? null)
      }
      return remaining
    })
  }, [activeFileId])

  const handleCloseCodePanel = useCallback(() => {
    setShowCodePanel(false)
  }, [])

  const handlePathDeleted = useCallback((deletedPath) => {
    if (!deletedPath) return
    setEditedFiles(prev => {
      const { files, nextActiveId } = closePanelFilesForDeletedPath(
        prev, activeFileId, deletedPath,
      )
      if (files.length === prev.length) return prev
      const removed = prev.filter(f => !files.some(n => n.id === f.id))
      if (removed.length) {
        setClosedFiles(cf => {
          const next = new Set(cf)
          for (const f of removed) next.add(f.id)
          return next
        })
      }
      setActiveFileId(nextActiveId)
      return files
    })
  }, [activeFileId])

  const handleRefreshFiles = useCallback(async () => {
    const viewOnly = editedFilesRef.current.filter(f => f.isViewOnly)
    await fetchFileDiffs()
    if (viewOnly.length === 0) return

    setIsLoadingFiles(true)
    try {
      const results = await Promise.all(viewOnly.map(async (f) => {
        try {
          const data = await readWorkspaceFile(f.path)
          return { id: f.id, content: data.content ?? '' }
        } catch (error) {
          console.error('Error refreshing file:', error)
          return { id: f.id, missing: true }
        }
      }))

      setEditedFiles(prev => {
        const next = applyViewOnlyReadResults(prev, results)
        const removedIds = prev
          .filter(f => f.isViewOnly && !next.some(n => n.id === f.id))
          .map(f => f.id)
        if (removedIds.length) {
          setClosedFiles(cf => {
            const closed = new Set(cf)
            for (const id of removedIds) closed.add(id)
            return closed
          })
        }
        setActiveFileId(aid => (
          aid && next.some(f => f.id === aid) ? aid : (next[0]?.id ?? null)
        ))
        return next
      })
    } finally {
      setIsLoadingFiles(false)
    }
  }, [fetchFileDiffs])

  const handleFileTreeClick = useCallback(async (filePath) => {
    try {
      const data = await readWorkspaceFile(filePath)

      const entry = {
        id: filePath,          // plain path — merges naturally with diff-tracked files
        path: filePath,
        isNew: false,
        hasChanges: false,
        isViewOnly: true,
        lines: data.content.split('\n').map((content, idx) => ({
          lineNumber: idx + 1,
          content,
          type: null,
        })),
      }

      setEditedFiles(prev => {
        const existing = prev.find(f => f.id === entry.id)
        if (existing) return prev.map(f => (f.id === entry.id ? entry : f))
        return [...prev, entry]
      })
      setActiveFileId(entry.id)
      setShowCodePanel(true)
      setClosedFiles(prev => {
        const next = new Set(prev)
        next.delete(entry.id)
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
    editedFiles,
    activeFileId,
    setActiveFileId,
    showCodePanel,
    setShowCodePanel,
    isLoadingFiles,
    fileTreeRefreshTrigger,
    isUploading,
    uploadInputRef,
    handleFileClose,
    handleCloseCodePanel,
    handleRefreshFiles,
    handlePathDeleted,
    handleFileTreeClick,
    handleUploadProject,
    setEditedFiles,
    setClosedFiles,
    setFileTreeRefreshTrigger,
  }
}
