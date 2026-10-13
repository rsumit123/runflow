import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import api, { API_URL } from '../api';
import { useAuth } from '../auth/AuthContext';
import { errorText } from '../errors';
import { authStyles as a } from './authStyles';

const card = {
  backgroundColor: '#111820', borderRadius: 14, padding: 20, marginBottom: 16,
};
const h2 = { fontSize: 18, fontWeight: 600, color: '#e8edf2', margin: '0 0 8px' };
const note = { fontSize: 13, color: '#93a1b1', lineHeight: 1.6, margin: '0 0 12px' };
const row = { display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' };
const primary = {
  minHeight: 44, backgroundColor: '#ff5a1f', color: '#0b0f14', border: 'none',
  borderRadius: 6, padding: '0 18px', fontSize: 14, fontWeight: 700, cursor: 'pointer',
};
const quiet = {
  minHeight: 44, backgroundColor: 'transparent', color: '#e8edf2', border: '1px solid #1e2936',
  borderRadius: 6, padding: '0 18px', fontSize: 14, fontWeight: 600, cursor: 'pointer',
};
const input = { ...a.input, marginTop: 6 };
const secretBox = { ...a.input, marginTop: 0, flex: 1, minWidth: 0, fontFamily: 'monospace', fontSize: 13 };
const ok = { color: '#3ddc84', fontSize: 14, margin: '8px 0 0' };
const bad = { color: '#ff4d4f', fontSize: 14, margin: '8px 0 0' };

const STRAVA_REASONS = {
  capacity: "This RunFlow app has reached Strava's athlete limit. Ask the owner.",
  denied: 'Strava access was not granted.',
  already_linked: 'That Strava account is already linked to another RunFlow account.',
  signin: 'Your session expired while connecting. Sign in again, then reconnect Strava.',
  state: 'That connection link was not started from this browser. Start again from this page.',
  exchange_failed: 'Connecting Strava failed. Try again.',
};

const detail = errorText;

const statusText = (s) =>
  s === 'ok' ? 'Connected' : s === 'needs_reconnect' ? 'Needs reconnecting' : 'Not connected';
const statusColor = (s) =>
  s === 'ok' ? '#3ddc84' : s === 'needs_reconnect' ? '#f5a623' : '#93a1b1';

function Status({ value }) {
  return <span style={{ color: statusColor(value), fontSize: 14, fontWeight: 600 }}>{statusText(value)}</span>;
}

function CopyRow({ value, label }) {
  const [copied, setCopied] = useState('');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied('Copied.');
    } catch (e) {
      setCopied('Could not copy. Select the text and copy it by hand.');
    }
  };
  return (
    <div>
      <div style={row}>
        <input readOnly aria-label={label} value={value} style={secretBox} onFocus={(e) => e.target.select()} />
        <button type="button" style={quiet} onClick={copy}>Copy</button>
      </div>
      {copied && <p role="status" style={note}>{copied}</p>}
    </div>
  );
}

function StravaSection({ user, refresh, banner, clearBanner }) {
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  const disconnect = async () => {
    setBusy(true); setMsg(null); clearBanner();
    try {
      await api.post('/strava/disconnect');
      await refresh();
      setMsg({ ok: true, text: 'Strava disconnected.' });
    } catch (err) {
      setMsg({ ok: false, text: detail(err, 'Could not disconnect Strava.') });
    } finally {
      setBusy(false);
    }
  };

  // A top-level navigation, so the session cookie travels and the API can
  // redirect on to Strava. XHR could not follow that redirect.
  const connect = () => {
    clearBanner();
    window.location.assign(`${API_URL}/strava/connect`);
  };

  const shown = msg || banner;
  return (
    <section style={card}>
      <h2 style={h2}>Strava</h2>
      <p style={{ margin: '0 0 12px' }}><Status value={user.strava} /></p>
      {shown && <p role={shown.ok ? 'status' : 'alert'} style={shown.ok ? ok : bad}>{shown.text}</p>}
      <div style={{ ...row, marginTop: 12 }}>
        <button style={primary} onClick={connect}>
          {user.strava === 'needs_reconnect' ? 'Reconnect Strava' : user.strava ? 'Connect again' : 'Connect Strava'}
        </button>
        {user.strava && (
          <button style={quiet} onClick={disconnect} disabled={busy}>Disconnect</button>
        )}
      </div>
    </section>
  );
}

