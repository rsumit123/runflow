import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import api from '../api';
import { resetGarminWarning } from '../garminWarning';
import { onSessionEnded, rearmSessionEnded } from './sessionEvents';

const AuthContext = createContext(null);

export const COOKIE_HELP =
  "Signed in, but the session cookie wasn't saved. Check that cookies are allowed for this site.";

export function AuthProvider({ children }) {
  // undefined while loading, null when signed out, an object when signed in.
  const [user, setUser] = useState(undefined);
  // True when the last /auth/me failed for a reason other than "signed out".
  const [authError, setAuthError] = useState(false);
  const navigate = useNavigate();

  // Resolves to the user, or null when signed out (401). On any other failure
  // (500, network, deploy) the previous state is kept and undefined is returned.
  const refresh = useCallback(async () => {
    try {
      const res = await api.get('/auth/me');
      rearmSessionEnded();
      setUser(res.data);
      setAuthError(false);
      return res.data;
    } catch (e) {
      if (e.response && e.response.status === 401) {
        setUser(null);
        setAuthError(false);
        return null;
      }
      setAuthError(true);
      return undefined;
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // api.js reports a 401 on a data call: the session has ended.
  useEffect(() => onSessionEnded(() => setUser(null)), []);

  const afterAuth = async () => {
    const me = await refresh();
    if (me === null) throw new Error(COOKIE_HELP);
    if (me === undefined) {
      throw new Error("Signed in, but RunFlow couldn't load your account. Try again.");
    }
    return me;
  };

  const login = async (email, password) => {
    await api.post('/auth/login', { email, password });
    return afterAuth();
  };

  const signup = async (email, password, inviteCode) => {
    await api.post('/auth/signup', { email, password, invite_code: inviteCode });
    return afterAuth();
  };

  // Throws if the server could not end the session, and then the user stays
  // signed in: their cookie is still live. A 401 means it is already gone.
  const logout = async () => {
    try {
      await api.post('/auth/logout');
    } catch (e) {
      if (!(e.response && e.response.status === 401)) throw e;
    }
    resetGarminWarning();
    navigate('/login', { replace: true });   // plain /login: no `next` from this user
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, authError, refresh, login, signup, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

// Only same-site paths: never let ?next= send someone to another host.
// Backslashes (and control characters) are refused too: browsers treat
// "/\evil.com" as "//evil.com".
export function safeNext(raw) {
  if (!raw || typeof raw !== 'string') return '/';
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/';
  // eslint-disable-next-line no-control-regex
  if (/[\\\u0000-\u001f\u007f]/.test(raw)) return '/';
  return raw;
}

export function RequireAuth({ children }) {
  const { user, authError, refresh } = useAuth();
  const location = useLocation();
  if (user === undefined) {
    if (authError) {
      return (
        <div role="alert" style={{ padding: 32, color: '#93a1b1' }}>
          <p style={{ marginBottom: 12 }}>Can't reach RunFlow right now.</p>
          <button onClick={refresh} style={{
            minHeight: 44, padding: '0 18px', borderRadius: 6, border: '1px solid #1e2936',
            background: 'transparent', color: '#e8edf2', cursor: 'pointer', fontSize: 14,
          }}>Try again</button>
        </div>
      );
    }
    return <div style={{ padding: 32, color: '#93a1b1' }}>Loading…</div>;
  }
  if (user === null) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return children;
}
