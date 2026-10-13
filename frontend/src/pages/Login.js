import React, { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { safeNext, useAuth } from '../auth/AuthContext';
import { authStyles as s } from './authStyles';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    const pw = password;
    setPassword('');   // the password does not outlive the submit
    try {
      await login(email, pw);
      navigate(safeNext(params.get('next')), { replace: true });
    } catch (err) {
      setError((err.response && err.response.data && err.response.data.detail)
        || 'Could not sign in. Try again.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={s.page}>
      <form onSubmit={submit} style={s.card}>
        <h1 style={s.title}>RunFlow</h1>
        <p style={s.sub}>Sign in to your training log.</p>
        <label style={s.label}>Email
          <input style={s.input} type="email" autoComplete="email" value={email}
                 onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label style={s.label}>Password
          <input style={s.input} type="password" autoComplete="current-password"
                 value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div style={s.error}>{error}</div>}
        <button style={s.button} type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p style={s.foot}>Have an invite? <Link to="/signup" style={s.link}>Create an account</Link></p>
      </form>
    </div>
  );
}
