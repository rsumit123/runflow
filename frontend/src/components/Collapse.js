import React, { useState } from 'react';
import { color, font, space, radius } from '../theme';

/**
 * A disclosure row: one line of summary, the detail on demand.
 *
 * Insight panels earn their place by being available, not by being loud. Left
 * expanded by default they push the thing you actually came for below the fold.
 */
export default function Collapse({ label, summary, tone, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section style={{
      backgroundColor: color.surface, borderRadius: radius.lg, marginBottom: space(3),
    }}
    >
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{
          display: 'flex', alignItems: 'center', gap: space(3), width: '100%',
          background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left',
          padding: space(5), minHeight: '44px',
        }}
      >
        <span style={{
          color: color.textMuted, fontSize: '11px', flexShrink: 0,
          transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 120ms ease',
        }}
        >
          ▶
        </span>
        <span style={{ minWidth: 0, flex: 1 }}>
          <span style={{ ...font.label, color: color.textMuted, display: 'block' }}>{label}</span>
          <span style={{
            ...font.small, color: tone || color.text, display: 'block', marginTop: '2px',
            fontWeight: 500,
          }}
          >
            {summary}
          </span>
        </span>
      </button>
      {open && (
        <div style={{ padding: `0 ${space(5)} ${space(5)}` }}>{children}</div>
      )}
    </section>
  );
}
