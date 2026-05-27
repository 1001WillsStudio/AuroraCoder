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
    'field.apiKeyPlaceholderSet': '{provider} API key has been set, enter another to override',
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
      "Fast/cheap model for summarizing scraped web pages before they enter the agent's context — select a provider above.",
    'webSecondary.provider': 'Provider',
    'webSecondary.providerDefault': '(same as agent default)',
    'webSecondary.maxTokens': 'Max Tokens',
    'webSecondary.maxTokensPlaceholder': '4096',

    // ── Agent Behavior section ────────────────────────────────────
    'agent.title': 'Agent Behavior',
    'agent.desc': 'Tune loop limits, parallelism, and the default provider.',
    'agent.defaultProvider': 'Default Provider',
    'agent.systemDefault': '(system default)',
    'agent.customSuffix': ' (custom)',
    'agent.maxIterations': 'Max Iterations Per Turn',
    'agent.maxToolConcurrency': 'Max Tool Concurrency',
    'agent.terminalMaxOutput': 'Terminal Max Output',

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

    // ═══════════════════════════════════════════════════════════════
    // ── App‑wide translations (not just settings) ────────────────
    // ═══════════════════════════════════════════════════════════════

    // ── Welcome screen ─────────────────────────────────────────────
    'welcome.subtitle':
      'Your intelligent coding companion. I can search the web, analyze code, write and edit files, and execute commands to help you build faster.',
    'welcome.example.search': 'Search the web',
    'welcome.example.searchPrompt': 'Search for the latest developments in AI agents and summarize the key trends',
    'welcome.example.analyze': 'Analyze code',
    'welcome.example.analyzePrompt': 'Read my codebase and explain the architecture of the main application',
    'welcome.example.create': 'Create files',
    'welcome.example.createPrompt': 'Create a Python script that fetches weather data from an API',
    'welcome.example.run': 'Run commands',
    'welcome.example.runPrompt': 'Check my Python environment and list installed packages',
    'welcome.footer': 'Start typing or click an example above to begin',

    // ── Chat input ─────────────────────────────────────────────────
    'chat.placeholder.normal': 'Ask me anything…',
    'chat.placeholder.streaming': 'Type to interrupt and redirect the agent…',
    'chat.placeholder.pendingInterrupt': 'Interrupt queued — waiting for safe point…',
    'chat.hint.normal': 'AuroraCoder can search, browse, write code, and execute commands.',
    'chat.hint.streaming': 'Type a message to interrupt and redirect the agent with your new instructions.',
    'chat.title.stop': 'Stop generation',
    'chat.title.interrupt': 'Send and interrupt current generation',
    'chat.title.cancelPending': 'Cancel pending interrupt',
    'chat.continueNewChat': 'Continue in new chat',
    'chat.continueNewChatTitle': 'Ask the agent to summarize progress and continue in a fresh context',
    'chat.interruptQueued': 'Interrupt queued: "{msg}" — Waiting for tool calls to complete…',

    // ── Chat message ───────────────────────────────────────────────
    'chat.reasoning': 'Reasoning {current}/{total}',
    'chat.thinking': 'Thinking…',
    'chat.reasoningShort': 'Reasoning',
    'chat.retryRequest': 'Retry Request',
    'chat.tryAgain': 'Try Again',
    'chat.timeoutHint': 'The request timed out. Click to retry.',

    // ── Sidebar ────────────────────────────────────────────────────
    'sidebar.themeSwitch': 'Switch to {mode} mode',
    'sidebar.settingsTitle': 'Settings — API keys, custom providers',
    'sidebar.newChat': '+ New Chat',
    'sidebar.uploadProject': 'Upload Project',
    'sidebar.uploading': 'Uploading…',
    'sidebar.uploadTitle': 'Select a folder to upload into the workspace',
    'sidebar.taskInstructions': 'Task Instructions',
    'sidebar.taskInstructionsTitle': 'Configure task instructions (prepended to first message)',
    'sidebar.taskInstructionsActive': 'Task instructions active',
    'sidebar.model': 'Model',
    'sidebar.selectModel': 'Select Model',
    'sidebar.noProviders': 'No providers available. Add an API key in Settings ⚙',
    'sidebar.thinkingBadge': 'Thinking',

    // ── File tree ──────────────────────────────────────────────────
    'fileTree.workspace': 'Workspace',
    'fileTree.refresh': 'Refresh file tree',
    'fileTree.loading': 'Loading…',
    'fileTree.empty': 'Workspace is empty',
    'fileTree.emptyHint': 'Files will appear here when created',
    'fileTree.error': 'Failed to load file tree',
    'fileTree.retry': 'Retry',
    'fileTree.download': 'Download',
    'fileTree.export': 'Export as .zip',
    'fileTree.delete': 'Delete',
    'fileTree.deleteConfirm': 'Delete {name}{folderSuffix}?',
    'fileTree.deleteConfirmFolder': ' and all its contents',
    'fileTree.cancel': 'Cancel',

    // ── Conversation history ───────────────────────────────────────
    'history.current': 'Current',
    'history.allHistory': 'All History',
    'history.search': 'Search conversations…',
    'history.noMatches': 'No matches',
    'history.noConversations': 'No conversations yet',
    'history.running': 'Running',
    'history.justNow': 'just now',
    'history.minutesAgo': '{n}m ago',
    'history.hoursAgo': '{n}h ago',
    'history.daysAgo': '{n}d ago',
    'history.untitled': 'Untitled',
    'history.subagent': 'Subagent',
    'history.history': 'History',

    // ── Code panel ─────────────────────────────────────────────────
    'code.codeView': 'Code View',
    'code.refresh': 'Refresh',
    'code.closePanel': 'Close panel',
    'code.noFilesEdited': 'No files edited yet',
    'code.noFilesHint': "When the agent edits or creates files, they'll appear here with diff highlighting.",
    'code.expand': 'Expand',
    'code.minimize': 'Minimize',
    'code.lines': '{n} lines',
    'code.untitled': 'Untitled',
    'code.viewBadge': 'view',
    'code.moreLines': '… {n} more lines',
    'code.viewing': 'Viewing: {path}',

    // ── Tool activity ──────────────────────────────────────────────
    'tool.searching': 'Searching',
    'tool.reading': 'Reading',
    'tool.readingFile': 'Reading file',
    'tool.creatingFile': 'Creating file',
    'tool.editingFile': 'Editing file',
    'tool.deletingFile': 'Deleting file',
    'tool.closingFile': 'Closing file',
    'tool.listingDirectory': 'Listing directory',
    'tool.searchingFiles': 'Searching files',
    'tool.searchingInFiles': 'Searching in files',
    'tool.runningCommand': 'Running command',
    'tool.subagent': 'Subagent',
    'tool.searchingTools': 'Searching tools',
    'tool.usingTool': 'Using tool',
    'tool.stop': 'Stop',
    'tool.stopTitle': 'Stop this tool',
    'tool.viewSubagent': 'View →',
    'tool.showOutput': 'Show output',
    'tool.hideOutput': 'Hide output',
    'tool.moreLines': '… {n} more lines',
    'tool.deletedBadge': '(deleted)',
    'tool.editIndex': 'Edit #{n}',

    // ── Thinking indicator ─────────────────────────────────────────
    'thinking.label': 'Thinking',

    // ── Main app ───────────────────────────────────────────────────
    'app.continueGeneration': 'Continue Generation',
    'app.agentRunning': 'An agent is still running. Stop it or wait for it to finish before starting a new conversation.',
    'app.viewActiveConversation': 'View active conversation',
    'app.subagentRunning': 'Subagent is running…',
    'app.subagentReadOnly': 'Subagent conversation (read-only)',
    'app.backToParent': 'Back to parent',
    'app.mainAgent': 'Main Agent',
    'app.subagent': 'Subagent',
    'app.taskInstructions': 'Task Instructions',
    'app.taskInstructionsDesc':
      'Prepended to the first message of each new conversation. Use this to give the agent persistent context (e.g., project conventions, file locations, safety rules).',
    'app.taskInstructionsPlaceholder':
      'e.g., Always write tests for new code, Use TypeScript strict mode, Keep explanations concise…',
    'app.error': 'Error:',
    'app.toolStopped': 'Tool Stopped:',
    'app.toolStoppedByUser': 'The {tool} operation was terminated by user after running for {time}.',
    'app.toolStoppedByUserSys': 'Tool "{tool}" was stopped by the user.',

    // ── Theme names ────────────────────────────────────────────────
    'theme.light': 'light',
    'theme.dark': 'dark',
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
    'field.apiKeyPlaceholderSet': '{provider} API 密钥已设置，输入新密钥以覆盖',
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
    'webSecondary.desc': '用于在网页内容进入智能体上下文前对其进行摘要的快速/廉价模型 — 从上方选择一个提供者。',
    'webSecondary.provider': '提供者',
    'webSecondary.providerDefault': '（与智能体默认相同）',
    'webSecondary.maxTokens': '最大 Token 数',
    'webSecondary.maxTokensPlaceholder': '4096',

    // ── Agent Behavior section ────────────────────────────────────
    'agent.title': '智能体行为',
    'agent.desc': '调整循环限制、并行度和默认提供者。',
    'agent.defaultProvider': '默认提供者',
    'agent.systemDefault': '(系统默认)',
    'agent.customSuffix': '（自定义）',
    'agent.maxIterations': '每轮最大迭代次数',
    'agent.maxToolConcurrency': '最大工具并发数',
    'agent.terminalMaxOutput': '终端最大输出',

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

    // ═══════════════════════════════════════════════════════════════
    // ── App‑wide translations ─────────────────────────────────────
    // ═══════════════════════════════════════════════════════════════

    // ── Welcome screen ─────────────────────────────────────────────
    'welcome.subtitle':
      '您的智能编程助手。我可以搜索网页、分析代码、编写和编辑文件，并执行命令来帮助您更快地构建项目。',
    'welcome.example.search': '搜索网页',
    'welcome.example.searchPrompt': '搜索 AI 智能体的最新发展并总结关键趋势',
    'welcome.example.analyze': '分析代码',
    'welcome.example.analyzePrompt': '阅读我的代码库并解释主应用程序的架构',
    'welcome.example.create': '创建文件',
    'welcome.example.createPrompt': '创建一个从 API 获取天气数据的 Python 脚本',
    'welcome.example.run': '运行命令',
    'welcome.example.runPrompt': '检查我的 Python 环境并列出已安装的包',
    'welcome.footer': '开始输入或点击上方示例开始使用',

    // ── Chat input ─────────────────────────────────────────────────
    'chat.placeholder.normal': '尽管问我任何问题…',
    'chat.placeholder.streaming': '输入以中断并重定向智能体…',
    'chat.placeholder.pendingInterrupt': '中断已排队 — 等待安全点…',
    'chat.hint.normal': 'AuroraCoder 可以搜索、浏览、编写代码和执行命令。',
    'chat.hint.streaming': '输入消息以中断并重定向智能体。',
    'chat.title.stop': '停止生成',
    'chat.title.interrupt': '发送并中断当前生成',
    'chat.title.cancelPending': '取消待处理的中断',
    'chat.continueNewChat': '在新对话中继续',
    'chat.continueNewChatTitle': '要求智能体总结进度并在新的上下文中继续',
    'chat.interruptQueued': '中断已排队："{msg}" — 等待工具调用完成…',

    // ── Chat message ───────────────────────────────────────────────
    'chat.reasoning': '推理 {current}/{total}',
    'chat.thinking': '思考中…',
    'chat.reasoningShort': '推理',
    'chat.retryRequest': '重试请求',
    'chat.tryAgain': '再试一次',
    'chat.timeoutHint': '请求超时。点击重试。',

    // ── Sidebar ────────────────────────────────────────────────────
    'sidebar.themeSwitch': '切换到{mode}模式',
    'sidebar.settingsTitle': '设置 — API 密钥、自定义提供者',
    'sidebar.newChat': '+ 新对话',
    'sidebar.uploadProject': '上传项目',
    'sidebar.uploading': '上传中…',
    'sidebar.uploadTitle': '选择一个文件夹上传到工作区',
    'sidebar.taskInstructions': '任务指令',
    'sidebar.taskInstructionsTitle': '配置任务指令（附加到第一条消息）',
    'sidebar.taskInstructionsActive': '任务指令已激活',
    'sidebar.model': '模型',
    'sidebar.selectModel': '选择模型',
    'sidebar.noProviders': '没有可用的提供者。请在设置中添加 API 密钥 ⚙',
    'sidebar.thinkingBadge': '思考',

    // ── File tree ──────────────────────────────────────────────────
    'fileTree.workspace': '工作区',
    'fileTree.refresh': '刷新文件树',
    'fileTree.loading': '加载中…',
    'fileTree.empty': '工作区为空',
    'fileTree.emptyHint': '文件创建后会显示在这里',
    'fileTree.error': '加载文件树失败',
    'fileTree.retry': '重试',
    'fileTree.download': '下载',
    'fileTree.export': '导出为 .zip',
    'fileTree.delete': '删除',
    'fileTree.deleteConfirm': '删除 {name}{folderSuffix}？',
    'fileTree.deleteConfirmFolder': ' 及其所有内容',
    'fileTree.cancel': '取消',

    // ── Conversation history ───────────────────────────────────────
    'history.current': '当前',
    'history.allHistory': '全部历史',
    'history.search': '搜索对话…',
    'history.noMatches': '无匹配结果',
    'history.noConversations': '暂无对话',
    'history.running': '运行中',
    'history.justNow': '刚刚',
    'history.minutesAgo': '{n}分钟前',
    'history.hoursAgo': '{n}小时前',
    'history.daysAgo': '{n}天前',
    'history.untitled': '未命名',
    'history.subagent': '子智能体',
    'history.history': '历史记录',

    // ── Code panel ─────────────────────────────────────────────────
    'code.codeView': '代码视图',
    'code.refresh': '刷新',
    'code.closePanel': '关闭面板',
    'code.noFilesEdited': '暂无编辑的文件',
    'code.noFilesHint': '当智能体编辑或创建文件时，文件将在此处以差异高亮显示。',
    'code.expand': '展开',
    'code.minimize': '最小化',
    'code.lines': '{n} 行',
    'code.untitled': '未命名',
    'code.viewBadge': '查看',
    'code.moreLines': '… 还有 {n} 行',
    'code.viewing': '查看：{path}',

    // ── Tool activity ──────────────────────────────────────────────
    'tool.searching': '搜索中',
    'tool.reading': '阅读中',
    'tool.readingFile': '读取文件',
    'tool.creatingFile': '创建文件',
    'tool.editingFile': '编辑文件',
    'tool.deletingFile': '删除文件',
    'tool.closingFile': '关闭文件',
    'tool.listingDirectory': '列出目录',
    'tool.searchingFiles': '搜索文件',
    'tool.searchingInFiles': '在文件中搜索',
    'tool.runningCommand': '运行命令',
    'tool.subagent': '子智能体',
    'tool.searchingTools': '搜索工具',
    'tool.usingTool': '使用工具',
    'tool.stop': '停止',
    'tool.stopTitle': '停止此工具',
    'tool.viewSubagent': '查看 →',
    'tool.showOutput': '显示输出',
    'tool.hideOutput': '隐藏输出',
    'tool.moreLines': '… 还有 {n} 行',
    'tool.deletedBadge': '（已删除）',
    'tool.editIndex': '编辑 #{n}',

    // ── Thinking indicator ─────────────────────────────────────────
    'thinking.label': '思考中',

    // ── Main app ───────────────────────────────────────────────────
    'app.continueGeneration': '继续生成',
    'app.agentRunning': '智能体仍在运行。请停止它或等待其完成后再开始新的对话。',
    'app.viewActiveConversation': '查看活跃对话',
    'app.subagentRunning': '子智能体正在运行…',
    'app.subagentReadOnly': '子智能体对话（只读）',
    'app.backToParent': '返回父级',
    'app.mainAgent': '主智能体',
    'app.subagent': '子智能体',
    'app.taskInstructions': '任务指令',
    'app.taskInstructionsDesc':
      '附加到每个新对话的第一条消息。用于为智能体提供持久上下文（例如项目约定、文件位置、安全规则）。',
    'app.taskInstructionsPlaceholder':
      '例如：始终为新代码编写测试，使用 TypeScript 严格模式，保持解释简洁…',
    'app.error': '错误：',
    'app.toolStopped': '工具已停止：',
    'app.toolStoppedByUser': '{tool} 操作已被用户终止，运行时间为 {time}。',
    'app.toolStoppedByUserSys': '工具 "{tool}" 已被用户停止。',

    // ── Theme names ────────────────────────────────────────────────
    'theme.light': '亮色',
    'theme.dark': '暗色',
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
