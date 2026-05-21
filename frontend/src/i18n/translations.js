/**
 * Translations for the Settings panel (and extensible to other components).
 *
 * Supported languages:
 *   en — English (default / fallback)
 *   zh — Chinese (Simplified)
 *
 * Usage:
 *   import { t } from '../hooks/useLanguage'
 *   t('settings.title')  →  "Settings" or "设置"
 *
 * To add a new language, add a new block under LANG and provide
 * translations for every key. Missing keys fall back to 'en'.
 */

const LANG = {
  en: {
    // ── Settings header ───────────────────────────────────────────
    'settings.title': 'Settings',
    'settings.subtitle':
      'All providers are OpenAI-compatible. Pre-configured ones are built-in defaults; add your own endpoints below. Settings survive Docker restarts & rebuilds.',
    'settings.close': 'Close',
    'settings.loading': 'Loading…',
    'settings.loadError': 'Failed to load settings',

    // ── Language selector ─────────────────────────────────────────
    'language.label': 'Language',

    // ── Providers section ─────────────────────────────────────────
    'providers.title': 'Providers',
    'providers.desc': 'All providers use the OpenAI-compatible API protocol.',
    'providers.badgeBuiltin': 'built-in',
    'providers.badgeCustom': 'custom',
    'providers.remove': 'Remove',

    // ── Provider fields ───────────────────────────────────────────
    'field.apiKey': 'API Key',
    'field.apiKeyPlaceholder': 'Overrides env var',
    'field.apiKeyPlaceholderSet': '••••••••••••••••',
    'field.show': 'Show',
    'field.hide': 'Hide',
    'field.displayName': 'Display Name',
    'field.displayNamePlaceholder': 'My OpenRouter',
    'field.providerId': 'Provider ID',
    'field.providerIdPlaceholder': 'my-openrouter',
    'field.baseUrl': 'Base URL',
    'field.baseUrlPlaceholderBuiltin': 'Defaults to built-in endpoint',
    'field.baseUrlPlaceholderCustom': 'https://openrouter.ai/api/v1',
    'field.model': 'Model',
    'field.modelPlaceholderBuiltin': 'Defaults to built-in model',
    'field.modelPlaceholderCustom': 'anthropic/claude-sonnet-4',
    'field.thinking': 'Thinking',
    'field.addProvider': 'Add Provider',

    // ── Web Secondary Model section ───────────────────────────────
    'webSecondary.title': 'Web Secondary Model',
    'webSecondary.desc':
      "Fast/cheap model for summarizing scraped web pages before they enter the agent's context.",
    'webSecondary.modelName': 'Model Name',
    'webSecondary.modelNamePlaceholder': 'deepseek-chat',
    'webSecondary.maxTokens': 'Max Tokens',
    'webSecondary.maxTokensPlaceholder': '4096',
    'webSecondary.baseUrlPlaceholder': 'https://api.deepseek.com/v1',

    // ── Agent Behavior section ────────────────────────────────────
    'agent.title': 'Agent Behavior',
    'agent.desc': 'Tune loop limits, parallelism, and the default provider.',
    'agent.defaultProvider': 'Default Provider',
    'agent.systemDefault': '(system default)',
    'agent.customSuffix': ' (custom)',
    'agent.maxIterations': 'Max Iterations Per Turn',
    'agent.continueIterations': 'Continue Iterations',
    'agent.maxToolConcurrency': 'Max Tool Concurrency',
    'agent.terminalMaxOutput': 'Max Terminal Output (chars)',
    'agent.codeInterpreterChecks': 'Code Interpreter Checks',

    // ── Persistence note ──────────────────────────────────────────
    'persistence.note':
      'Stored in <code>/app/data/settings.json</code> (volume-mounted) — survives restarts & rebuilds.',

    // ── Footer / buttons ──────────────────────────────────────────
    'footer.cancel': 'Cancel',
    'footer.save': 'Save',
    'footer.saving': 'Saving…',

    // ── Messages ──────────────────────────────────────────────────
    'msg.validationError': 'Fix validation errors before saving',
    'msg.saved': 'Settings saved',
    'msg.saveFailed': 'Save failed',
    'msg.nameRequired': 'Name required',
    'msg.baseUrlRequired': 'Base URL required',
    'msg.apiKeyRequired': 'API key required',
    'msg.modelRequired': 'Model required',
  },

  zh: {
    // ── Settings header ───────────────────────────────────────────
    'settings.title': '设置',
    'settings.subtitle':
      '所有提供者均兼容 OpenAI 协议。预配置的为内置默认值；您可以在下方添加自定义端点。设置在 Docker 重启和重建后仍然有效。',
    'settings.close': '关闭',
    'settings.loading': '加载中…',
    'settings.loadError': '加载设置失败',

    // ── Language selector ─────────────────────────────────────────
    'language.label': '语言',

    // ── Providers section ─────────────────────────────────────────
    'providers.title': '提供者',
    'providers.desc': '所有提供者均使用 OpenAI 兼容的 API 协议。',
    'providers.badgeBuiltin': '内置',
    'providers.badgeCustom': '自定义',
    'providers.remove': '移除',

    // ── Provider fields ───────────────────────────────────────────
    'field.apiKey': 'API 密钥',
    'field.apiKeyPlaceholder': '覆盖环境变量',
    'field.apiKeyPlaceholderSet': '••••••••••••••••',
    'field.show': '显示',
    'field.hide': '隐藏',
    'field.displayName': '显示名称',
    'field.displayNamePlaceholder': '我的 OpenRouter',
    'field.providerId': '提供者 ID',
    'field.providerIdPlaceholder': 'my-openrouter',
    'field.baseUrl': '基础 URL',
    'field.baseUrlPlaceholderBuiltin': '默认使用内置端点',
    'field.baseUrlPlaceholderCustom': 'https://openrouter.ai/api/v1',
    'field.model': '模型',
    'field.modelPlaceholderBuiltin': '默认使用内置模型',
    'field.modelPlaceholderCustom': 'anthropic/claude-sonnet-4',
    'field.thinking': '思考模式',
    'field.addProvider': '添加提供者',

    // ── Web Secondary Model section ───────────────────────────────
    'webSecondary.title': '网页辅助模型',
    'webSecondary.desc': '用于在网页内容进入智能体上下文前对其进行摘要的快速/廉价模型。',
    'webSecondary.modelName': '模型名称',
    'webSecondary.modelNamePlaceholder': 'deepseek-chat',
    'webSecondary.maxTokens': '最大 Token 数',
    'webSecondary.maxTokensPlaceholder': '4096',
    'webSecondary.baseUrlPlaceholder': 'https://api.deepseek.com/v1',

    // ── Agent Behavior section ────────────────────────────────────
    'agent.title': '智能体行为',
    'agent.desc': '调整循环限制、并行度和默认提供者。',
    'agent.defaultProvider': '默认提供者',
    'agent.systemDefault': '(系统默认)',
    'agent.customSuffix': '（自定义）',
    'agent.maxIterations': '每轮最大迭代次数',
    'agent.continueIterations': '继续迭代次数',
    'agent.maxToolConcurrency': '最大工具并发数',
    'agent.terminalMaxOutput': '终端最大输出（字符）',
    'agent.codeInterpreterChecks': '代码解释器检查',

    // ── Persistence note ──────────────────────────────────────────
    'persistence.note':
      '存储在 <code>/app/data/settings.json</code>（卷挂载）— 重启和重建后仍然有效。',

    // ── Footer / buttons ──────────────────────────────────────────
    'footer.cancel': '取消',
    'footer.save': '保存',
    'footer.saving': '保存中…',

    // ── Messages ──────────────────────────────────────────────────
    'msg.validationError': '请先修正验证错误再保存',
    'msg.saved': '设置已保存',
    'msg.saveFailed': '保存失败',
    'msg.nameRequired': '名称不能为空',
    'msg.baseUrlRequired': '基础 URL 不能为空',
    'msg.apiKeyRequired': 'API 密钥不能为空',
    'msg.modelRequired': '模型不能为空',
  },
}

/** Language display names (for the selector). */
export const LANG_LABELS = {
  en: 'English',
  zh: '中文',
}

/** Default language. */
export const DEFAULT_LANG = 'en'

/** Return the translation dict for a given language code. */
export function getTranslations(lang) {
  return LANG[lang] || LANG[DEFAULT_LANG]
}

export default LANG
