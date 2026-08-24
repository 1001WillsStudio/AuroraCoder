/**
 * Tool-activity card helpers. Living-tool-state panels append protocol
 * dumps to the LLM tool message; the card should show only the tool's
 * own result and mark an Error: result as failed.
 */

const PANEL_START_MARKERS = [
  '<====CODE_INTERPRETER_START====>',
  '<====TOOLSTORE_START====>',
  '<====MEMORY_START====>',
]

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

export function isFailedToolOutput(content) {
  return /^Error\b/.test(String(content || '').trim())
}

export function toolActivityFinishClass(result) {
  if (!result) return ''
  if (result.isTerminated) return 'terminated'
  const display = stripPanelMarkup(result.content)
  if (isFailedToolOutput(display)) return 'failed'
  return 'complete'
}
