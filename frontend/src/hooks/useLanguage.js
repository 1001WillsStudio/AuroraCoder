import { useState, useCallback } from 'react'
import { getTranslations, DEFAULT_LANG } from '../i18n/translations'

/** localStorage key for persisting the user's language choice. */
const STORAGE_KEY = 'thinkwithtool:lang'

/** Read the persisted language; fall back to default. */
function readStoredLang() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && getTranslations(stored)) return stored
  } catch { /* localStorage may be unavailable */ }
  return DEFAULT_LANG
}

/**
 * Simple i18n hook that manages the current UI language.
 *
 * Usage inside a component:
 *   const { t, lang, setLang, LANG_LABELS } = useLanguage()
 *   <label>{t('settings.title')}</label>
 */
export default function useLanguage() {
  const [lang, setLangState] = useState(readStoredLang)

  const setLang = useCallback((newLang) => {
    try { localStorage.setItem(STORAGE_KEY, newLang) } catch {}
    setLangState(newLang)
  }, [])

  /**
   * Translate a key into the current language.
   * Falls back to 'en' for missing keys/unsupported languages.
   */
  const t = useCallback((key) => {
    const dict = getTranslations(lang)
    return dict[key] ?? getTranslations(DEFAULT_LANG)[key] ?? key
  }, [lang])

  return { t, lang, setLang }
}
