/**
 * ThinkWithTool Mobile — Chat Module
 * 
 * Manages chat state, SSE streaming, message rendering, and UI interactions.
 * All DOM manipulation for the chat interface lives here.
 */

const Chat = (() => {
  // ── State ────────────────────────────────────────────────────────────
  let conversationId = null;
  let provider = null;
  let abortController = null;
  let isStreaming = false;
  let statusTimerInterval = null;
  let statusStartTime = null;
  let resumeNeeded = false;

  // ── DOM Cache ────────────────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const chatArea = $('#chat-area');
  const messagesContainer = $('#messages-container');
  const messagesEnd = $('#messages-end');
  const welcomeScreen = $('#welcome-screen');
  const chatInput = $('#chat-input');
  const sendBtn = $('#send-btn');
  const sendIcon = $('#send-icon');
  const stopBtn = $('#stop-btn');
  const statusBar = $('#status-bar');
  const statusText = $('#status-text');
  const statusTimer = $('#status-timer');
  const continueBar = $('#continue-bar');
  const continueBtn = $('#continue-btn');
  const headerProvider = $('#header-provider');

  // ── Markdown-ish formatting ─────────────────────────────────────────
  function _formatContent(text) {
    if (!text) return '';
    let html = text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    
    // Code blocks ```...```
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) => {
      return `<pre><code>${code.trim()}</code></pre>`;
    });
    
    // Inline code `...`
    html = html.replace(/`([^`]+?)`/g, '<code>$1</code>');
    
    // Bold **...** 
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    
    // Italic *...*
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    
    // Headers
    html = html.replace(/^### (.+)$/gm, '<h4>$1</h4>');
    html = html.replace(/^## (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^# (.+)$/gm, '<h2>$1</h2>');
    
    // Unordered lists
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
    
    // Line breaks → paragraphs
    const paragraphs = html.split(/\n\n+/);
    html = paragraphs.map(p => {
      p = p.trim();
      if (p.startsWith('<pre>') || p.startsWith('<ul>') || 
          p.startsWith('<h2>') || p.startsWith('<h3>') || p.startsWith('<h4>')) {
        return p;
      }
      p = p.replace(/\n/g, '<br>');
      return `<p>${p}</p>`;
    }).join('\n');
    
    return html;
  }

  // ── Message rendering ───────────────────────────────────────────────
  function _createMessageElement(msg, index = -1) {
    const el = document.createElement('div');
    el.className = `message ${msg.role}`;
    el.dataset.index = String(index);

    // Role label
    const roleEl = document.createElement('div');
    roleEl.className = 'message-role';
    if (msg.role === 'assistant') {
      roleEl.innerHTML = '🤖 Assistant';
    } else if (msg.role === 'user') {
      roleEl.innerHTML = '👤 You';
    } else if (msg.role === 'error') {
      roleEl.innerHTML = '⚠️ Error';
    }
    el.appendChild(roleEl);

    // Activities (for assistant messages with tool calls / thinking)
    if (msg.activities && msg.activities.length > 0) {
      const actsEl = document.createElement('div');
      actsEl.className = 'message-activities';
      for (const act of msg.activities) {
        const actEl = document.createElement('div');
        actEl.className = `activity ${act.type}`;
        
        if (act.type === 'thinking') {
          actEl.textContent = `💭 ${act.content || ''}`;
          actEl.title = act.content || '';
        } else if (act.type === 'tool_call') {
          let argsDisplay = '';
          try {
            const parsed = typeof act.arguments === 'string' ? JSON.parse(act.arguments) : act.arguments;
            // Show key arguments concisely
            const keys = Object.keys(parsed || {});
            if (keys.length > 0) {
              const parts = keys.slice(0, 3).map(k => {
                const v = parsed[k];
                const vs = typeof v === 'string' ? (v.length > 40 ? v.slice(0, 40) + '…' : v) : JSON.stringify(v);
                return `${k}: ${vs}`;
              });
              argsDisplay = parts.join(', ');
              if (keys.length > 3) argsDisplay += '…';
            }
          } catch { argsDisplay = String(act.arguments || '').slice(0, 60); }
          
          actEl.innerHTML = `<span class="tc-badge">🔧 ${act.name}</span>`;
          if (argsDisplay) {
            const argsSpan = document.createElement('span');
            argsSpan.className = 'tc-args';
            argsSpan.textContent = argsDisplay;
            actEl.appendChild(argsSpan);
          }
        } else if (act.type === 'tool_result') {
          actEl.textContent = act.content || '';
          if (act.content && (act.content.includes('error') || act.content.includes('Error'))) {
            actEl.classList.add('error-result');
          }
        }
        actsEl.appendChild(actEl);
      }
      el.appendChild(actsEl);
    }

    // Content (formatted)
    if (msg.content) {
      const contentEl = document.createElement('div');
      contentEl.className = 'message-content';
      contentEl.innerHTML = _formatContent(msg.content);
      el.appendChild(contentEl);
    }

    return el;
  }

  // ── Render all messages ─────────────────────────────────────────────
  function _renderMessages(messages) {
    // Clear existing messages
    messagesContainer.innerHTML = '';
    
    if (!messages || messages.length === 0) {
      welcomeScreen.classList.remove('hidden');
      continueBar.classList.add('hidden');
      return;
    }
    
    welcomeScreen.classList.add('hidden');
    
    // Check if the last message is a partial (streaming) assistant message
    const needsContinue = messages.length > 0 && 
      messages[messages.length - 1]?.role === 'assistant' &&
      messages[messages.length - 1]?.content === '' &&
      (!messages[messages.length - 1]?.activities || messages[messages.length - 1]?.activities.length === 0);

    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      const el = _createMessageElement(msg, i);
      messagesContainer.appendChild(el);
    }
    
    // Show continue bar if the last assistant message is empty
    if (needsContinue && !isStreaming) {
      continueBar.classList.remove('hidden');
      resumeNeeded = true;
    } else {
      continueBar.classList.add('hidden');
      resumeNeeded = false;
    }
    
    _scrollToBottom();
  }

  // ── Scroll ──────────────────────────────────────────────────────────
  function _scrollToBottom(smooth = true) {
    requestAnimationFrame(() => {
      if (smooth) {
        messagesEnd.scrollIntoView({ behavior: 'smooth', block: 'end' });
      } else {
        messagesEnd.scrollIntoView({ behavior: 'auto', block: 'end' });
      }
    });
  }

  // ── Status bar management ───────────────────────────────────────────
  function _startStatusTimer() {
    statusStartTime = Date.now();
    statusBar.classList.remove('hidden');
    _updateStatusTimer();
    statusTimerInterval = setInterval(_updateStatusTimer, 1000);
  }

  function _updateStatusTimer() {
    if (!statusStartTime) return;
    const elapsed = Math.floor((Date.now() - statusStartTime) / 1000);
    const mins = Math.floor(elapsed / 60);
    const secs = elapsed % 60;
    statusTimer.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }

  function _stopStatusTimer() {
    clearInterval(statusTimerInterval);
    statusTimerInterval = null;
    statusStartTime = null;
    statusBar.classList.add('hidden');
  }

  // ── Set input state ─────────────────────────────────────────────────
  function _setStreaming(state) {
    isStreaming = state;
    if (state) {
      sendBtn.classList.add('hidden');
      stopBtn.classList.remove('hidden');
      chatInput.disabled = true;
    } else {
      sendBtn.classList.remove('hidden');
      stopBtn.classList.add('hidden');
      chatInput.disabled = false;
    }
  }

  // ── Show toast ──────────────────────────────────────────────────────
  function _showToast(message, isError = false) {
    const toast = $('#toast');
    toast.textContent = message;
    toast.className = `toast ${isError ? 'error' : ''}`;
    toast.classList.remove('hidden');
    clearTimeout(toast._timeout);
    toast._timeout = setTimeout(() => toast.classList.add('hidden'), 3000);
  }

  // ── Send message ────────────────────────────────────────────────────
  async function sendMessage(message, convId, prov) {
    if (isStreaming) return;
    if (!message || !message.trim()) return;
    
    conversationId = convId || null;
    provider = prov || null;
    
    _setStreaming(true);
    _startStatusTimer();
    statusText.textContent = 'Generating...';
    
    // Add user message to UI immediately
    welcomeScreen.classList.add('hidden');
    continueBar.classList.add('hidden');
    const userEl = _createMessageElement({ role: 'user', content: message });
    messagesContainer.appendChild(userEl);
    _scrollToBottom();
    
    // Clear input
    chatInput.value = '';
    chatInput.style.height = 'auto';
    
    abortController = new AbortController();
    
    let lastMessages = null;
    let streamEnded = false;
    
    try {
      await API.streamChat(
        message,
        conversationId,
        {
          onMessages: (msgs, status, data) => {
            lastMessages = msgs;
            conversationId = data.conversation_id || conversationId;
            provider = data.provider || provider;
            _renderMessages(msgs);
            statusText.textContent = status === 'running' ? 'Generating...' : 
                                     status === 'thinking' ? 'Thinking...' : 'Generating...';
            if (provider) {
              headerProvider.textContent = provider;
            }
          },
          onDone: (data) => {
            lastMessages = data.messages;
            conversationId = data.conversation_id || conversationId;
            provider = data.provider || provider;
            _renderMessages(data.messages);
            streamEnded = true;
            _endStream();
          },
          onError: (data) => {
            _showToast(data.message || 'An error occurred', true);
            streamEnded = true;
            _endStream();
          },
          onStatusChange: (status) => {
            if (status === 'error') {
              statusText.textContent = 'Error';
            }
          },
        },
        abortController.signal,
        null,  // existingMessages
        provider
      );
    } catch (err) {
      if (err.name === 'AbortError') {
        statusText.textContent = 'Stopped';
        _showToast('Generation stopped');
      } else {
        _showToast(err.message || 'Connection error', true);
      }
      _endStream();
    }
  }

  // ── Continue/resume ─────────────────────────────────────────────────
  async function continueGeneration() {
    if (isStreaming) return;
    if (!conversationId) return;
    
    _setStreaming(true);
    _startStatusTimer();
    statusText.textContent = 'Continuing...';
    continueBar.classList.add('hidden');
    
    abortController = new AbortController();
    
    try {
      await API.streamChat(
        null,  // no message — continue
        conversationId,
        {
          onMessages: (msgs, status, data) => {
            conversationId = data.conversation_id || conversationId;
            provider = data.provider || provider;
            _renderMessages(msgs);
            if (status !== 'running') {
              // done or other status — end the stream
              _endStream();
            }
          },
          onDone: (data) => {
            _renderMessages(data.messages);
            _endStream();
          },
          onError: (data) => {
            _showToast(data.message || 'An error occurred', true);
            _endStream();
          },
          onStatusChange: () => {},
        },
        abortController.signal,
        null,
        provider
      );
    } catch (err) {
      if (err.name !== 'AbortError') {
        _showToast(err.message || 'Connection error', true);
      }
      _endStream();
    }
  }

  // ── Resume mid-stream ───────────────────────────────────────────────
  async function resumeStream() {
    if (isStreaming) return;
    if (!conversationId) return;
    
    _setStreaming(true);
    _startStatusTimer();
    statusText.textContent = 'Reconnecting...';
    
    abortController = new AbortController();
    
    try {
      await API.resumeStream(
        conversationId,
        {
          onMessages: (msgs, status, data) => {
            conversationId = data.conversation_id || conversationId;
            provider = data.provider || provider;
            _renderMessages(msgs);
            if (status !== 'running') {
              _endStream();
            }
          },
          onDone: (data) => {
            _renderMessages(data.messages);
            _endStream();
          },
          onError: (data) => {
            _showToast(data.message || 'An error occurred', true);
            _endStream();
          },
          onStatusChange: () => {},
        },
        abortController.signal
      );
    } catch (err) {
      if (err.name !== 'AbortError') {
        _showToast(err.message || 'Connection error', true);
      }
      _endStream();
    }
  }

  // ── Stop generation ─────────────────────────────────────────────────
  function stopGeneration() {
    if (abortController) {
      abortController.abort();
      abortController = null;
    }
    _endStream();
  }

  function _endStream() {
    _setStreaming(false);
    _stopStatusTimer();
    abortController = null;
  }

  // ── Retry last message ──────────────────────────────────────────────
  function retryLast() {
    // TBD — could re-send the last user message
    _showToast('Retry not implemented yet');
  }

  // ── Load conversation from history ──────────────────────────────────
  async function loadConversation(cid) {
    if (isStreaming) {
      _showToast('Stop current generation first');
      return;
    }
    
    try {
      const conv = await API.getConversation(cid);
      conversationId = cid;
      provider = conv.provider_id || null;
      
      const msgs = conv.frontend_messages || conv.messages || [];
      _renderMessages(msgs);
      
      if (provider) {
        headerProvider.textContent = provider;
      }
      
      welcomeScreen.classList.add('hidden');
      
      return conv;
    } catch (err) {
      _showToast('Failed to load conversation', true);
      throw err;
    }
  }

  // ── New chat ────────────────────────────────────────────────────────
  function newChat() {
    if (isStreaming) {
      _showToast('Stop current generation first');
      return;
    }
    conversationId = null;
    messagesContainer.innerHTML = '';
    welcomeScreen.classList.remove('hidden');
    continueBar.classList.add('hidden');
    headerProvider.textContent = '';
    chatInput.focus();
  }

  // ── Auto-resize input ───────────────────────────────────────────────
  function _autoResizeInput() {
    chatInput.style.height = 'auto';
    const newHeight = Math.min(chatInput.scrollHeight, 120);
    chatInput.style.height = newHeight + 'px';
  }

  // ── Init event listeners ────────────────────────────────────────────
  function init() {
    // Send button
    sendBtn.addEventListener('click', () => {
      const msg = chatInput.value;
      if (msg.trim() && !isStreaming) {
        sendMessage(msg, conversationId, provider);
      }
    });

    // Stop button
    stopBtn.addEventListener('click', stopGeneration);

    // Continue button
    continueBtn.addEventListener('click', continueGeneration);

    // Enter to send (Shift+Enter for newline)
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const msg = chatInput.value;
        if (msg.trim() && !isStreaming) {
          sendMessage(msg, conversationId, provider);
        }
      }
    });

    // Auto-resize textarea
    chatInput.addEventListener('input', _autoResizeInput);

    // Prevent zoom on iOS
    chatInput.addEventListener('gesturestart', (e) => e.preventDefault());
  }

  // ── Public API ──────────────────────────────────────────────────────
  return {
    init,
    sendMessage,
    stopGeneration,
    continueGeneration,
    resumeStream,
    retryLast,
    loadConversation,
    newChat,
    isStreaming: () => isStreaming,
    getConversationId: () => conversationId,
    getProvider: () => provider,
    setProvider: (p) => { provider = p; },
  };
})();
