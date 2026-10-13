import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import api from '../api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  // undefined while loading, null when signed out, an object when signed in.
  const [user, setUser] = useState(undefined);

  const refresh = useCallback(async () => {
    try {
      const res = await api.get('/auth/me');
      setUser(res.data);
      return res.data;
    } catch (e) {
      setUser(null);
      return null;
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const login = async (email, password) => {
    await api.post('/auth/login', { email, password });
    return refresh();
  };

  const signup = async (email, password, inviteCode) => {
    await api.post('/auth/signup', { email, password, invite_code: inviteCode });
    return refresh();
  };

  const logout = async () => {
    try { await api.post('/auth/logout'); } finally { setUser(null); }
  };

  return (
    <AuthContext.Provider value={{ user, refresh, login, signup, logout }}>
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
  const { user } = useAuth();
  const location = useLocation();
  if (user === undefined) {
    return <div style={{ padding: 32, color: '#93a1b1' }}>Loading…</div>;
  }
  if (user === null) {
    const next = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return children;
}
