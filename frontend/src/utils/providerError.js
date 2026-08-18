/**
 * Turn provider/SDK exception text into a short sentence for the chat UI.
 * Must stay aligned with src/user_visible_errors.py.
 */
export const PROVIDER_FAILURE_MESSAGE = 'The model provider failed.'

const ERROR_CODE_DUMP = /Error code:\s*\d+\s*-\s*[{\[]/i
const DICT_ERROR_KEY = /['"]error['"]\s*:/

export function looksLikeRawProviderError(message) {
  if (typeof message !== 'string') return false
  let text = message.trim()
  if (!text) return false
  if (text.toLowerCase().startsWith('error:')) text = text.slice(6).trim()
  if (ERROR_CODE_DUMP.test(text)) return true
  if (DICT_ERROR_KEY.test(text) && text.includes('{') && text.includes('}')) return true
  if (text.length >= 2 && '{['.includes(text[0]) && '}]'.includes(text[text.length - 1])) return true
  return false
}

/** User-facing sentence. Never includes an "Error:" prefix. */
export function userVisibleErrorMessage(message) {
  let text = typeof message === 'string' ? message.trim() : ''
  if (text.toLowerCase().startsWith('error:')) text = text.slice(6).trim()
  if (!text || looksLikeRawProviderError(text)) return PROVIDER_FAILURE_MESSAGE
  return text
}
