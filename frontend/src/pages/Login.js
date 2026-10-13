import React, { useRef, useState } from 'react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { safeNext, useAuth } from '../auth/AuthContext';
import { errorText } from '../errors';
import { authStyles as s } from './authStyles';

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submitted = useRef(false);   // the submit handler navigates itself

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    submitted.current = true;
    const pw = password;
    setPassword('');   // the password does not outlive the submit
    try {
      await login(email, pw);
      navigate(safeNext(params.get('next')), { replace: true });
    } catch (err) {
      setError(err.isAxiosError ? errorText(err, 'Could not sign in. Try again.') : err.message);
    } finally {
      submitted.current = false;
      setBusy(false);
    }
  };

  // Already signed in: nothing to do here.
  if (user && !submitted.current) return <Navigate to={safeNext(params.get('next'))} replace />;

  return (
    <div style={s.page}>
      <form onSubmit={submit} style={s.card}>
        <h1 style={s.title}>RunFlow</h1>
        <p style={s.sub}>Sign in to your training log.</p>
        <label style={s.label}>Email
          <input style={s.input} type="email" autoComplete="username" value={email}
                 onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label style={s.label}>Password
          <input style={s.input} type="password" autoComplete="current-password"
                 value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div role="alert" style={s.error}>{error}</div>}
        <button style={s.button} type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
        <p style={s.foot}>Have an invite? <Link to="/signup" style={s.link}>Create an account</Link></p>
      </form>
    </div>
  );
}
