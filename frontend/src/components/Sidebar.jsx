import React from 'react'
import { Sun, Moon, ChevronDown, Upload, FileText, Settings } from 'lucide-react'
import useLanguage from '../hooks/useLanguage'
import FileTree from './FileTree'
import ConversationHistory from './ConversationHistory'

/**
 * Full sidebar: logo, theme toggle, new chat, upload,
 * task instructions button, file tree, conversation history, model selector.
 */
export default function Sidebar({
  theme,
  onToggleTheme,
  onNewChat,
  uploadInputRef,
  isUploading,
  onUploadProject,
  taskInstructionsBtnRef,
  showTaskInstructions,
  onToggleTaskInstructions,
  systemPrompt,
  fileTreeRefreshTrigger,
  isStreaming,
  onFileClick,
  conversationId,
  onLoadConversation,
  historyRefreshTrigger,
  historyCloseTrigger,
  onDrawerToggle,
  providers,
  providersLoading,
  selectedProvider,
  onSelectProvider,
  showProviderDropdown,
  onToggleProviderDropdown,
  onOpenSettings,
}) {
  const { t } = useLanguage()
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="logo">
          <img src="/assets/logo.png" alt="1001 Wills AI Lab" className="logo-image" />
          <span className="logo-text">AuroraCoder</span>
        </div>
        <div className="sidebar-header-actions">
          <button
            className="theme-toggle"
            onClick={onToggleTheme}
            title={t('sidebar.themeSwitch', { mode: theme === 'dark' ? t('theme.light') : t('theme.dark') })}
          >
            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button
            className="settings-gear-btn"
            onClick={onOpenSettings}
            title={t('sidebar.settingsTitle')}
          >
            <Settings size={18} />
          </button>
        </div>
      </div>
      
      <div className="sidebar-actions">
        <button className="new-chat-btn" onClick={onNewChat}>
          <span>{t('sidebar.newChat')}</span>
        </button>
        <button
          className="load-session-btn"
          onClick={() => uploadInputRef.current?.click()}
          disabled={isUploading}
          title={t('sidebar.uploadTitle')}
        >
          <Upload size={16} />
          <span>{isUploading ? t('sidebar.uploading') : t('sidebar.uploadProject')}</span>
        </button>
        <input
          ref={uploadInputRef}
          type="file"
          webkitdirectory=""
          directory=""
          multiple
          style={{ display: 'none' }}
          onChange={onUploadProject}
        />
      </div>

      {/* Task Instructions — clickable button, toggles drawer */}
      <div className="sidebar-section task-instructions-section">
        <button
          ref={taskInstructionsBtnRef}
          className="load-session-btn"
          onClick={onToggleTaskInstructions}
          title={t('sidebar.taskInstructionsTitle')}
        >
          <FileText size={16} />
          <span>{t('sidebar.taskInstructions')}</span>
          {systemPrompt && (
            <span className="system-prompt-indicator" title={t('sidebar.taskInstructionsActive')}>
              ●
            </span>
          )}
        </button>
      </div>

      {/* File Tree - Workspace Explorer */}
      <div className="sidebar-section file-tree-section">
        <FileTree 
          onFileClick={onFileClick}
          isStreaming={isStreaming}
          refreshTrigger={fileTreeRefreshTrigger}
        />
      </div>

      <div className="sidebar-footer">
        <ConversationHistory
          currentConversationId={conversationId}
          onSelect={onLoadConversation}
          refreshTrigger={historyRefreshTrigger}
          closeTrigger={historyCloseTrigger}
          onDrawerToggle={onDrawerToggle}
        />
        <div className="model-selector">
          <span className="model-label">{t('sidebar.model')}</span>
          <div className="provider-dropdown-container">
            <button
              className="provider-dropdown-btn"
              onClick={onToggleProviderDropdown}
              disabled={isStreaming}
            >
              <span className="provider-name">
                {providers.find(p => p.id === selectedProvider)?.name || selectedProvider || t('sidebar.selectModel')}
              </span>
              <ChevronDown size={16} className={showProviderDropdown ? 'rotated' : ''} />
            </button>
            {showProviderDropdown && (
              <div className="provider-dropdown-menu">
                {providers.length === 0 ? (
                  <div className="provider-option provider-option-empty">
                    {providersLoading ? t('sidebar.loadingProviders') || 'Loading models...' : t('sidebar.noProviders')}
                  </div>
                ) : (
                  providers.map(provider => (
                    <button
                      key={provider.id}
                      className={`provider-option ${provider.id === selectedProvider ? 'selected' : ''}`}
                      onClick={() => {
                        onSelectProvider(provider.id)
                      }}
                    >
                      <div className="provider-option-name">{provider.name}</div>
                      <div className="provider-option-desc">{provider.description}</div>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </aside>
  )
}
