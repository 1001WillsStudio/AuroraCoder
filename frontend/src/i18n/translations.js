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
    'field.baseUrl': 'Base URL',
    'field.baseUrlPlaceholderBuiltin': 'Defaults to built-in endpoint',
    'field.baseUrlPlaceholderCustom': 'https://openrouter.ai/api/v1',
    'field.model': 'Model',
    'field.modelPlaceholderBuiltin': 'Defaults to built-in model',
    'field.modelPlaceholderCustom': 'anthropic/claude-sonnet-4',
    'field.thinking': 'Thinking',
    'field.addProvider': 'Add Provider',
    'field.discover': 'Discover',
    'field.discovering': 'Discovering…',
    'field.foundModels': 'Found models',
    'field.filterModels': 'Filter models…',
    'field.clearFilter': 'Clear filter',
    'field.noMatches': 'No models match',

    // ── Web Secondary Model section ───────────────────────────────
    'webSecondary.title': 'Web Secondary Model',
    'webSecondary.desc':
      "Fast/cheap model for summarizing scraped web pages before they enter the agent's context — select a model above.",
    'webSecondary.model': 'Model',
    'webSecondary.modelDefault': '(same as agent default)',
    'webSecondary.maxTokens': 'Max Tokens',
    'webSecondary.maxTokensPlaceholder': '4096',

    // ── Agent Behavior section ────────────────────────────────────
    'agent.title': 'Agent Behavior',
    'agent.desc': 'Tune loop limits, parallelism, and the default model.',
    'agent.defaultModel': 'Default Model',
    'agent.systemDefault': '(system default)',
    'agent.customSuffix': ' (custom)',
    'agent.maxIterations': 'Max Iterations Per Turn',
    'agent.maxToolConcurrency': 'Max Tool Concurrency',
    'agent.terminalMaxOutput': 'Terminal Max Output',
    'agent.saveTrainingData': 'Save Training Data',
    'agent.editMode': 'File Edit Mode',
    'agent.editModeAurora': 'AuroraCoder Style (anchor-based)',
    'agent.editModeNormal': 'Normal Style (string replace)',
    'agent.editModeDesc': 'AuroraCoder: line-range anchor-based editing with indentation auto-fix. Normal: simpler exact-string replacement (like Claude Code).',
    'agent.saveTrainingDataDesc': 'Log each API request/response pair for fine-tuning. Disable to save disk space and reduce I/O overhead.',

    // ── Memory section ──────────────────────────────────────────
    'memory.title': 'Memory',
    'memory.desc': 'Let the agent remember durable facts (preferences, conventions, corrections) across sessions.',
    'memory.enabled': 'Enable Memory',
    'memory.enabledDesc': 'When off, the agent behaves exactly as it does without the memory module — no remember/recall tools, no cross-session context.',
    'memory.browserTitle': 'Stored Memories',
    'memory.browserDesc': 'Review and delete individual memories. Works even while memory is disabled above.',
    'memory.browserLoading': 'Loading…',
    'memory.browserError': 'Failed to load memories.',
    'memory.browserEmpty': 'No memories stored yet.',
    'memory.browserDelete': 'Delete this memory',

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
    'msg.fillFieldsFirst': 'Fill in Base URL and API Key first',
    'msg.fillProviderFirst': 'Fill in the API key first, then click Discover',
    'providers.untitled': 'Unnamed',
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
    'chat.thinking': 'Thinking…',
    'chat.reasoningShort': 'Reasoning',
    'chat.taskInstructionChip': 'Task instruction',
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
    'sidebar.openMenu': 'Open menu',
    'sidebar.closeMenu': 'Close menu',

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
    'tool.readingFiles': 'Reading {n} files',
    'tool.creatingFile': 'Creating file',
    'tool.editingFile': 'Editing file',
    'tool.deletingFile': 'Deleting file',
    'tool.closingFile': 'Closing file',
    'tool.closingFiles': 'Closing {n} files',
    'tool.closingAllExcept': 'Closing all except',
    'tool.closingAll': 'Closing all',
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
    'tool.noOutput': 'No output',
    'tool.deletedBadge': '(deleted)',
    'tool.editIndex': 'Edit #{n}',
    'tool.remembering': 'Remembering',
    'tool.recallingMemory': 'Recalling memory',
    'tool.loggingGap': 'Logging a knowledge gap',
    'tool.forgettingMemory': 'Forgetting a memory',
    'tool.reportingFindings': 'Reporting findings',
    'tool.reportingUnresolved': 'Reporting: could not resolve',
    'tool.memoryVolatile': 'volatile',
    'tool.memoryUpdating': 'Updating memory {id}',
    'tool.memoryPendingReview': 'Pending end-of-session review — not saved yet',

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

    // ── Fork ────────────────────────────────────────────────────
    'app.forkConversation': 'Fork conversation before this message',
    'app.forkAnyway': 'Fork anyway',
    'app.forkWarning': '{count} code/terminal tool(s) ran after this point — workspace state will NOT be restored.',

    // ── Theme names ────────────────────────────────────────────────
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
    'field.baseUrl': '基础 URL',
    'field.baseUrlPlaceholderBuiltin': '默认使用内置端点',
    'field.baseUrlPlaceholderCustom': 'https://openrouter.ai/api/v1',
    'field.model': '模型',
    'field.modelPlaceholderBuiltin': '默认使用内置模型',
    'field.modelPlaceholderCustom': 'anthropic/claude-sonnet-4',
    'field.thinking': '思考模式',
    'field.addProvider': '添加提供者',
    'field.discover': '发现模型',
    'field.discovering': '发现中…',
    'field.foundModels': '已发现的模型',
    'field.filterModels': '筛选模型…',
    'field.clearFilter': '清除筛选',
    'field.noMatches': '无匹配模型',

    // ── Web Secondary Model section ───────────────────────────────
    'webSecondary.title': '网页辅助模型',
    'webSecondary.desc': '用于在网页内容进入智能体上下文前对其进行摘要的快速/廉价模型 — 从上方选择一个模型。',
    'webSecondary.model': '模型',
    'webSecondary.modelDefault': '（与智能体默认相同）',
    'webSecondary.maxTokens': '最大 Token 数',
    'webSecondary.maxTokensPlaceholder': '4096',

    // ── Agent Behavior section ────────────────────────────────────
    'agent.title': '智能体行为',
    'agent.desc': '调整循环限制、并行度和默认模型。',
    'agent.defaultModel': '默认模型',
    'agent.systemDefault': '(系统默认)',
    'agent.customSuffix': '（自定义）',
    'agent.maxIterations': '每轮最大迭代次数',
    'agent.maxToolConcurrency': '最大工具并发数',
    'agent.terminalMaxOutput': '终端最大输出',
    'agent.saveTrainingData': '保存训练数据',
    'agent.editMode': '文件编辑模式',
    'agent.editModeAurora': 'AuroraCoder 风格（锚点定位）',
    'agent.editModeNormal': '普通风格（字符串替换）',
    'agent.editModeDesc': 'AuroraCoder 风格：基于行号和锚点的精准编辑，支持自动缩进修复。普通风格：更简单的精确字符串替换（类似 Claude Code）。',
    'agent.saveTrainingDataDesc': '记录每次 API 请求/响应用于微调。关闭可节省磁盘空间和 I/O 开销。',

    // ── Memory section ──────────────────────────────────────────
    'memory.title': '记忆',
    'memory.desc': '让智能体在跨会话之间记住持久性事实（偏好、约定、更正）。',
    'memory.enabled': '启用记忆',
    'memory.enabledDesc': '关闭时，智能体的行为与没有记忆模块时完全一致 — 没有 remember/recall 工具，没有跨会话上下文。',
    'memory.browserTitle': '已存储的记忆',
    'memory.browserDesc': '查看并删除单条记忆。即使上方的记忆功能已关闭，此处仍可使用。',
    'memory.browserLoading': '加载中…',
    'memory.browserError': '加载记忆失败。',
    'memory.browserEmpty': '尚无已存储的记忆。',
    'memory.browserDelete': '删除此记忆',

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
    'msg.fillFieldsFirst': '请先填写基础 URL 和 API 密钥',
    'msg.fillProviderFirst': '请先填写 API 密钥，然后点击发现',
    'providers.untitled': '未命名',

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
    'chat.thinking': '思考中…',
    'chat.reasoningShort': '推理',
    'chat.taskInstructionChip': '任务指令',
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
    'sidebar.openMenu': '打开菜单',
    'sidebar.closeMenu': '关闭菜单',

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
    'tool.readingFiles': '读取 {n} 个文件',
    'tool.creatingFile': '创建文件',
    'tool.editingFile': '编辑文件',
    'tool.deletingFile': '删除文件',
    'tool.closingFile': '关闭文件',
    'tool.closingFiles': '关闭 {n} 个文件',
    'tool.closingAllExcept': '关闭除以下之外的全部',
    'tool.closingAll': '关闭全部',
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
    'tool.noOutput': '无输出',
    'tool.deletedBadge': '（已删除）',
    'tool.editIndex': '编辑 #{n}',
    'tool.remembering': '记忆中',
    'tool.recallingMemory': '回忆记忆',
    'tool.loggingGap': '记录知识缺口',
    'tool.forgettingMemory': '删除记忆',
    'tool.reportingFindings': '报告调查结果',
    'tool.reportingUnresolved': '报告：未能解决',
    'tool.memoryVolatile': '易变',
    'tool.memoryUpdating': '更新记忆 {id}',
    'tool.memoryPendingReview': '待会话结束审核 — 尚未保存',

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

    // ── Fork ────────────────────────────────────────────────────
    'app.forkConversation': '在此消息之前分叉对话',
    'app.forkAnyway': '仍然分叉',
    'app.forkWarning': '{count} 个工具在此点之后运行 — 工作区状态将不会恢复。',

    // ── Theme names ────────────────────────────────────────────────
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
