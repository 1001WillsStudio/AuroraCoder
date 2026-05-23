/**
 * ThinkWithTool Mobile — API Communication Module
 *
 * Handles all HTTP/SSE communication with the gateway server.
 * All requests include the auth token when available.
 */

const API = (() => {
  const BASE = '/api';

  /** Build headers including auth token */
  function _headers(extra = {}) {
    const h = { ...extra };
    const auth = Auth.getAuthHeader();
    if (auth) h['Authorization'] = auth;
    return h;
  }

  /** Simple GET request */
  async function _get(path, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const url = qs ? `${BASE}${path}?${qs}` : `${BASE}${path}`;
    const resp = await fetch(url, { headers: _headers() });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  /** Simple POST request */
  async function _post(path, body = {}) {
    const resp = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: _headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    return data;
  }

  // ── Providers ──────────────────────────────────────────────────────────

  async function getProviders() {
    return _get('/providers');
  }

  // ── Conversations ─────────────────────────────────────────────────────

  async function listConversations() {
    return _get('/conversations');
  }

  async function getConversation(cid) {
    return _get(`/conversations/${cid}`);
  }

  async function cancelConversation(cid) {
    return _post(`/conversations/${cid}/cancel`);
  }

  async function getActiveStreams() {
    return _get('/conversations/active');
  }

  // ── Chat (SSE streaming) ──────────────────────────────────────────────

  /**
   * Stream chat via SSE.
   *
   * @param {string|null} message - User message (null for continue)
   * @param {string|null} conversationId
   * @param {object} callbacks - { onMessages, onDone, onError, onStatusChange }
   * @param {AbortSignal} signal
   * @param {array|null} existingMessages - for resume/continue
   * @param {string|null} provider
   */
  async function streamChat(message, conversationId, callbacks, signal, existingMessages = null, provider = null) {
    const { onMessages, onDone, onError, onStatusChange } = callbacks;

    const body = {
      message: message || null,
      conversation_id: conversationId || null,
      messages: existingMessages || null,
      provider: provider || null,
    };

    const auth = Auth.getAuthHeader();
    const headers = { 'Content-Type': 'application/json' };
    if (auth) headers['Authorization'] = auth;

    try {
      const resp = await fetch(`${BASE}/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
        signal,
      });

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';

        for (const part of parts) {
          if (!part.trim()) continue;
          const events = _parseSSE(part);
          for (const evt of events) {
            switch (evt.type) {
              case 'messages':
                onMessages?.(evt.data.messages, evt.data.status, evt.data);
                onStatusChange?.('streaming');
                break;
              case 'done':
                onDone?.(evt.data);
                onStatusChange?.('done');
                break;
              case 'error':
                onError?.(evt.data);
                onStatusChange?.('error');
                break;
              case 'subagent_event':
                // Forward subagent events
                break;
            }
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') throw err;
      onError?.({ message: err.message, type: err.name });
      throw err;
    }
  }

  /** Parse SSE formatted text into {type, data} objects */
  function _parseSSE(text) {
    const events = [];
    const lines = text.split('\n');
    let current = null;
    for (const line of lines) {
      if (line.startsWith('event:')) {
        current = { type: line.slice(6).trim(), data: null };
      } else if (line.startsWith('data:')) {
        try {
          const data = JSON.parse(line.slice(5).trim());
          if (current) {
            current.data = data;
            events.push(current);
            current = null;
          } else {
            events.push({ type: 'message', data });
          }
        } catch (e) { /* partial chunk – skip */ }
      }
    }
    return events;
  }

  /**
   * Resume an in-progress stream (for reconnection).
   */
  async function resumeStream(conversationId, callbacks, signal) {
    const { onMessages, onDone, onError, onStatusChange } = callbacks;
    const auth = Auth.getAuthHeader();
    const headers = {};
    if (auth) headers['Authorization'] = auth;

    try {
      const resp = await fetch(`${BASE}/conversations/${conversationId}/stream`, {
        headers,
        signal,
      });

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
          if (!part.trim()) continue;
          const events = _parseSSE(part);
          for (const evt of events) {
            switch (evt.type) {
              case 'messages':
                onMessages?.(evt.data.messages, evt.data.status, evt.data);
                onStatusChange?.('streaming');
                break;
              case 'done':
                onDone?.(evt.data);
                onStatusChange?.('done');
                break;
              case 'error':
                onError?.(evt.data);
                onStatusChange?.('error');
                break;
            }
          }
        }
      }
    } catch (err) {
      if (err.name === 'AbortError') throw err;
      onError?.({ message: err.message, type: err.name });
      throw err;
    }
  }

  // ── Settings ───────────────────────────────────────────────────────────

  async function getSettings() {
    return _get('/settings');
  }

  // ── Auth Check ─────────────────────────────────────────────────────────

  async function checkAuth() {
    try {
      const resp = await fetch(`${BASE}/auth/check`, { headers: _headers() });
      return resp.ok;
    } catch { return false; }
  }

  return {
    getProviders,
    listConversations,
    getConversation,
    cancelConversation,
    getActiveStreams,
    streamChat,
    resumeStream,
    getSettings,
    checkAuth,
  };
})();
