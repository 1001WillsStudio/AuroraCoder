/**
 * ThinkWithTool Mobile — Authentication Module
 * 
 * Handles token storage, login/logout, and auth headers.
 * The gateway checks ACCESS_PASSWORD env var; if not set, auth is skipped.
 */

const Auth = (() => {
  const TOKEN_KEY = 'auroracoder_token';
  const TOKEN_EXPIRY_KEY = 'auroracoder_token_expiry';

  let _token = null;

  /** Get stored token, checking expiry */
  function getToken() {
    if (_token) return _token;
    try {
      const token = localStorage.getItem(TOKEN_KEY);
      const expiry = localStorage.getItem(TOKEN_EXPIRY_KEY);
      if (token && expiry) {
        const expiryTime = parseInt(expiry, 10);
        if (Date.now() < expiryTime) {
          _token = token;
          return token;
        }
        // Expired — clear
        clearToken();
      }
    } catch (e) { /* localStorage unavailable */ }
    return null;
  }

  /** Store token with expiry (default 7 days) */
  function setToken(token, expiresInMs = 7 * 24 * 60 * 60 * 1000) {
    _token = token;
    try {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(TOKEN_EXPIRY_KEY, String(Date.now() + expiresInMs));
    } catch (e) { /* localStorage unavailable */ }
  }

  /** Remove stored token */
  function clearToken() {
    _token = null;
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(TOKEN_EXPIRY_KEY);
    } catch (e) { /* ignore */ }
  }

  /** Check if the user has a valid token */
  function isAuthenticated() {
    return !!getToken();
  }

  /** Get authorization header value, or null if no token */
  function getAuthHeader() {
    const token = getToken();
    return token ? `Bearer ${token}` : null;
  }

  /** Attempt login with password */
  async function login(password) {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.detail || 'Login failed');
    }
    setToken(data.token, data.expires_in_ms || 7 * 24 * 60 * 60 * 1000);
    return data;
  }

  /** Check with server if current token is valid */
  async function checkAuth() {
    const token = getToken();
    if (!token) return false;
    try {
      const resp = await fetch('/api/auth/check', {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      return resp.ok;
    } catch (e) {
      return false;
    }
  }

  /** Logout — clear local token */
  function logout() {
    clearToken();
  }

  return {
    getToken,
    setToken,
    clearToken,
    isAuthenticated,
    getAuthHeader,
    login,
    checkAuth,
    logout,
  };
})();
