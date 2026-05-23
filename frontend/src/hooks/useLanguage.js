/**
 * Re-exports useLanguage from the LanguageContext provider.
 *
 * Previously this hook owned its own useState — that meant every component
 * had an independent copy of the language state.  Now it pulls from a React
 * Context so that when setLang is called (e.g. in the Settings panel) every
 * component that calls useLanguage() re-renders with the new language.
 */
export { useLanguage } from '../i18n/LanguageContext'
export default useLanguage
export { LANG_LABELS } from '../i18n/translations'
