import React, { useState, useEffect } from 'react'
import { X, Plus, Trash2, Save, RefreshCw, Shield, Globe, LogOut, ExternalLink, Wrench, ChevronDown, ChevronRight, Search } from 'lucide-react'
import { getSettings, updateSettings, getProviders, getToolStoreStatus, refreshToolStore } from '../services/api'
import { isAuthRequired, isAuthenticated, logout as authLogout, clearToken } from '../utils/auth.js'
import useLanguage from '../hooks/useLanguage'
import { LANG_LABELS } from '../i18n/translations'
import '../styles/settings.css'

/**
 * Settings modal — unified provider management.
 *
 * One list of OpenAI-compatible providers: built-in defaults + user-added custom.
 * Also includes Web Secondary Model and Agent Behavior sections.
 * All changes persist to /app/data/settings.json.
 *
 * Language support: the language selector in the header switches the entire
 * settings UI between available languages (currently en / zh). The choice is
 * persisted in localStorage via the useLanguage hook.
 */
export default function SettingsPanel({ isOpen, onClose }) {
  const { t, lang, setLang } = useLanguage()

  const [settings, setSettings] = useState(null)
  const [providers, setProviders] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)
  const [apiKeysConfigured, setApiKeysConfigured] = useState({})
  const [errorFields, setErrorFields] = useState({})
  const [authEnabled, setAuthEnabled] = useState(null)
  const [isAuthed, setIsAuthed] = useState(isAuthenticated())
  const [toolStoreStatus, setToolStoreStatus] = useState(null)
  const [providersCollapsed, setProvidersCollapsed] = useState(false)  // false = always open
  const [webSecondaryCollapsed, setWebSecondaryCollapsed] = useState(true)
  const [discovering, setDiscovering] = useState({})         // { providerId: true }
  const [discoveredModels, setDiscoveredModels] = useState({})// { providerId: ["gpt-4",...] }
  const [discoverError, setDiscoverError] = useState({})      // { providerId: "msg" }
  const [discoverFilter, setDiscoverFilter] = useState({})    // { providerId: "search" }
  const [expandedProvider, setExpandedProvider] = useState(null)

  /** Built-in provider *families* — each can have multiple models discovered.
   *  Must be kept in sync with MODEL_PROVIDERS in src/config.py. */
  const BUILTIN_PROVIDERS = [
    { id: 'deepseek', name: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', builtin: true },
    { id: 'opencode', name: 'OpenCode', base_url: 'https://opencode.ai/zen/go/v1', builtin: true },
    { id: 'nvidia',  name: 'NVIDIA NIM', base_url: 'https://integrate.api.nvidia.com/v1', builtin: true },
  ]

  // ── Load ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!isOpen) return
    ;(async () => {
      setLoading(true)
      setMessage(null)
      try {
        const [s, p] = await Promise.all([getSettings(), getProviders()])
        // Backend returns boolean true for configured keys — track separately
        const normApiKeys = {}
        const configured = {}
        for (const [k, v] of Object.entries(s.api_keys || {})) {
          if (v === true) { normApiKeys[k] = ''; configured[k] = true }
          else normApiKeys[k] = typeof v === 'string' ? v : ''
        }
        s.api_keys = normApiKeys
        setApiKeysConfigured(configured)
        // Same for custom providers — track configured keys
        for (const cp of s.custom_providers || []) {
          if (cp?.api_key === true) { cp.api_key = ''; cp._key_configured = true }
        }
        const other = s.other || {}
        other.web_secondary = other.web_secondary || {}
        other.agent = other.agent || {}
        other.google_search = other.google_search || {}
        other.github = other.github || {}
        setSettings({ ...s, other })
        setProviders(p.providers || [])
      } catch {
        // Backend unavailable — set safe defaults so the UI still works
        setSettings({ api_keys: {}, provider_overrides: {}, custom_providers: [], other: { web_secondary: {}, agent: {} } })
        setProviders(BUILTIN_PROVIDERS.map(p => ({ ...p, custom: false })))
        setMessage({ type: 'error', text: t('settings.loadError') })
      } finally {
        setLoading(false)
      }
    })()
  }, [isOpen, t])

  // Check server auth requirement when panel opens
  useEffect(() => {
    if (!isOpen) return
    isAuthRequired().then(needed => setAuthEnabled(needed))
    setIsAuthed(isAuthenticated())
  }, [isOpen])

  // Load ToolStore status when panel opens
  useEffect(() => {
    if (!isOpen) return
    getToolStoreStatus().then(s => setToolStoreStatus(s)).catch(() => setToolStoreStatus(null))
  }, [isOpen])

  if (!isOpen) return null

  // ── Helpers ─────────────────────────────────────────────────────────────
  const setApiKey = (providerId, value) => {
    setSettings(prev => {
      const next = { ...prev.api_keys, [providerId]: value }
      return { ...prev, api_keys: next }
    })
    setErrorFields(prev => ({ ...prev, [providerId]: false }))
  }

  const setOverride = (providerId, field, value) => {
    setSettings(prev => ({
      ...prev,
      provider_overrides: {
        ...prev.provider_overrides,
        [providerId]: { ...(prev.provider_overrides?.[providerId] || {}), [field]: value }
      }
    }))
  }

  const setOther = (section, field, value) => {
    setSettings(prev => ({
      ...prev,
      other: { ...prev.other, [section]: { ...(prev.other?.[section] || {}), [field]: value }}
    }))
  }

  // ── Custom provider CRUD ────────────────────────────────────────────────
  const addCustomProvider = () => {
    setSettings(prev => ({
      ...prev,
      custom_providers: [...(prev.custom_providers || []), {
        id: 'custom-' + Date.now(), name: '', base_url: '', api_key: ''
      }]
    }))
  }

  const updateCustomProvider = (index, field, value) => {
    setSettings(prev => {
      const cp = [...(prev.custom_providers || [])]; cp[index] = { ...cp[index], [field]: value }
      return { ...prev, custom_providers: cp }
    })
    if (field === 'base_url')
      setErrorFields(prev => ({ ...prev, [`custom-${index}`]: false }))
  }

  const removeCustomProvider = (idx) => {
    setSettings(prev => {
      const cp = [...(prev.custom_providers || [])]
      const removed = cp[idx]
      cp.splice(idx, 1)
      // also clean up provider_models for this custom provider
      const pm = { ...(prev.provider_models || {}) }
      if (removed?.id) delete pm[removed.id]
      return { ...prev, custom_providers: cp, provider_models: pm }
    })
  }


  // ── Discover models from a provider's endpoint ──────────────────────────
  const discoverModels = async (providerId) => {
    // Frontend never sees the API key — the gateway resolves it server-side.
    setDiscovering(prev => ({ ...prev, [providerId]: true }))
    setDiscoverError(prev => ({ ...prev, [providerId]: '' }))
    setDiscoveredModels(prev => ({ ...prev, [providerId]: [] }))
    try {
      const resp = await fetch(`/api/discover-models?provider_id=${encodeURIComponent(providerId)}`)
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        throw new Error(data.detail || `HTTP ${resp.status}`)
      }
      const data = await resp.json()
      setDiscoveredModels(prev => ({ ...prev, [providerId]: data.models || [] }))
      setExpandedProvider(providerId)
    } catch (err) {
      setDiscoverError(prev => ({ ...prev, [providerId]: err.message }))
    } finally {
      setDiscovering(prev => ({ ...prev, [providerId]: false }))
    }
  }

  const toggleModel = (providerId, modelId, enabled) => {
    setSettings(prev => {
      const pm = { ...(prev.provider_models || {}) }
      let list = [...(pm[providerId] || [])]
      if (enabled) {
        if (!list.find(m => (typeof m === 'string' ? m : m.id) === modelId)) {
          list.push({ id: modelId })
        }
      } else {
        list = list.filter(m => (typeof m === 'string' ? m : m.id) !== modelId)
      }
      pm[providerId] = list
      return { ...prev, provider_models: pm }
    })
  }

  /** Get enabled model IDs for a provider from provider_models. */
  const getEnabledModels = (pid) => {
    const pm = settings?.provider_models || {}
    return (pm[pid] || []).map(m => typeof m === 'string' ? m : m.id)
  }

  // ── Validation ──────────────────────────────────────────────────────────
  const validate = () => {
    const errors = {};
    (settings?.custom_providers || []).forEach((cp, i) => {
      const b = `custom-${i}`
      if (!cp.name?.trim()) errors[b] = t('msg.nameRequired')
      if (!cp.base_url?.trim()) errors[b] = errors[b] || t('msg.baseUrlRequired')
      if (!cp.api_key?.trim()) errors[b] = errors[b] || t('msg.apiKeyRequired')
    })
    setErrorFields(errors)
    return Object.keys(errors).length === 0
  }

  // ── Save ────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!validate()) { setMessage({ type: 'error', text: t('msg.validationError') }); return }
    setSaving(true); setMessage(null)
    try {
      // Prune empty custom providers
      const cp = (settings.custom_providers || []).filter(c => c.name?.trim() || c.base_url?.trim())
      // Convert empty-kept keys back to boolean true so backend preserves the real key
      for (const c of cp) {
        if (c.api_key === '' && c._key_configured) { c.api_key = true; delete c._key_configured }
      }
      // Convert api_keys: empty+configured → true, non-empty → actual value
      const outApiKeys = {}
      for (const [k, v] of Object.entries(settings.api_keys || {})) {
        if (v === '' && apiKeysConfigured[k]) outApiKeys[k] = true   // keep existing
        else if (v && v.trim()) outApiKeys[k] = v                     // new key
      }
      // Prune empty 'other' sub-objects (also convert empty+configured for web_secondary api_key)
      const prunedOther = {}
      for (const [sec, fields] of Object.entries(settings.other || {})) {
        if (fields && typeof fields === 'object') {
          const clean = {}
          for (const [k, v] of Object.entries(fields)) {
            if (v === '' && apiKeysConfigured[k]) clean[k] = true    // keep existing
            else if (v !== '' && v !== null && v !== undefined) clean[k] = v
          }
          if (Object.keys(clean).length > 0) prunedOther[sec] = clean
        }
      }
      await updateSettings({
        api_keys: outApiKeys, provider_overrides: settings.provider_overrides,
        custom_providers: cp, other: prunedOther,
        provider_models: settings.provider_models || {},
      })
      setMessage({ type: 'success', text: t('msg.saved') })
      setTimeout(async () => { try { const p = await getProviders(); setProviders(p.providers || []); window.dispatchEvent(new Event('providers-changed')) } catch {} }, 800)
    } catch { setMessage({ type: 'error', text: t('msg.saveFailed') }) }
    finally { setSaving(false) }
  }

  // ── Derived data ────────────────────────────────────────────────────────
  const builtIn = providers.filter(p => !p.custom)
  const custom = settings?.custom_providers || []
  const apiKeys = settings?.api_keys || {}
  const other = settings?.other || {}

  // ── Structured model-selection (de)serialization ───────────────────
  // The on-disk form is { provider, model } (not a composite "provider::model"
  // string), but a <select> needs a scalar value. The flat providers[] list
  // carries a composite `id` ("provider::model" or bare family id) that we
  // use as that scalar — converting to/from the structured form here.
  const selectionToId = (sel) => {
    if (!sel || typeof sel !== 'object') return ''
    const exact = providers.find(p => p.provider_id === sel.provider
      && (p.model || '') === (sel.model || ''))
    if (exact) return exact.id
    const byProv = providers.find(p => p.provider_id === sel.provider)
    return byProv?.id || ''
  }
  const idToSelection = (id) => {
    const entry = providers.find(p => p.id === id)
    return entry ? { provider: entry.provider_id, model: entry.model || '' } : ''
  }

  // Merge built-in + custom into one list for rendering
  const allProviders = [
    ...builtIn.map(p => ({ ...p, _builtin: true })),
    ...custom.map((cp, i) => ({
      id: cp.id || `custom-key-${i}`, name: cp.name || 'Untitled', _customIndex: i,
      _builtin: false, custom: true
    }))
  ]

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-modal" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="settings-header">
          <div className="settings-header-top">
            <h2>{t('settings.title')}</h2>
            <div className="settings-header-actions">
              {/* ── Language selector ────────────────────────────── */}
              <div className="settings-lang-selector">
                <Globe size={14} />
                <select
                  value={lang}
                  onChange={e => setLang(e.target.value)}
                  title={t('language.label')}
                >
                  {Object.entries(LANG_LABELS).map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                </select>
              </div>
              <button className="settings-close-btn" onClick={onClose} title={t('settings.close')}>
                <X size={18} />
              </button>
            </div>
          </div>
          <p className="settings-subtitle">{t('settings.subtitle')}</p>
        </div>

        <div className="settings-body">
          {loading ? (
            <div className="settings-loading">{t('settings.loading')}</div>
          ) : (
            <>

              {/* ── Providers ────────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">⚙ {t('providers.title')}</h3>
                <p className="settings-section-desc">{t('providers.desc')}</p>
                  {/* ── Built-in provider families ──────────────────────── */}
                  {BUILTIN_PROVIDERS.map(prov => {
                    const pid = prov.id
                    const enabled = getEnabledModels(pid)
                    const isExpanded = expandedProvider === pid
                    return (
                      <div key={pid} className="settings-custom-provider">
                        <div className="settings-custom-header">
                          <div className="settings-provider-title-row">
                            <span className="settings-custom-label">{prov.name}</span>
                            <span className="settings-badge settings-badge-builtin"><Shield size={9} /> built‑in</span>
                          </div>
                        </div>

                        {/* Base URL — locked for built-in */}
                        <div className="settings-field-row" style={{ marginTop: 10 }}>
                          <div className="settings-field-col settings-field-col-wide">
                            <label>{t('field.baseUrl')}</label>
                            <input className="settings-input" type="text"
                              value={prov.base_url}
                              disabled
                              placeholder={t('field.baseUrlPlaceholderBuiltin')} />
                          </div>
                        </div>

                        {/* API Key */}
                        <div className="settings-field-row" style={{ marginTop: 10 }}>
                          <div className="settings-field-col settings-field-col-wide">
                            <label>{t('field.apiKey')}</label>
                            <input className="settings-input" type="text"
                              value={apiKeys[pid] || ''}
                              onChange={e => setApiKey(pid, e.target.value)}
                              placeholder={apiKeysConfigured[pid]
                                ? t('field.apiKeyPlaceholderSet').replace('{provider}', prov.name)
                                : t('field.apiKeyPlaceholder')} />
                          </div>
                        </div>

                        {/* Discover + Models */}
                        <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--border-color)' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                            <button
                              className="settings-btn-discover"
                              onClick={() => discoverModels(pid)}
                              disabled={discovering[pid]}
                            >
                              {discovering[pid] ? (
                                <><RefreshCw size={14} className="spin" /> {t('field.discovering')}</>
                              ) : (
                                <><Search size={14} /> {t('field.discover')}</>
                              )}
                            </button>
                            {enabled.length > 0 && (
                              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                {enabled.length} model{enabled.length !== 1 ? 's' : ''} enabled
                              </span>
                            )}
                          </div>

                          {discoverError[pid] && (
                            <div className="settings-field-error" style={{ marginBottom: 8 }}>{discoverError[pid]}</div>
                          )}

                          {/* Enabled models shown as tags */}
                          {enabled.length > 0 && (
                            <div className="settings-enabled-models">
                              {enabled.map(m => (
                                <span key={m} className="settings-enabled-model-tag">
                                  {m}
                                  <button className="settings-enabled-model-remove"
                                    onClick={() => toggleModel(pid, m, false)}
                                    title={t('providers.remove')}>
                                    <X size={10} />
                                  </button>
                                </span>
                              ))}
                            </div>
                          )}

                          {/* Discovered model list with checkboxes (when expanded) */}
                          {isExpanded && discoveredModels[pid] && discoveredModels[pid].length > 0 && (
                            <div className="settings-discovered-models" style={{ marginTop: 8 }}>
                              <div className="settings-discovered-models-header">
                                {t('field.foundModels')} ({discoveredModels[pid].length})
                              </div>
                              <div className="settings-discovered-filter">
                                <Search size={12} className="settings-discovered-filter-icon" />
                                <input
                                  className="settings-input settings-discovered-filter-input"
                                  type="text"
                                  value={discoverFilter[pid] || ''}
                                  onChange={e => setDiscoverFilter(prev => ({ ...prev, [pid]: e.target.value }))}
                                  placeholder={t('field.filterModels')}
                                />
                                {(discoverFilter[pid] || '') && (
                                  <button className="settings-icon-btn"
                                    onClick={() => setDiscoverFilter(prev => ({ ...prev, [pid]: '' }))}
                                    title={t('field.clearFilter')}>
                                    <X size={14} />
                                  </button>
                                )}
                              </div>
                              <div className="settings-discovered-models-list">
                                {(() => {
                                  const f = (discoverFilter[pid] || '').toLowerCase()
                                  const filtered = f
                                    ? discoveredModels[pid].filter(m => m.toLowerCase().includes(f))
                                    : discoveredModels[pid]
                                  const shown = filtered.slice(0, 50)
                                  return (
                                    <>
                                      {shown.map(m => (
                                        <label key={m} className="settings-discovered-model-checkbox">
                                          <input type="checkbox"
                                            checked={enabled.includes(m)}
                                            onChange={e => toggleModel(pid, m, e.target.checked)} />
                                          <span>{m}</span>
                                        </label>
                                      ))}
                                      {filtered.length > 50 && (
                                        <span className="settings-discovered-more">
                                          … {filtered.length - 50} more
                                        </span>
                                      )}
                                      {f && shown.length === 0 && (
                                        <span className="settings-discovered-more">{t('field.noMatches')}</span>
                                      )}
                                    </>
                                  )
                                })()}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })}

                  {/* ── Custom provider cards ──────────────────────────── */}
                  {custom.map((cp, ci) => {
                    const pid = cp.id
                    const enabled = getEnabledModels(pid)
                    const isExpanded = expandedProvider === pid
                    const hasError = errorFields[`custom-${ci}`]
                    return (
                      <div key={pid} className="settings-custom-provider">
                        <div className="settings-custom-header">
                          <div className="settings-provider-title-row">
                            <span className="settings-custom-label">{cp.name || t('providers.untitled')}</span>
                            <span className="settings-badge settings-badge-custom">{t('providers.badgeCustom')}</span>
                          </div>
                          <button className="settings-icon-btn settings-danger-btn"
                            onClick={() => removeCustomProvider(ci)} title={t('providers.remove')}>
                            <Trash2 size={16} />
                          </button>
                        </div>
                        {hasError && <div className="settings-field-error">{errorFields[`custom-${ci}`]}</div>}

                        {/* Display Name */}
                        <div className="settings-field-row">
                          <div className="settings-field-col">
                            <label>{t('field.displayName')}</label>
                            <input className="settings-input" type="text"
                              value={cp.name || ''}
                              onChange={e => updateCustomProvider(ci, 'name', e.target.value)}
                              placeholder={t('field.displayNamePlaceholder')} />
                          </div>
                        </div>

                        {/* Base URL */}
                        <div className="settings-field-row" style={{ marginTop: 10 }}>
                          <div className="settings-field-col settings-field-col-wide">
                            <label>{t('field.baseUrl')}</label>
                            <input className="settings-input" type="text"
                              value={cp.base_url || ''}
                              onChange={e => updateCustomProvider(ci, 'base_url', e.target.value)}
                              placeholder={t('field.baseUrlPlaceholderCustom')} />
                          </div>
                        </div>

                        {/* API Key */}
                        <div className="settings-field-row" style={{ marginTop: 10 }}>
                          <div className="settings-field-col settings-field-col-wide">
                            <label>{t('field.apiKey')}</label>
                            <input className="settings-input" type="text"
                              value={cp.api_key || ''}
                              onChange={e => updateCustomProvider(ci, 'api_key', e.target.value)}
                              placeholder={cp.api_key === '' && cp._key_configured
                                ? t('field.apiKeyPlaceholderSet').replace('{provider}', cp.name || 'Custom')
                                : 'sk-…'} />
                          </div>
                        </div>

                        {/* Discover + Models */}
                        <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--border-color)' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                            <button
                              className="settings-btn-discover"
                              onClick={() => discoverModels(pid)}
                              disabled={discovering[pid]}
                            >
                              {discovering[pid] ? (
                                <><RefreshCw size={14} className="spin" /> {t('field.discovering')}</>
                              ) : (
                                <><Search size={14} /> {t('field.discover')}</>
                              )}
                            </button>
                            {enabled.length > 0 && (
                              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                                {enabled.length} model{enabled.length !== 1 ? 's' : ''} enabled
                              </span>
                            )}
                          </div>

                          {discoverError[pid] && (
                            <div className="settings-field-error" style={{ marginBottom: 8 }}>{discoverError[pid]}</div>
                          )}

                          {/* Enabled models shown as tags */}
                          {enabled.length > 0 && (
                            <div className="settings-enabled-models">
                              {enabled.map(m => (
                                <span key={m} className="settings-enabled-model-tag">
                                  {m}
                                  <button className="settings-enabled-model-remove"
                                    onClick={() => toggleModel(pid, m, false)}
                                    title={t('providers.remove')}>
                                    <X size={10} />
                                  </button>
                                </span>
                              ))}
                            </div>
                          )}

                          {/* Discovered model list with checkboxes (when expanded) */}
                          {isExpanded && discoveredModels[pid] && discoveredModels[pid].length > 0 && (
                            <div className="settings-discovered-models" style={{ marginTop: 8 }}>
                              <div className="settings-discovered-models-header">
                                {t('field.foundModels')} ({discoveredModels[pid].length})
                              </div>
                              <div className="settings-discovered-filter">
                                <Search size={12} className="settings-discovered-filter-icon" />
                                <input
                                  className="settings-input settings-discovered-filter-input"
                                  type="text"
                                  value={discoverFilter[pid] || ''}
                                  onChange={e => setDiscoverFilter(prev => ({ ...prev, [pid]: e.target.value }))}
                                  placeholder={t('field.filterModels')}
                                />
                                {(discoverFilter[pid] || '') && (
                                  <button className="settings-icon-btn"
                                    onClick={() => setDiscoverFilter(prev => ({ ...prev, [pid]: '' }))}
                                    title={t('field.clearFilter')}>
                                    <X size={14} />
                                  </button>
                                )}
                              </div>
                              <div className="settings-discovered-models-list">
                                {(() => {
                                  const f = (discoverFilter[pid] || '').toLowerCase()
                                  const filtered = f
                                    ? discoveredModels[pid].filter(m => m.toLowerCase().includes(f))
                                    : discoveredModels[pid]
                                  const shown = filtered.slice(0, 50)
                                  return (
                                    <>
                                      {shown.map(m => (
                                        <label key={m} className="settings-discovered-model-checkbox">
                                          <input type="checkbox"
                                            checked={enabled.includes(m)}
                                            onChange={e => toggleModel(pid, m, e.target.checked)} />
                                          <span>{m}</span>
                                        </label>
                                      ))}
                                      {filtered.length > 50 && (
                                        <span className="settings-discovered-more">
                                          … {filtered.length - 50} more
                                        </span>
                                      )}
                                      {f && shown.length === 0 && (
                                        <span className="settings-discovered-more">{t('field.noMatches')}</span>
                                      )}
                                    </>
                                  )
                                })()}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })}

                  {/* Add Custom Provider */}
                  <button className="settings-add-btn" onClick={addCustomProvider}>
                    <Plus size={16} /><span>{t('field.addProvider')}</span>
                  </button>
              </section>

              {/* ── Agent Behavior ──────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">{t('agent.title')}</h3>
                <p className="settings-section-desc">{t('agent.desc')}</p>
                <div className="settings-field-row">
                  <div className="settings-field-col">
                    <label>{t('agent.defaultModel')}</label>
                    <select className="settings-input"
                      value={selectionToId(other.agent?.default_model)}
                      onChange={e => setOther('agent', 'default_model', idToSelection(e.target.value))}>
                      <option value="">{t('agent.systemDefault')}</option>
                      {providers.map(p => (
                        <option key={p.id} value={p.id}>{p.name}{p.custom ? t('agent.customSuffix') : ''}</option>
                      ))}
                    </select>
                  </div>
                  <div className="settings-field-col">
                    <label>{t('agent.maxIterations')}</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.max_iterations || ''}
                      onChange={e => setOther('agent', 'max_iterations', e.target.value)}
                      placeholder="30" min="5" max="200"
                    />
                  </div>
                </div>
                <div className="settings-field-row" style={{ marginTop: 12 }}>
                  <div className="settings-field-col">
                    <label>{t('agent.maxToolConcurrency')}</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.max_tool_concurrency || ''}
                      onChange={e => setOther('agent', 'max_tool_concurrency', e.target.value)}
                      placeholder="5" min="1" max="20"
                    />
                  </div>
                  <div className="settings-field-col">
                    <label>{t('agent.terminalMaxOutput')}</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.terminal_max_output || ''}
                      onChange={e => setOther('agent', 'terminal_max_output', e.target.value)}
                      placeholder="15000" min="1000" max="100000"
                    />
                  </div>
                </div>
                <div className="settings-field-row" style={{ marginTop: 12 }}>
                  <div className="settings-field-col settings-field-col-checkbox" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <label className="settings-checkbox-label">
                      <input type="checkbox"
                        checked={other.agent?.save_training_data !== false}
                        onChange={e => setOther('agent', 'save_training_data', e.target.checked)}
                      />
                      {t('agent.saveTrainingData')}
                    </label>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 4 }}>
                      {t('agent.saveTrainingDataDesc')}
                    </span>
                  </div>
                </div>
              </section>

              {/* ── Google Search ──────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">
                  🔍 Google Search
                </h3>
                <p className="settings-section-desc">
                  Programmable Search Engine credentials for the google_search tool.
                  <br />
                  <a href="https://developers.google.com/custom-search/v1/overview" target="_blank" rel="noopener"
                    style={{ color: 'var(--accent)', fontSize: 12 }}>
                    Get an API key →
                  </a>
                  {' · '}
                  <a href="https://programmablesearchengine.google.com/" target="_blank" rel="noopener"
                    style={{ color: 'var(--accent)', fontSize: 12 }}>
                    Create a CSE →
                  </a>
                </p>
                <div className="settings-field-row">
                  <div className="settings-field-col">
                    <label>API Key</label>
                    <input className="settings-input" type="text"
                      value={apiKeys['google_search'] || ''}
                      onChange={e => setApiKey('google_search', e.target.value)}
                      placeholder={apiKeysConfigured['google_search'] ? 'Google Search API key has been set, enter another to override' : 'AIza…'}
                    />
                  </div>
                  <div className="settings-field-col">
                    <label>CSE ID</label>
                    <input className="settings-input" type="text"
                      value={other.google_search?.cse_id || ''}
                      onChange={e => setOther('google_search', 'cse_id', e.target.value)}
placeholder="abc123..."
                    />
                  </div>
                </div>
              </section>

              {/* ── GitHub Personal Access Token ────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">
                  🐙 GitHub Personal Access Token
                </h3>
                <p className="settings-section-desc">
                  For git push operations. Classic PAT (<code>ghp_xxx</code>) or fine-grained PAT (<code>github_pat_xxx</code>).
                  <br />
                  Required scopes: <code>repo</code> (private repos) or <code>public_repo</code> (public only).
                  <br />
                  <a href="https://github.com/settings/tokens" target="_blank" rel="noopener"
                    style={{ color: 'var(--accent)', fontSize: 12 }}>
                    Create a token →
                  </a>
                </p>
                <div className="settings-field-row">
                  <div className="settings-field-col settings-field-col-wide">
                    <label>Personal Access Token</label>
                    <input className="settings-input" type="password"
                      value={apiKeys['github'] || ''}
                      onChange={e => setApiKey('github', e.target.value)}
                      placeholder={apiKeysConfigured['github'] ? 'GitHub token has been set — enter a new token to override' : 'ghp_… or github_pat_…'}
                    />
                  </div>
                </div>
              </section>

              {/* ── ToolStore ──────────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">
                  <Wrench size={14} style={{ marginRight: 2, verticalAlign: 'middle' }} /> ToolStore
                </h3>
                <p className="settings-section-desc">
                  Local MCP servers &amp; skills — managed via the ToolStore dashboard.
                </p>

                {/* Status */}
                <div className="settings-security-status" style={{ marginTop: 8 }}>
                  <span className={`settings-security-dot ${toolStoreStatus?.available ? 'authed' : ''}`} />
                  <span className="settings-security-text">
                    {toolStoreStatus === null
                      ? 'Checking…'
                      : toolStoreStatus.available
                        ? `${toolStoreStatus.total} tool(s) indexed`
                        : 'ToolStore not available — is it installed?'
                    }
                  </span>
                </div>

                {/* Source breakdown */}
                {toolStoreStatus?.by_source && Object.keys(toolStoreStatus.by_source).length > 0 && (
                  <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-muted)' }}>
                    {Object.entries(toolStoreStatus.by_source).map(([src, count]) => (
                      <span key={src} style={{ marginRight: 12 }}>
                        <span style={{ fontWeight: 600 }}>{src}</span>: {count}
                      </span>
                    ))}
                  </div>
                )}

                {/* Actions */}
                <div style={{ marginTop: 14, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                  <a
                    href={`//${window.location.hostname}:8765`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="settings-btn-save"
                    style={{ display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}
                  >
                    <ExternalLink size={14} style={{ marginRight: 6 }} />
                    Open Management Dashboard
                  </a>
                  <button
                    className="settings-btn-cancel"
                    style={{ display: 'inline-flex', alignItems: 'center' }}
                    onClick={async () => {
                      try {
                        const r = await refreshToolStore()
                        if (r.ok) {
                          const s = await getToolStoreStatus()
                          setToolStoreStatus(s)
                        }
                      } catch {}
                    }}
                  >
                    <RefreshCw size={14} style={{ marginRight: 6 }} />
                    Refresh Index
                  </button>
                </div>
              </section>

              {/* ── Security ────────────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">
                  <Shield size={14} style={{ marginRight: 2, verticalAlign: 'middle' }} />
                  {t('settings.security') || 'Security'}
                </h3>
                <div className="settings-security-status">
                  <span className={`settings-security-dot ${authEnabled ? 'enabled' : authEnabled === false ? 'disabled' : ''}`} />
                  <span className="settings-security-text">
                    {authEnabled === null
                      ? 'Checking…'
                      : authEnabled
                        ? 'Password protection is ENABLED — API endpoints require authentication.'
                        : 'Password protection is DISABLED — set ACCESS_PASSWORD env var to enable.'
                    }
                  </span>
                </div>
                {authEnabled && (
                  <div style={{ marginTop: 12 }}>
                    <div className="settings-security-status" style={{ marginBottom: 10 }}>
                      <span className={`settings-security-dot ${isAuthed ? 'authed' : 'noauth'}`} />
                      <span className="settings-security-text">
                        {isAuthed ? 'You are authenticated.' : 'Not authenticated.'}
                      </span>
                    </div>
                    {isAuthed ? (
                      <button className="settings-btn-save"
                        style={{ background: 'var(--danger, #f85149)', borderColor: 'var(--danger, #f85149)' }}
                        onClick={() => { authLogout(); clearToken(); setIsAuthed(false); window.location.reload() }}>
                        <LogOut size={14} style={{ marginRight: 6 }} /> Logout
                      </button>
                    ) : (
                      <button className="settings-btn-save" onClick={() => window.location.reload()}>
                        <RefreshCw size={14} style={{ marginRight: 6 }} /> Login
                      </button>
                    )}
                  </div>
                )}
                <div className="settings-security-status" style={{ marginTop: 14, borderTop: '1px solid var(--border-color)', paddingTop: 12 }}>
                  <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                    📱 <a href="/m" target="_blank" rel="noopener"
                      style={{ color: 'var(--accent)' }}>Open mobile web app</a> —
                    optimized for your phone.
                  </span>
                </div>
              </section>

              {/* ── Web Secondary Model ─────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title settings-collapse-title"
                  onClick={() => setWebSecondaryCollapsed(!webSecondaryCollapsed)}>
                  {webSecondaryCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                  {t('webSecondary.title')}
                </h3>
                {!webSecondaryCollapsed && <p className="settings-section-desc">{t('webSecondary.desc')}</p>}

                {!webSecondaryCollapsed && <>
                  <div className="settings-field-row">
                    <div className="settings-field-col">
                      <label>{t('webSecondary.model')}</label>
                      <select className="settings-input"
                        value={selectionToId(other.web_secondary?.model)}
                        onChange={e => setOther('web_secondary', 'model', idToSelection(e.target.value))}>
                        <option value="">{t('webSecondary.modelDefault')}</option>
                        {providers.map(p => (
                          <option key={p.id} value={p.id}>{p.name}{p.custom ? t('agent.customSuffix') : ''}</option>
                        ))}
                      </select>
                    </div>
                    <div className="settings-field-col">
                      <label>{t('webSecondary.maxTokens')}</label>
                      <input className="settings-input" type="number"
                        value={other.web_secondary?.max_tokens || ''}
                        onChange={e => setOther('web_secondary', 'max_tokens', e.target.value)}
                        placeholder={t('webSecondary.maxTokensPlaceholder')} min="256" max="32768"
                      />
                    </div>
                  </div>
                </>}
              </section>

              {/* ── Persistence note ────────────────────────────────────── */}
              <div className="settings-persistence-note">
                <span className="settings-note-icon">💾</span>
                <span dangerouslySetInnerHTML={{ __html: t('persistence.note') }} />
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="settings-footer">
          {message && <span className={`settings-msg settings-msg-${message.type}`}>{message.text}</span>}
          <div className="settings-footer-actions">
            <button className="settings-btn-cancel" onClick={onClose}>{t('footer.cancel')}</button>
            <button className="settings-btn-save" onClick={handleSave} disabled={saving || loading}>
              {saving ? <><RefreshCw size={16} className="spin" /> {t('footer.saving')}</> : <><Save size={16} /> {t('footer.save')}</>}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