function GarminSection({ user, refresh }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [needsMfa, setNeedsMfa] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const connect = async (e) => {
    e.preventDefault();
    setBusy(true); setMsg(null);
    const pw = password;
    setPassword('');   // never keep the password in state past the request
    try {
      const res = await api.post('/garmin/connect', { email, password: pw });
      if (res.data.status === 'needs_mfa') {
        setNeedsMfa(true);
        setMsg({ ok: true, text: 'Garmin sent you a code. Enter it below.' });
      } else {
        setEmail('');
        await refresh();
        setMsg({ ok: true, text: 'Garmin connected.' });
      }
    } catch (err) {
      setMsg({ ok: false, text: detail(err, 'Could not connect to Garmin.') });
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (e) => {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      await api.post('/garmin/mfa', { code: code.trim() });
      setNeedsMfa(false); setCode(''); setEmail('');
      await refresh();
      setMsg({ ok: true, text: 'Garmin connected.' });
    } catch (err) {
      setMsg({
        ok: false,
        text: err.response && err.response.status === 503
          ? 'Garmin is unreachable right now. Try again in a few minutes.'
          : detail(err, 'That code did not work.'),
      });
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true); setMsg(null);
    try {
      await api.post('/garmin/disconnect');
      await refresh();
      setMsg({ ok: true, text: 'Garmin disconnected.' });
    } catch (err) {
      setMsg({ ok: false, text: detail(err, 'Could not disconnect Garmin.') });
    } finally {
      setBusy(false);
    }
  };

  const connected = user.garmin === 'ok';
  return (
    <section style={card}>
      <h2 style={h2}>Garmin</h2>
      <p style={{ margin: '0 0 12px' }}><Status value={user.garmin} /></p>
      <p style={note}>
        Garmin has no official sign-in for apps like this one. RunFlow uses your password once to sign in to Garmin, keeps only the resulting access tokens (encrypted), and never stores your password. Garmin can change their side at any time and break this.
      </p>
      {needsMfa ? (
        <form onSubmit={submitCode}>
          <label style={a.label}>6-digit code
            <input style={input} inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                   value={code} onChange={(e) => setCode(e.target.value)} required />
          </label>
          <div style={row}>
            <button style={primary} type="submit" disabled={busy}>
              {busy ? 'Checking…' : 'Submit code'}
            </button>
            <button type="button" style={quiet} disabled={busy}
                    onClick={() => { setNeedsMfa(false); setCode(''); setMsg(null); }}>
              Start over
            </button>
          </div>
        </form>
      ) : connected ? (
        <button style={quiet} onClick={disconnect} disabled={busy}>Disconnect</button>
      ) : (
        <form onSubmit={connect}>
          <label style={a.label}>Garmin email
            <input style={input} type="email" autoComplete="off" value={email}
                   onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label style={a.label}>Garmin password
            <input style={input} type="password" autoComplete="off" value={password}
                   onChange={(e) => setPassword(e.target.value)} required />
          </label>
          <div style={row}>
            <button style={primary} type="submit" disabled={busy}>
              {busy ? 'Connecting…' : user.garmin ? 'Reconnect Garmin' : 'Connect Garmin'}
            </button>
            {user.garmin && (
              <button type="button" style={quiet} onClick={disconnect} disabled={busy}>Disconnect</button>
            )}
          </div>
        </form>
      )}
      {msg && <p role={msg.ok ? 'status' : 'alert'} style={msg.ok ? ok : bad}>{msg.text}</p>}
    </section>
  );
}

function ConnectorSection() {
  const [exists, setExists] = useState(null);
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    api.get('/connector')
      .then((res) => setExists(!!res.data.exists))
      .catch((err) => setMsg({ ok: false, text: detail(err, 'Could not load the connector status.') }));
  }, []);

  const rotate = async () => {
    if (exists && !window.confirm('Your current connector URL will stop working. Continue?')) return;
    setBusy(true); setMsg(null);
    try {
      const res = await api.post('/connector/rotate');
      setUrl(res.data.url);
      setExists(true);
    } catch (err) {
      setMsg({ ok: false, text: detail(err, 'Could not create the connector URL.') });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={card}>
      <h2 style={h2}>Claude connector</h2>
      <p style={note}>Lets Claude read your training data through RunFlow.</p>
      <button style={primary} onClick={rotate} disabled={busy || exists === null}>
        {exists ? 'Replace connector URL' : 'Create connector URL'}
      </button>
      {url && (
        <div style={{ marginTop: 12 }}>
          <CopyRow value={url} label="Connector URL" />
          <p style={note}>This URL is a password. Paste it into Claude → Settings → Connectors. It won't be shown again.</p>
        </div>
      )}
      {msg && <p role={msg.ok ? 'status' : 'alert'} style={msg.ok ? ok : bad}>{msg.text}</p>}
    </section>
  );
}

