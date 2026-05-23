/**
 * ThinkWithTool Desktop — Authentication Module
 *
 * Handles token storage, login/logout, and auth headers.
 * Mirrors the mobile auth.js with ES module exports.
 */

const TOKEN_KEY = 'thinkwithtool_token';
const TOKEN_EXPIRY_KEY = 'thinkwithtool_token_expiry';

let _token = null;

/** Get stored token, checking expiry */
export function getToken() {
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
      clearToken();
    }
  } catch (e) { /* localStorage unavailable */ }
  return null;
}

/** Store token with expiry (default 7 days) */
export function setToken(token, expiresInMs = 7 * 24 * 60 * 60 * 1000) {
  _token = token;
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(TOKEN_EXPIRY_KEY, String(Date.now() + expiresInMs));
  } catch (e) { /* ignore */ }
}

/** Remove stored token */
export function clearToken() {
  _token = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_EXPIRY_KEY);
  } catch (e) { /* ignore */ }
}

/** Check if the user has a valid token */
export function isAuthenticated() {
  return !!getToken();
}

/** Get authorization header value, or null if no token */
export function getAuthHeader() {
  const token = getToken();
  return token ? `Bearer ${token}` : null;
}

/** Attempt login with password */
export async function login(password) {
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
export async function checkAuth() {
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

/** Check if the server requires authentication (no token sent) */
export async function isAuthRequired() {
  try {
    const resp = await fetch('/api/providers');
    if (resp.status === 401) return true;
    if (resp.ok) return false;
    // Other errors — assume not auth-related
    return false;
  } catch (e) {
    return false;
  }
}

/** Logout — clear local token */
export function logout() {
  clearToken();
}
