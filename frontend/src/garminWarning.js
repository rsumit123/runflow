import React, { useEffect, useState } from 'react';

// A tiny store: api.js publishes, the banner subscribes. No dependency.
let current = null;
const listeners = new Set();

export function publishGarminWarning(message) {
  current = message;
  listeners.forEach((fn) => fn(current));
}

export function GarminWarningBanner() {
  const [message, setMessage] = useState(current);
  useEffect(() => {
    listeners.add(setMessage);
    setMessage(current);
    return () => { listeners.delete(setMessage); };
  }, []);

  if (!message) return null;
  const dismiss = () => publishGarminWarning(null);
  return (
    <div role="alert" style={{
      position: 'fixed', left: 12, right: 12, bottom: 12, zIndex: 10000,
      maxWidth: 520, margin: '0 auto', display: 'flex', gap: 12, alignItems: 'flex-start',
      backgroundColor: '#2b2110', border: '1px solid #f5a623', borderRadius: 8,
      color: '#f5a623', padding: '12px 14px', fontSize: 14, lineHeight: 1.5,
    }}>
      <div style={{ flex: 1 }}>{message}</div>
      <button onClick={dismiss} aria-label="Dismiss" style={{
        background: 'none', border: 'none', color: '#f5a623', cursor: 'pointer',
        fontSize: 20, lineHeight: 1, minWidth: 44, minHeight: 44, margin: '-10px -8px -10px 0',
      }}>&times;</button>
    </div>
  );
}
