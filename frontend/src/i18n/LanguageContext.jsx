import React, { createContext, useContext, useState, useCallback, useMemo } from 'react'
import { getTranslations, DEFAULT_LANG } from './translations'

const STORAGE_KEY = 'auroracoder:lang'

function readStoredLang() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && getTranslations(stored)) return stored
  } catch { /* localStorage may be unavailable */ }
  return DEFAULT_LANG
}

const LanguageContext = createContext(null)

/**
 * Provider that lifts language state to the app root so ALL components
 * re-render when the language changes (e.g., from the Settings panel).
 */
export function LanguageProvider({ children }) {
  const [lang, setLangState] = useState(readStoredLang)

  const setLang = useCallback((newLang) => {
    try { localStorage.setItem(STORAGE_KEY, newLang) } catch {}
    setLangState(newLang)
  }, [])

  const t = useCallback((key, params) => {
    const dict = getTranslations(lang)
    let str = dict[key] ?? getTranslations(DEFAULT_LANG)[key] ?? key
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        str = str.replaceAll(`{${k}}`, v)
      }
    }
    return str
  }, [lang])

  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t])

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  )
}

/**
 * Hook to consume language context. Must be called inside a <LanguageProvider>.
 */
export function useLanguage() {
  const ctx = useContext(LanguageContext)
  if (!ctx) {
    // Fallback for any edge case where provider is missing
    const fallbackT = (key, params) => {
      let str = getTranslations(DEFAULT_LANG)[key] ?? key
      if (params) {
        for (const [k, v] of Object.entries(params)) {
          str = str.replaceAll(`{${k}}`, v)
        }
      }
      return str
    }
    return { lang: DEFAULT_LANG, setLang: () => {}, t: fallbackT }
  }
  return ctx
}

export default LanguageContext
