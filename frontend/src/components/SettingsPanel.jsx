import React, { useState, useEffect } from 'react'
import { X, Plus, Trash2, Eye, EyeOff, Save, RefreshCw, Shield } from 'lucide-react'
import { getSettings, updateSettings, getProviders } from '../services/api'
import '../styles/settings.css'

/**
 * Settings modal — unified provider management.
 *
 * One list of OpenAI-compatible providers: built-in defaults + user-added custom.
 * Also includes Web Secondary Model and Agent Behavior sections.
 * All changes persist to /app/data/settings.json.
 */
export default function SettingsPanel({ isOpen, onClose }) {
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
        setMessage({ type: 'error', text: 'Failed to load settings' })
      } finally {
        setLoading(false)
      }
    })()
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
      if (!cp.name?.trim()) errors[b] = 'Name required'
      if (!cp.base_url?.trim()) errors[b] = errors[b] || 'Base URL required'
      if (!cp.api_key?.trim()) errors[b] = errors[b] || 'API key required'
      if (!cp.model?.trim()) errors[b] = errors[b] || 'Model required'
    })
    setErrorFields(errors)
    return Object.keys(errors).length === 0
  }

  // ── Save ────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!validate()) { setMessage({ type: 'error', text: 'Fix validation errors before saving' }); return }
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
      setMessage({ type: 'success', text: 'Settings saved' })
      setTimeout(async () => { try { setProviders((await getProviders()).providers || []) } catch {} }, 800)
    } catch { setMessage({ type: 'error', text: 'Save failed' }) }
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
          <h2>Settings</h2>
          <p className="settings-subtitle">
            All providers are OpenAI-compatible. Pre-configured ones are built-in defaults;
            add your own endpoints below. Settings survive Docker restarts &amp; rebuilds.
          </p>
          <button className="settings-close-btn" onClick={onClose} title="Close"><X size={18} /></button>
        </div>

        <div className="settings-body">
          {loading ? (
            <div className="settings-loading">Loading…</div>
          ) : (
            <>
              {/* ── Providers ──────────────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">Providers</h3>
                <p className="settings-section-desc">All providers use the OpenAI-compatible API protocol.</p>

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
                            ? <span className="settings-badge settings-badge-builtin"><Shield size={10} /> built-in</span>
                            : <span className="settings-badge settings-badge-custom">custom</span>
                          }
                        </div>
                        {!isBuiltIn && (
                          <button className="settings-icon-btn settings-danger-btn" onClick={() => removeCustomProvider(ci)} title="Remove">
                            <Trash2 size={16} />
                          </button>
                        )}
                      </div>

                      {hasError && <div className="settings-field-error">{errorFields[errKey]}</div>}

                      {/* Name + API key row */}
                      <div className="settings-field-row">
                        {isBuiltIn ? (
                          <div className="settings-field-col">
                            <label>API Key</label>
                            <div className="settings-input-row">
                              <input className="settings-input"
                                type={showKeys[pid] ? 'text' : 'password'} value={key}
                                onChange={e => setApiKey(pid, e.target.value)}
                                placeholder={isKeySet ? '••••••••••••••••' : 'Overrides env var'}
                              />
                              <button className="settings-icon-btn" onClick={() => toggleShowKey(pid)}
                                title={showKeys[pid] ? 'Hide' : 'Show'}>
                                {showKeys[pid] ? <EyeOff size={16} /> : <Eye size={16} />}
                              </button>
                            </div>
                          </div>
                        ) : (
                          <>
                            <div className="settings-field-col">
                              <label>Display Name</label>
                              <input className="settings-input" type="text"
                                value={custom[ci]?.name || ''}
                                onChange={e => updateCustomProvider(ci, 'name', e.target.value)}
                                placeholder="My OpenRouter"
                              />
                            </div>
                            <div className="settings-field-col">
                              <label>Provider ID</label>
                              <input className="settings-input" type="text"
                                value={custom[ci]?.id || ''}
                                onChange={e => updateCustomProvider(ci, 'id', e.target.value)}
                                placeholder="my-openrouter"
                              />
                            </div>
                          </>
                        )}
                      </div>

                      {/* Base URL row */}
                      <div className="settings-field-row" style={{ marginTop: '10px' }}>
                        <div className="settings-field-col settings-field-col-wide">
                          <label>Base URL</label>
                          {isBuiltIn ? (
                            <input className="settings-input" type="text" value={baseUrl}
                              onChange={e => setOverride(pid, 'base_url', e.target.value)}
                              placeholder="Defaults to built-in endpoint"
                            />
                          ) : (
                            <input className="settings-input" type="text" value={baseUrl}
                              onChange={e => updateCustomProvider(ci, 'base_url', e.target.value)}
                              placeholder="https://openrouter.ai/api/v1"
                            />
                          )}
                        </div>
                      </div>

                      {/* Model + API key + thinking row */}
                      <div className="settings-field-row" style={{ marginTop: '10px' }}>
                        <div className="settings-field-col">
                          <label>Model</label>
                          {isBuiltIn ? (
                            <input className="settings-input" type="text" value={model}
                              onChange={e => setOverride(pid, 'model', e.target.value)}
                              placeholder="Defaults to built-in model"
                            />
                          ) : (
                            <input className="settings-input" type="text" value={model}
                              onChange={e => updateCustomProvider(ci, 'model', e.target.value)}
                              placeholder="anthropic/claude-sonnet-4"
                            />
                          )}
                        </div>
                        {!isBuiltIn && (
                          <div className="settings-field-col">
                            <label>API Key</label>
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
                            Thinking
                          </label>
                        </div>
                      </div>
                    </div>
                  )
                })}

                <button className="settings-add-btn" onClick={addCustomProvider}>
                  <Plus size={16} /><span>Add Provider</span>
                </button>
              </section>

              {/* ── Web Secondary Model ─────────────────────────────────── */}
              <section className="settings-section">
                <h3 className="settings-section-title">Web Secondary Model</h3>
                <p className="settings-section-desc">Fast/cheap model for summarizing scraped web pages before they enter the agent's context.</p>
                <div className="settings-field-row">
                  <div className="settings-field-col">
                    <label>Model Name</label>
                    <input className="settings-input" type="text"
                      value={other.web_secondary?.model_name || ''}
                      onChange={e => setOther('web_secondary', 'model_name', e.target.value)}
                      placeholder="deepseek-chat"
                    />
                  </div>
                  <div className="settings-field-col">
                    <label>Max Tokens</label>
                    <input className="settings-input" type="number"
                      value={other.web_secondary?.max_tokens || ''}
                      onChange={e => setOther('web_secondary', 'max_tokens', e.target.value)}
                      placeholder="4096" min="256" max="32768"
                    />
                  </div>
                </div>
                <div className="settings-field-row" style={{ marginTop: '10px' }}>
                  <div className="settings-field-col settings-field-col-wide">
                    <label>Base URL</label>
                    <input className="settings-input" type="text"
                      value={other.web_secondary?.base_url || ''}
                      onChange={e => setOther('web_secondary', 'base_url', e.target.value)}
                      placeholder="https://api.deepseek.com/v1"
                    />
                  </div>
                </div>
                <div className="settings-field-row" style={{ marginTop: '10px' }}>
                  <div className="settings-field-col settings-field-col-wide">
                    <label>API Key</label>
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
                <h3 className="settings-section-title">Agent Behavior</h3>
                <p className="settings-section-desc">Tune loop limits, parallelism, and the default provider.</p>
                <div className="settings-field-row">
                  <div className="settings-field-col">
                    <label>Default Provider</label>
                    <select className="settings-input"
                      value={other.agent?.default_provider || ''}
                      onChange={e => setOther('agent', 'default_provider', e.target.value)}>
                      <option value="">(system default)</option>
                      {allProviders.map(p => (
                        <option key={p.id} value={p.id}>{p.name}{p.custom ? ' (custom)' : ''}</option>
                      ))}
                    </select>
                  </div>
                  <div className="settings-field-col">
                    <label>Max Iterations Per Turn</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.max_iterations || ''}
                      onChange={e => setOther('agent', 'max_iterations', e.target.value)}
                      placeholder="30" min="5" max="200"
                    />
                  </div>
                </div>
                <div className="settings-field-row" style={{ marginTop: '12px' }}>
                  <div className="settings-field-col">
                    <label>Continue Iterations</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.continue_iterations || ''}
                      onChange={e => setOther('agent', 'continue_iterations', e.target.value)}
                      placeholder="30" min="1" max="200"
                    />
                  </div>
                  <div className="settings-field-col">
                    <label>Max Tool Concurrency</label>
                    <input className="settings-input" type="number"
                      value={other.agent?.max_tool_concurrency || ''}
                      onChange={e => setOther('agent', 'max_tool_concurrency', e.target.value)}
                      placeholder="5" min="1" max="20"
                    />
                  </div>
                </div>
                <div className="settings-field-row" style={{ marginTop: '12px' }}>
                  <div className="settings-field-col">
                    <label>Max Terminal Output (chars)</label>
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
                      Code Interpreter Checks
                    </label>
                  </div>
                </div>
              </section>

              {/* ── Persistence note ────────────────────────────────────── */}
              <div className="settings-persistence-note">
                <span className="settings-note-icon">💾</span>
                Stored in <code>/app/data/settings.json</code> (volume-mounted) — survives restarts &amp; rebuilds.
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="settings-footer">
          {message && <span className={`settings-msg settings-msg-${message.type}`}>{message.text}</span>}
          <div className="settings-footer-actions">
            <button className="settings-btn-cancel" onClick={onClose}>Cancel</button>
            <button className="settings-btn-save" onClick={handleSave} disabled={saving || loading}>
              {saving ? <><RefreshCw size={16} className="spin" /> Saving…</> : <><Save size={16} /> Save</>}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
