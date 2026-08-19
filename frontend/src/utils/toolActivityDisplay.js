/**
 * Tool-activity card helpers. Living-tool-state panels append protocol
 * dumps (CODE_INTERPRETER / TOOLSTORE / MEMORY) to the LLM tool message;
 * the chat card must show only a readable result or a clear failure.
 */

const PANEL_START_MARKERS = [
  '<====CODE_INTERPRETER_START====>',
  '<====TOOLSTORE_START====>',
  '<====MEMORY_START====>',
]

const PY_INTERNAL = ['cannot unpack', 'nonetype', 'traceback']

export function stripPanelMarkup(content) {
  if (!content) return ''
  const text = String(content)
  let cut = -1
  for (const marker of PANEL_START_MARKERS) {
    const idx = text.indexOf(marker)
    if (idx !== -1 && (cut === -1 || idx < cut)) cut = idx
  }
  return cut === -1 ? text : text.slice(0, cut).replace(/\s+$/, '')
}

function looksLikePythonException(detail) {
  const lower = String(detail || '').toLowerCase()
  return PY_INTERNAL.some((snippet) => lower.includes(snippet))
}

export function sanitizeToolResultContent(content) {
  const raw = content == null ? '' : String(content)
  const notFound = raw.match(/\[File not found: ([^\]]+)\]/)
  const text = stripPanelMarkup(raw)
  const exec = text.trim().match(/^Error executing tool '([^']+)': ([\s\S]*)/)
  if (exec && looksLikePythonException(exec[2])) {
    if (notFound) return `File not found: ${notFound[1]}`
    return `Error: ${exec[1]} failed`
  }
  if (!text && notFound) return `File not found: ${notFound[1]}`
  return text
}

export function isFailedToolOutput(content, isErrorFlag) {
  if (isErrorFlag) return true
  const text = String(content || '').trim()
  if (!text) return false
  if (/^Error\b/.test(text)) return true
  if (/^File not found\b/i.test(text)) return true
  if (text.includes('[File not found:')) return true
  return false
}

export function toolActivityFinishClass(result) {
  if (!result) return ''
  if (result.isTerminated) return 'terminated'
  const display = sanitizeToolResultContent(result.content)
  if (isFailedToolOutput(display, result.isError)) return 'failed'
  return 'complete'
}
