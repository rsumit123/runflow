import React, { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { authStyles as s } from './authStyles';

export default function Signup() {
  const { signup } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [invite, setInvite] = useState(params.get('invite') || '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError('');
    const pw = password;
    setPassword('');   // the password does not outlive the submit
    try {
      await signup(email, pw, invite.trim());
      navigate('/settings', { replace: true });
    } catch (err) {
      setError((err.response && err.response.data && err.response.data.detail)
        || 'Could not create the account.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={s.page}>
      <form onSubmit={submit} style={s.card}>
        <h1 style={s.title}>Join RunFlow</h1>
        <p style={s.sub}>RunFlow is invite-only for now.</p>
        <label style={s.label}>Invite code
          <input style={s.input} value={invite} onChange={(e) => setInvite(e.target.value)} required />
        </label>
        <label style={s.label}>Email
          <input style={s.input} type="email" autoComplete="email" value={email}
                 onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label style={s.label}>Password (at least 10 characters)
          <input style={s.input} type="password" autoComplete="new-password" minLength={10}
                 value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div style={s.error}>{error}</div>}
        <button style={s.button} type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
        <p style={s.foot}>Already have one? <Link to="/login" style={s.link}>Sign in</Link></p>
      </form>
    </div>
  );
}
