import React, { useState, useEffect } from 'react'
import { X, Plus, Trash2, Eye, EyeOff, Save, RefreshCw, Shield, Globe } from 'lucide-react'
import { getSettings, updateSettings, getProviders } from '../services/api'
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

  // ── Load ────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!isOpen) return
    ;(async () => {
      setLoading(true)
      setMessage(null)
      try {
        const [s, p] = await Promise.all([getSettings(), getProviders()])
        const other = s.other || {}
        other.web_secondary = other.web_secondary || {}
        other.agent = other.agent || {}
        setSettings({ ...s, other })
        setProviders(p.providers || [])
      } catch {
        setMessage({ type: 'error', text: t('settings.loadError') })
      } finally {
        setLoading(false)
      }
    })()
  }, [isOpen, t])

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
      // Prune empty 'other' sub-objects
      const prunedOther = {}
      for (const [sec, fields] of Object.entries(settings.other || {})) {
        if (fields && typeof fields === 'object') {
          const clean = {}
          for (const [k, v] of Object.entries(fields)) { if (v !== '' && v !== null && v !== undefined) clean[k] = v }
          if (Object.keys(clean).length > 0) prunedOther[sec] = clean
        }
      }
      await updateSettings({
        api_keys: settings.api_keys, provider_overrides: settings.provider_overrides,
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
                <h3 className="settings-section-title">{t('providers.title')}</h3>
                <p className="settings-section-desc">{t('providers.desc')}</p>

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
              </section>

              {/* ── Web Secondary Model ─────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">{t('webSecondary.title')}</h3>
                <p className="settings-section-desc">{t('webSecondary.desc')}</p>
                <div className="settings-field-row">
                  <div className="settings-field-col">
                    <label>{t('webSecondary.modelName')}</label>
                    <input className="settings-input" type="text"
                      value={other.web_secondary?.model_name || ''}
                      onChange={e => setOther('web_secondary', 'model_name', e.target.value)}
                      placeholder={t('webSecondary.modelNamePlaceholder')}
                    />
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
                <div className="settings-field-row" style={{ marginTop: '10px' }}>
                  <div className="settings-field-col settings-field-col-wide">
                    <label>{t('field.baseUrl')}</label>
                    <input className="settings-input" type="text"
                      value={other.web_secondary?.base_url || ''}
                      onChange={e => setOther('web_secondary', 'base_url', e.target.value)}
                      placeholder={t('webSecondary.baseUrlPlaceholder')}
                    />
                  </div>
                </div>
                <div className="settings-field-row" style={{ marginTop: '10px' }}>
                  <div className="settings-field-col settings-field-col-wide">
                    <label>{t('field.apiKey')}</label>
                    <div className="settings-input-row">
                      <input className="settings-input"
                        type={showKeys['web-secondary'] ? 'text' : 'password'}
                        value={other.web_secondary?.api_key || ''}
                        onChange={e => setOther('web_secondary', 'api_key', e.target.value)}
                        placeholder="sk-…"
                      />
                      <button className="settings-icon-btn" onClick={() => toggleShowKey('web-secondary')}>
                        {showKeys['web-secondary'] ? <EyeOff size={16} /> : <Eye size={16} />}
                      </button>
                    </div>
                  </div>
                </div>
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
                    <label>{t('agent.continueIterations')}</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.continue_iterations || ''}
                      onChange={e => setOther('agent', 'continue_iterations', e.target.value)}
                      placeholder="30" min="1" max="200"
                    />
                  </div>
                  <div className="settings-field-col">
                    <label>{t('agent.maxToolConcurrency')}</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.max_tool_concurrency || ''}
                      onChange={e => setOther('agent', 'max_tool_concurrency', e.target.value)}
                      placeholder="5" min="1" max="20"
                    />
                  </div>
                </div>
                <div className="settings-field-row" style={{ marginTop: '12px' }}>
                  <div className="settings-field-col">
                    <label>{t('agent.terminalMaxOutput')}</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.terminal_max_output || ''}
                      onChange={e => setOther('agent', 'terminal_max_output', e.target.value)}
                      placeholder="15000" min="1000" max="100000"
                    />
                  </div>
                  <div className="settings-field-col settings-field-col-checkbox" style={{ paddingTop: '20px' }}>
                    <label className="settings-checkbox-label">
                      <input type="checkbox" checked={other.agent?.code_interpreter_errors !== false}
                        onChange={e => setOther('agent', 'code_interpreter_errors', e.target.checked)} />
                      {t('agent.codeInterpreterChecks')}
                    </label>
                  </div>
                </div>
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
