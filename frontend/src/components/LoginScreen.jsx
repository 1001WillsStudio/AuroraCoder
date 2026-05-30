import React, { useState, useRef } from 'react';

/**
 * Desktop login screen — shown when ACCESS_PASSWORD is set and user
 * hasn't authenticated yet. Matches the desktop app's visual style.
 */
export default function LoginScreen({ onLoginSuccess }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const inputRef = useRef(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!password.trim()) return;
    setError('');
    setLoading(true);

    try {
      const resp = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.detail || 'Login failed');
      }
      // Store token
      const TOKEN_KEY = 'thinkwithtool_token';
      const TOKEN_EXPIRY_KEY = 'thinkwithtool_token_expiry';
      try {
        localStorage.setItem(TOKEN_KEY, data.token);
        localStorage.setItem(
          TOKEN_EXPIRY_KEY,
          String(Date.now() + (data.expires_in_ms || 7 * 24 * 60 * 60 * 1000))
        );
      } catch (e) { /* ignore */ }
      onLoginSuccess?.();
    } catch (err) {
      setError(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-overlay">
      <div className="login-card">
        <div className="login-card-icon">🧠</div>
        <h1 className="login-card-title">🌌 AuroraCoder</h1>
        <p className="login-card-subtitle">Secure access required</p>

        <form onSubmit={handleSubmit} className="login-card-form">
          <div className="login-field">
            <label htmlFor="desktop-password" className="login-field-label">
              Access Password
            </label>
            <div className="login-input-wrap">
              <input
                ref={inputRef}
                id="desktop-password"
                type={showPassword ? 'text' : 'password'}
                className="login-password-field"
                placeholder="Enter your access password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
                autoComplete="current-password"
                disabled={loading}
              />
              <button
                type="button"
                className="login-toggle-btn"
                onClick={() => setShowPassword(!showPassword)}
                tabIndex={-1}
                title={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? '🙈' : '👁️'}
              </button>
            </div>
          </div>

          {error && <div className="login-card-error">{error}</div>}

          <button
            type="submit"
            className="login-card-btn"
            disabled={loading || !password.trim()}
          >
            {loading ? 'Unlocking…' : 'Unlock'}
          </button>
        </form>

        <p className="login-card-footer">
          Set <code>ACCESS_PASSWORD</code> in your environment to configure.
        </p>
      </div>

      <style>{`
        .login-overlay {
          position: fixed;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--bg-primary, #0d1117);
          z-index: 10000;
          padding: 20px;
        }
        .login-card {
          width: 100%;
          max-width: 400px;
          background: var(--bg-secondary, #161b22);
          border: 1px solid var(--border-color, #30363d);
          border-radius: 16px;
          padding: 40px 32px;
          text-align: center;
          box-shadow: 0 8px 30px rgba(0,0,0,0.4);
        }
        .login-card-icon {
          font-size: 48px;
          margin-bottom: 12px;
        }
        .login-card-title {
          font-size: 24px;
          font-weight: 700;
          color: var(--text-primary, #e6edf3);
          margin: 0 0 4px;
          letter-spacing: -0.3px;
        }
        .login-card-subtitle {
          font-size: 14px;
          color: var(--text-muted, #8b949e);
          margin: 0 0 28px;
        }
        .login-card-form {
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .login-field {
          text-align: left;
        }
        .login-field-label {
          display: block;
          font-size: 12px;
          font-weight: 600;
          color: var(--text-secondary, #8b949e);
          margin-bottom: 6px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        .login-input-wrap {
          position: relative;
          display: flex;
        }
        .login-password-field {
          flex: 1;
          padding: 12px 44px 12px 14px;
          font-size: 15px;
          font-family: 'SF Mono', 'Fira Code', monospace;
          background: var(--bg-input, #1a1f29);
          border: 1.5px solid var(--border-color, #30363d);
          border-radius: 10px;
          color: var(--text-primary, #e6edf3);
          outline: none;
          transition: border-color 0.2s, box-shadow 0.2s;
        }
        .login-password-field:focus {
          border-color: var(--accent, #58a6ff);
          box-shadow: 0 0 0 3px rgba(88,166,255,0.15);
        }
        .login-password-field::placeholder {
          color: var(--text-muted, #6e7681);
        }
        .login-toggle-btn {
          position: absolute;
          right: 4px;
          top: 50%;
          transform: translateY(-50%);
          width: 36px;
          height: 36px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: none;
          border: none;
          font-size: 18px;
          cursor: pointer;
          border-radius: 6px;
          color: var(--text-secondary, #8b949e);
          transition: background 0.15s;
        }
        .login-toggle-btn:hover {
          background: var(--bg-hover, #292e36);
        }
        .login-card-error {
          font-size: 13px;
          color: #f85149;
          text-align: left;
          padding: 8px 12px;
          background: rgba(248,81,73,0.1);
          border-radius: 8px;
          border: 1px solid rgba(248,81,73,0.2);
        }
        .login-card-btn {
          width: 100%;
          padding: 12px;
          font-size: 15px;
          font-weight: 600;
          color: #fff;
          background: var(--accent, #58a6ff);
          border: none;
          border-radius: 10px;
          cursor: pointer;
          transition: background 0.15s, opacity 0.15s;
        }
        .login-card-btn:hover:not(:disabled) {
          background: var(--accent-hover, #79c0ff);
        }
        .login-card-btn:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .login-card-footer {
          font-size: 12px;
          color: var(--text-muted, #6e7681);
          margin: 20px 0 0;
        }
        .login-card-footer code {
          font-family: 'SF Mono', 'Fira Code', monospace;
          font-size: 11px;
          background: var(--bg-tertiary, #21262d);
          padding: 1px 6px;
          border-radius: 3px;
        }
      `}</style>
    </div>
  );
}
