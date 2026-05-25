import React, { useState, useEffect } from 'react'
import { X, Plus, Trash2, Eye, EyeOff, Save, RefreshCw, Shield, Globe, LogOut, ExternalLink, Wrench, ChevronDown, ChevronRight } from 'lucide-react'
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
  const [showKeys, setShowKeys] = useState({})
  const [errorFields, setErrorFields] = useState({})
  const [authEnabled, setAuthEnabled] = useState(null)
  const [isAuthed, setIsAuthed] = useState(isAuthenticated())
  const [toolStoreStatus, setToolStoreStatus] = useState(null)
  const [providersCollapsed, setProvidersCollapsed] = useState(true)
  const [webSecondaryCollapsed, setWebSecondaryCollapsed] = useState(true)

  // ── Sentinel for "key is configured but hidden" ─────────────────────────
  const KEY_SENTINEL = '••••••••'

  // ── Load ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!isOpen) return
    ;(async () => {
      setLoading(true)
      setMessage(null)
      try {
        const [s, p] = await Promise.all([getSettings(), getProviders()])
        // Backend returns boolean true for configured keys — convert to sentinel
        const normApiKeys = {}
        for (const [k, v] of Object.entries(s.api_keys || {})) {
          normApiKeys[k] = v === true ? KEY_SENTINEL : (typeof v === 'string' ? v : '')
        }
        s.api_keys = normApiKeys
        // Same for custom providers
        for (const cp of s.custom_providers || []) {
          if (cp?.api_key === true) cp.api_key = KEY_SENTINEL
        }
        const other = s.other || {}
        other.web_secondary = other.web_secondary || {}
        other.agent = other.agent || {}
        setSettings({ ...s, other })
        setProviders(p.providers || [])
      } catch {
        // Backend unavailable — set safe defaults so the UI still works
        setSettings({ api_keys: {}, provider_overrides: {}, custom_providers: [], other: { web_secondary: {}, agent: {} } })
        setProviders([])
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
    setSettings(prev => ({ ...prev, api_keys: { ...prev.api_keys, [providerId]: value }}))
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
        id: 'custom-' + Date.now(), name: '', base_url: '', api_key: '', model: '', supports_thinking: true
      }]
    }))
  }

  const updateCustomProvider = (index, field, value) => {
    setSettings(prev => {
      const cp = [...(prev.custom_providers || [])]; cp[index] = { ...cp[index], [field]: value }
      return { ...prev, custom_providers: cp }
    })
    if (field === 'id' || field === 'base_url')
      setErrorFields(prev => ({ ...prev, [`custom-${index}`]: false }))
  }

  const removeCustomProvider = (idx) => {
    setSettings(prev => ({ ...prev, custom_providers: (prev.custom_providers || []).filter((_, i) => i !== idx) }))
  }

  const toggleShowKey = (id) => setShowKeys(prev => ({ ...prev, [id]: !prev[id] }))

  // ── Validation ──────────────────────────────────────────────────────────
  const validate = () => {
    const errors = {};
    (settings?.custom_providers || []).forEach((cp, i) => {
      const b = `custom-${i}`
      if (!cp.name?.trim()) errors[b] = t('msg.nameRequired')
      if (!cp.base_url?.trim()) errors[b] = errors[b] || t('msg.baseUrlRequired')
      if (!cp.api_key?.trim()) errors[b] = errors[b] || t('msg.apiKeyRequired')
      if (!cp.model?.trim()) errors[b] = errors[b] || t('msg.modelRequired')
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
      // Convert sentinel back to boolean true so backend preserves the real key
      for (const c of cp) {
        if (c.api_key === KEY_SENTINEL) c.api_key = true
      }
      // Convert api_keys: sentinel → true, empty → skip
      const outApiKeys = {}
      for (const [k, v] of Object.entries(settings.api_keys || {})) {
        if (v === KEY_SENTINEL) outApiKeys[k] = true           // keep existing
        else if (v && v.trim()) outApiKeys[k] = v               // new key
      }
      // Prune empty 'other' sub-objects (also convert sentinel for web_secondary api_key)
      const prunedOther = {}
      for (const [sec, fields] of Object.entries(settings.other || {})) {
        if (fields && typeof fields === 'object') {
          const clean = {}
          for (const [k, v] of Object.entries(fields)) {
            if (v === KEY_SENTINEL) clean[k] = true             // keep existing
            else if (v !== '' && v !== null && v !== undefined) clean[k] = v
          }
          if (Object.keys(clean).length > 0) prunedOther[sec] = clean
        }
      }
      await updateSettings({
        api_keys: outApiKeys, provider_overrides: settings.provider_overrides,
        custom_providers: cp, other: prunedOther,
      })
      setMessage({ type: 'success', text: t('msg.saved') })
      setTimeout(async () => { try { setProviders((await getProviders()).providers || []) } catch {} }, 800)
    } catch { setMessage({ type: 'error', text: t('msg.saveFailed') }) }
    finally { setSaving(false) }
  }

  // ── Derived data ────────────────────────────────────────────────────────
  const builtIn = providers.filter(p => !p.custom)
  const custom = settings?.custom_providers || []
  const apiKeys = settings?.api_keys || {}
  const overrides = settings?.provider_overrides || {}
  const other = settings?.other || {}

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
              {/* ── Providers ──────────────────────────────────────────── */}
              <section className="settings-section">
                <h3
                  className="settings-section-title"
                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                  onClick={() => setProvidersCollapsed(!providersCollapsed)}
                >
                  {providersCollapsed
                    ? <ChevronRight size={16} style={{ marginRight: 4 }} />
                    : <ChevronDown size={16} style={{ marginRight: 4 }} />
                  }
                  {t('providers.title')}
                </h3>
                {!providersCollapsed && <p className="settings-section-desc">{t('providers.desc')}</p>}

{!providersCollapsed && <>
                {allProviders.map(prov => {
                  const pid = prov.id
                  const isBuiltIn = prov._builtin
                  const ci = prov._customIndex
                  const key = isBuiltIn ? (apiKeys[pid] || '') : (custom[ci]?.api_key || '')
                  const isKeySet = key.length > 0
                  const baseUrl = isBuiltIn
                    ? (overrides[pid]?.base_url || '')
                    : (custom[ci]?.base_url || '')
                  const model = isBuiltIn
                    ? (overrides[pid]?.model || '')
                    : (custom[ci]?.model || '')
                  const thinking = isBuiltIn ? prov.supports_thinking : (custom[ci]?.supports_thinking !== false)
                  const errKey = isBuiltIn ? null : `custom-${ci}`
                  const hasError = errKey && !!errorFields[errKey]

                  return (
                    <div key={pid} className="settings-custom-provider">
                      <div className="settings-custom-header">
                        <div className="settings-provider-title-row">
                          <span className="settings-custom-label">{prov.name}</span>
                          {isBuiltIn
                            ? <span className="settings-badge settings-badge-builtin"><Shield size={10} /> {t('providers.badgeBuiltin')}</span>
                            : <span className="settings-badge settings-badge-custom">{t('providers.badgeCustom')}</span>
                          }
                        </div>
                        {!isBuiltIn && (
                          <button className="settings-icon-btn settings-danger-btn" onClick={() => removeCustomProvider(ci)} title={t('providers.remove')}>
                            <Trash2 size={16} />
                          </button>
                        )}
                      </div>

                      {hasError && <div className="settings-field-error">{errorFields[errKey]}</div>}

                      {/* Name + API key row */}
                      <div className="settings-field-row">
                        {isBuiltIn ? (
                          <div className="settings-field-col">
                            <label>{t('field.apiKey')}</label>
                            <div className="settings-input-row">
                              <input className="settings-input"
                                type={showKeys[pid] ? 'text' : 'password'} value={key}
                                onChange={e => setApiKey(pid, e.target.value)}
                                placeholder={isKeySet ? t('field.apiKeyPlaceholderSet') : t('field.apiKeyPlaceholder')}
                              />
                              <button className="settings-icon-btn" onClick={() => toggleShowKey(pid)}
                                title={showKeys[pid] ? t('field.hide') : t('field.show')}>
                                {showKeys[pid] ? <EyeOff size={16} /> : <Eye size={16} />}
                              </button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="settings-field-col">
                              <label>{t('field.displayName')}</label>
                              <input className="settings-input" type="text"
                                value={custom[ci]?.name || ''}
                                onChange={e => updateCustomProvider(ci, 'name', e.target.value)}
                                placeholder={t('field.displayNamePlaceholder')}
                              />
                            </div>
                            <div className="settings-field-col">
                              <label>{t('field.providerId')}</label>
                              <input className="settings-input" type="text"
                                value={custom[ci]?.id || ''}
                                onChange={e => updateCustomProvider(ci, 'id', e.target.value)}
                                placeholder={t('field.providerIdPlaceholder')}
                              />
                            </div>
                          </>
                        )}
                      </div>

                      {/* Base URL row */}
                      <div className="settings-field-row" style={{ marginTop: '10px' }}>
                        <div className="settings-field-col settings-field-col-wide">
                          <label>{t('field.baseUrl')}</label>
                          {isBuiltIn ? (
                            <input className="settings-input" type="text" value={baseUrl}
                              onChange={e => setOverride(pid, 'base_url', e.target.value)}
                              placeholder={t('field.baseUrlPlaceholderBuiltin')}
                            />
                          ) : (
                            <input className="settings-input" type="text" value={baseUrl}
                              onChange={e => updateCustomProvider(ci, 'base_url', e.target.value)}
                              placeholder={t('field.baseUrlPlaceholderCustom')}
                            />
                          )}
                        </div>
                      </div>

                      {/* Model + API key + thinking row */}
                      <div className="settings-field-row" style={{ marginTop: '10px' }}>
                        <div className="settings-field-col">
                          <label>{t('field.model')}</label>
                          {isBuiltIn ? (
                            <input className="settings-input" type="text" value={model}
                              onChange={e => setOverride(pid, 'model', e.target.value)}
                              placeholder={t('field.modelPlaceholderBuiltin')}
                            />
                          ) : (
                            <input className="settings-input" type="text" value={model}
                              onChange={e => updateCustomProvider(ci, 'model', e.target.value)}
                              placeholder={t('field.modelPlaceholderCustom')}
                            />
                          )}
                        </div>
                        {!isBuiltIn && (
                          <div className="settings-field-col">
                            <label>{t('field.apiKey')}</label>
                            <div className="settings-input-row">
                              <input className="settings-input"
                                type={showKeys[`custom-${ci}`] ? 'text' : 'password'} value={key}
                                onChange={e => updateCustomProvider(ci, 'api_key', e.target.value)}
                                placeholder="sk-or-…"
                              />
                              <button className="settings-icon-btn" onClick={() => toggleShowKey(`custom-${ci}`)}>
                                {showKeys[`custom-${ci}`] ? <EyeOff size={16} /> : <Eye size={16} />}
                              </button>
                            </div>
                          </div>
                        )}
                        <div className="settings-field-col settings-field-col-checkbox" style={isBuiltIn ? { paddingTop: '20px' } : {}}>
                          <label className="settings-checkbox-label">
                            <input type="checkbox"
                              checked={thinking}
                              onChange={e => isBuiltIn
                                ? setOverride(pid, 'supports_thinking', e.target.checked)
                                : updateCustomProvider(ci, 'supports_thinking', e.target.checked)
                              }
                            />
                            {t('field.thinking')}
                          </label>
                        </div>
                      </div>
                    </div>
                  )
                })}

                <button className="settings-add-btn" onClick={addCustomProvider}>
                  <Plus size={16} /><span>{t('field.addProvider')}</span>
                </button>
                </>}
              </section>


              {/* ── Agent Behavior ──────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">{t('agent.title')}</h3>
                <p className="settings-section-desc">{t('agent.desc')}</p>
                <div className="settings-field-row">
                  <div className="settings-field-col">
                    <label>{t('agent.defaultProvider')}</label>
                    <select className="settings-input"
                      value={other.agent?.default_provider || ''}
                      onChange={e => setOther('agent', 'default_provider', e.target.value)}>
                      <option value="">{t('agent.systemDefault')}</option>
                      {allProviders.map(p => (
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
                <div className="settings-field-row" style={{ marginTop: '12px' }}>
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
              </section>

              {/* ── Security ──────────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">
                  <Shield size={16} style={{ marginRight: 6, verticalAlign: 'middle' }} />
                  {t('settings.security') || 'Security'}
                </h3>

                {/* Status indicator */}
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

                {/* Auth state */}
                {authEnabled && (
                  <div style={{ marginTop: 12 }}>
                    <div className="settings-security-status" style={{ marginBottom: 10 }}>
                      <span className={`settings-security-dot ${isAuthed ? 'authed' : 'noauth'}`} />
                      <span className="settings-security-text">
                        {isAuthed
                          ? 'You are authenticated. API calls include your credentials.'
                          : 'Not authenticated. API calls may fail.'
                        }
                      </span>
                    </div>

                    {isAuthed && (
                      <button
                        className="settings-btn-save"
                        style={{ background: 'var(--danger, #f85149)', borderColor: 'var(--danger, #f85149)' }}
                        onClick={() => {
                          authLogout()
                          clearToken()
                          setIsAuthed(false)
                          window.location.reload()
                        }}
                      >
                        <LogOut size={14} style={{ marginRight: 6 }} />
                        Logout
                      </button>
                    )}

                    {!isAuthed && (
                      <button
                        className="settings-btn-save"
                        onClick={() => window.location.reload()}
                      >
                        <RefreshCw size={14} style={{ marginRight: 6 }} />
                        Login
                      </button>
                    )}
                  </div>
                )}

                {/* Mobile app link */}
                <div className="settings-security-status" style={{ marginTop: 14, borderTop: '1px solid var(--border-color)', paddingTop: 12 }}>
                  <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                    📱 <a href="/m" target="_blank" rel="noopener"
                      style={{ color: 'var(--accent)' }}>Open mobile web app</a> —
                    optimized for your phone.
                  </span>
                </div>
              </section>

              {/* ── ToolStore ──────────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">
                  <Wrench size={16} style={{ marginRight: 6, verticalAlign: 'middle' }} />
                  ToolStore
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

              {/* ── Web Secondary Model ─────────────────────────────────── */}
              <section className="settings-section">
                <h3
                  className="settings-section-title"
                  style={{ cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                  onClick={() => setWebSecondaryCollapsed(!webSecondaryCollapsed)}
                >
                  {webSecondaryCollapsed
                    ? <ChevronRight size={16} style={{ marginRight: 4 }} />
                    : <ChevronDown size={16} style={{ marginRight: 4 }} />
                  }
                  {t('webSecondary.title')}
                </h3>
                {!webSecondaryCollapsed && <p className="settings-section-desc">{t('webSecondary.desc')}</p>}

                {!webSecondaryCollapsed && <>
                  <div className="settings-field-row">
                    <div className="settings-field-col">
                      <label>{t('webSecondary.provider')}</label>
                      <select className="settings-input"
                        value={other.web_secondary?.provider || ''}
                        onChange={e => setOther('web_secondary', 'provider', e.target.value)}>
                        <option value="">{t('webSecondary.providerDefault')}</option>
                        {allProviders.map(p => (
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
