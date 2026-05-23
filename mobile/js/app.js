/**
 * ThinkWithTool Mobile — Main Application Module
 *
 * Orchestrates authentication, chat, drawer/menu, provider selection,
 * and conversation management. Entry point triggered on DOMContentLoaded.
 */

const App = (() => {
  // ── DOM references ─────────────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const loginScreen = $('#login-screen');
  const appScreen = $('#app-screen');
  const loginForm = $('#login-form');
  const loginBtn = $('#login-btn');
  const loginBtnText = $('#login-btn-text');
  const loginSpinner = $('#login-spinner');
  const loginError = $('#login-error');
  const passwordInput = $('#password-input');
  const togglePw = $('#toggle-password');
  const eyeIcon = $('#eye-icon');
  const headerProvider = $('#header-provider');
  const providerSelect = $('#provider-select');
  const conversationList = $('#conversation-list');
  const refreshConvosBtn = $('#refresh-convos-btn');
  const menuBtn = $('#menu-btn');
  const drawer = $('#drawer');
  const drawerOverlay = $('#drawer-overlay');
  const drawerCloseBtn = $('#drawer-close-btn');
  const newChatBtn = $('#new-chat-btn');
  const logoutBtn = $('#logout-btn');
  const chatInput = $('#chat-input');

  // ── State ──────────────────────────────────────────────────────────────
  let providers = [];
  let conversations = [];
  let activeConversationId = null;

  // ── Login flow ─────────────────────────────────────────────────────────
  function _setLoginLoading(loading) {
    loginBtn.disabled = loading;
    loginBtnText.classList.toggle('hidden', loading);
    loginSpinner.classList.toggle('hidden', !loading);
  }

  function _showLoginError(msg) {
    loginError.textContent = msg;
    loginError.classList.remove('hidden');
  }

  function _hideLoginError() {
    loginError.classList.add('hidden');
  }

  async function _attemptAutoLogin() {
    // Check if we already have a valid token
    const ok = await Auth.checkAuth();
    if (ok) {
      _showApp();
      return true;
    }
    return false;
  }

  async function _handleLogin(e) {
    e.preventDefault();
    _hideLoginError();
    _setLoginLoading(true);

    try {
      await Auth.login(passwordInput.value);
      passwordInput.value = '';
      _showApp();
    } catch (err) {
      _showLoginError(err.message || 'Login failed');
    } finally {
      _setLoginLoading(false);
    }
  }

  // ── App screen ─────────────────────────────────────────────────────────
  function _showApp() {
    loginScreen.classList.remove('active');
    appScreen.classList.add('active');
    _loadProviders();
    _loadConversations();
    chatInput.focus();
  }

  function _showLogin() {
    appScreen.classList.remove('active');
    loginScreen.classList.add('active');
    Auth.logout();
    passwordInput.focus();
  }

  // ── Providers ──────────────────────────────────────────────────────────
  async function _loadProviders() {
    try {
      const data = await API.getProviders();
      providers = data.providers || [];
      _renderProviderSelect();
    } catch (err) {
      console.warn('Failed to load providers:', err);
    }
  }

  function _renderProviderSelect() {
    providerSelect.innerHTML = '';
    for (const p of providers) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = p.name || p.id;
      if (Chat.getProvider() && p.id === Chat.getProvider()) {
        opt.selected = true;
      }
      providerSelect.appendChild(opt);
    }
    // Update header
    const currentP = Chat.getProvider();
    if (currentP) {
      const p = providers.find(x => x.id === currentP);
      headerProvider.textContent = p ? (p.name || p.id) : currentP;
    }
  }

  function _onProviderChange() {
    const provId = providerSelect.value;
    Chat.setProvider(provId);
    const p = providers.find(x => x.id === provId);
    headerProvider.textContent = p ? (p.name || p.id) : provId;
  }

  // ── Conversations ──────────────────────────────────────────────────────
  async function _loadConversations() {
    try {
      const data = await API.listConversations();
      conversations = data.conversations || [];
      _renderConversationList();
    } catch (err) {
      console.warn('Failed to load conversations:', err);
    }
  }

  function _renderConversationList() {
    if (conversations.length === 0) {
      conversationList.innerHTML = '<div class="conversation-list-empty">No conversations yet</div>';
      return;
    }

    conversationList.innerHTML = '';
    for (const conv of conversations) {
      const item = document.createElement('div');
      item.className = 'conversation-item';
      if (conv.id === activeConversationId) {
        item.classList.add('active');
      }

      const title = conv.frontend_title || conv.title || 'Untitled';
      const date = conv.updated_at ? new Date(conv.updated_at).toLocaleDateString() : '';

      item.innerHTML = `
        <span class="conversation-item-title">${_escapeHtml(title)}</span>
        <span class="conversation-item-meta">
          <span>${date}</span>
          <span class="conversation-item-status ${conv.status || ''}">${conv.status || ''}</span>
        </span>
      `;

      item.addEventListener('click', () => _selectConversation(conv.id));
      conversationList.appendChild(item);
    }
  }

  async function _selectConversation(cid) {
    if (Chat.isStreaming()) {
      _showToast('Stop current generation first', true);
      return;
    }

    try {
      await Chat.loadConversation(cid);
      activeConversationId = cid;
      _renderConversationList();
      _closeDrawer();
    } catch (err) {
      // Error already handled by Chat
    }
  }

  // ── Drawer / Menu ──────────────────────────────────────────────────────
  function _openDrawer() {
    drawer.classList.remove('hidden');
    drawerOverlay.classList.remove('hidden');
    _loadConversations();
  }

  function _closeDrawer() {
    drawer.classList.add('hidden');
    drawerOverlay.classList.add('hidden');
  }

  // ── Toast ──────────────────────────────────────────────────────────────
  function _showToast(message, isError = false) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.className = `toast ${isError ? 'error' : ''}`;
    toast.classList.remove('hidden');
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => toast.classList.add('hidden'), 3000);
  }

  // ── Escape HTML ────────────────────────────────────────────────────────
  function _escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  // ── Message Observer (auto-scroll) ─────────────────────────────────────
  function _observeMessages() {
    const messagesEnd = $('#messages-end');
    if (!messagesEnd) return;

    const observer = new MutationObserver(() => {
      if (Chat.isStreaming()) {
        requestAnimationFrame(() => {
          messagesEnd.scrollIntoView({ behavior: 'auto', block: 'end' });
        });
      }
    });

    const messagesContainer = $('#messages-container');
    if (messagesContainer) {
      observer.observe(messagesContainer, {
        childList: true,
        subtree: true,
        characterData: true,
      });
    }
  }

  // ── Init ───────────────────────────────────────────────────────────────
  async function init() {
    // Event listeners
    loginForm.addEventListener('submit', _handleLogin);

    togglePw.addEventListener('click', () => {
      const isPassword = passwordInput.type === 'password';
      passwordInput.type = isPassword ? 'text' : 'password';
      // Update eye icon
      if (isPassword) {
        eyeIcon.innerHTML = '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>';
      } else {
        eyeIcon.innerHTML = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
      }
    });

    // Menu / Drawer
    menuBtn.addEventListener('click', _openDrawer);
    drawerCloseBtn.addEventListener('click', _closeDrawer);
    drawerOverlay.addEventListener('click', _closeDrawer);

    // New chat
    newChatBtn.addEventListener('click', () => {
      Chat.newChat();
      activeConversationId = null;
      _renderConversationList();
    });

    // Provider change
    providerSelect.addEventListener('change', _onProviderChange);

    // Refresh conversations
    refreshConvosBtn.addEventListener('click', _loadConversations);

    // Logout
    logoutBtn.addEventListener('click', () => {
      if (Chat.isStreaming()) {
        Chat.stopGeneration();
      }
      _closeDrawer();
      _showLogin();
    });

    // Init Chat module (event listeners for input)
    Chat.init();

    // Observe messages for auto-scroll
    _observeMessages();

    // Handle page visibility (reconnect SSE when returning)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        // Refresh conversations on return
        _loadConversations();
      }
    });

    // Handle Back/Forward browser buttons on drawer
    window.addEventListener('popstate', (e) => {
      if (!drawer.classList.contains('hidden')) {
        _closeDrawer();
      }
    });

    // Attempt auto-login
    const autoOk = await _attemptAutoLogin();
    if (!autoOk) {
      // Show login screen if not authenticated
      // (The server may or may not require auth depending on ACCESS_PASSWORD)
      const needsAuth = await _checkServerAuth();
      if (needsAuth) {
        loginScreen.classList.add('active');
        passwordInput.focus();
      } else {
        // No auth needed — go straight to app
        _showApp();
      }
    }
  }

  async function _checkServerAuth() {
    // Try fetching providers without auth — if 401, auth is required
    try {
      const resp = await fetch('/api/providers');
      if (resp.status === 401) return true;
      if (resp.ok) return false;
    } catch (e) {
      return false;
    }
    return false;
  }

  // Public API
  return { init };
})();

// ── Bootstrap ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  App.init().catch(err => console.error('App init error:', err));
});