function InvitesSection() {
  const [uses, setUses] = useState(1);
  const [days, setDays] = useState(14);
  const [link, setLink] = useState('');
  const [invites, setInvites] = useState([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const load = useCallback(() => {
    api.get('/admin/invites')
      .then((res) => setInvites(res.data))
      .catch((err) => setMsg({ ok: false, text: detail(err, 'Could not load invites.') }));
  }, []);
  useEffect(() => { load(); }, [load]);

  const create = async (e) => {
    e.preventDefault();
    setBusy(true); setMsg(null);
    try {
      const res = await api.post('/admin/invites', {
        uses: Number(uses), expires_days: Number(days),
      });
      setLink(`${window.location.origin}/signup?invite=${encodeURIComponent(res.data.code)}`);
      load();
    } catch (err) {
      setMsg({ ok: false, text: detail(err, 'Could not create the invite.') });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={card}>
      <h2 style={h2}>Invites</h2>
      <form onSubmit={create}>
        <div style={row}>
          <label style={{ ...a.label, flex: '1 1 120px', marginBottom: 8 }}>Uses
            <input style={input} type="number" min={1} value={uses}
                   onChange={(e) => setUses(e.target.value)} required />
          </label>
          <label style={{ ...a.label, flex: '1 1 120px', marginBottom: 8 }}>Expires in (days)
            <input style={input} type="number" min={1} value={days}
                   onChange={(e) => setDays(e.target.value)} required />
          </label>
        </div>
        <button style={primary} type="submit" disabled={busy}>Create invite</button>
      </form>
      {link && (
        <div style={{ marginTop: 12 }}>
          <CopyRow value={link} label="Invite link" />
          <p style={note}>Shown once. Send this link to the person you are inviting.</p>
        </div>
      )}
      {msg && <p role={msg.ok ? 'status' : 'alert'} style={msg.ok ? ok : bad}>{msg.text}</p>}
      {invites.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '16px 0 0' }}>
          {invites.map((inv) => (
            <li key={inv.id} style={{ fontSize: 13, color: '#93a1b1', padding: '8px 0', borderTop: '1px solid #1e2936' }}>
              {inv.uses_left} use{inv.uses_left === 1 ? '' : 's'} left
              {' · '}
              {inv.expired
                ? <span style={{ color: '#ff4d4f' }}>expired</span>
                : inv.expires_at
                  ? `expires ${new Date(inv.expires_at).toLocaleDateString()}`
                  : 'no expiry'}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function Settings() {
  const { user, refresh, logout } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [banner, setBanner] = useState(null);

  // Back from Strava: show the outcome, re-read the account, tidy the URL.
  useEffect(() => {
    const result = params.get('strava');
    if (!result) return;
    if (result === 'connected') {
      setBanner({ ok: true, text: 'Strava connected.' });
    } else {
      setBanner({
        ok: false,
        text: STRAVA_REASONS[params.get('reason')] || 'Connecting Strava failed. Try again.',
      });
    }
    refresh();
    navigate('/settings', { replace: true });
  }, [params, refresh, navigate]);

  const [signOutError, setSignOutError] = useState('');
  const signOut = async () => {
    setSignOutError('');
    try {
      await logout();
    } catch (err) {
      setSignOutError(errorText(err, "Couldn't sign out. You are still signed in. Try again."));
    }
  };

  if (!user) return null;
  return (
    <div style={{ maxWidth: 560, margin: '0 auto' }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: '#fff', margin: '0 0 16px' }}>Settings</h1>
      <section style={card}>
        <h2 style={h2}>Account</h2>
        <p style={{ ...note, wordBreak: 'break-all' }}>{user.email}</p>
        <button style={quiet} onClick={signOut}>Sign out</button>
        {signOutError && <p role="alert" style={bad}>{signOutError}</p>}
      </section>
      <StravaSection user={user} refresh={refresh} banner={banner} clearBanner={() => setBanner(null)} />
      <GarminSection user={user} refresh={refresh} />
      <ConnectorSection />
      {user.is_admin && <InvitesSection />}
    </div>
  );
}
