import React from 'react';
import { color, font, space, radius } from '../theme';

const fmt = (sec) => (sec == null ? '—'
  : `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}`);

const LEVEL = {
  ideal: { label: 'Neutral conditions', tone: color.textSecondary },
  mild: { label: 'Mild heat stress', tone: color.textSecondary },
  moderate: { label: 'Moderate heat stress', tone: color.warn },
  hard: { label: 'Hard heat stress', tone: color.warn },
  severe: { label: 'Severe heat stress', tone: color.bad },
  extreme: { label: 'Extreme heat stress', tone: color.bad },
};

function levelFor(index) {
  if (index == null) return null;
  if (index <= 100) return 'ideal';
  if (index <= 120) return 'mild';
  if (index <= 140) return 'moderate';
  if (index <= 155) return 'hard';
  if (index <= 170) return 'severe';
  return 'extreme';
}

/**
 * What the weather cost this run.
 *
 * Raw pace conflates fitness with weather. Every trend in every running app is
 * quietly distorted by that, and the runner is left to wonder why they've "got
 * slower" every summer. Showing the cool-day equivalent is the fix.
 */
export default function Conditions({ activity }) {
  if (!activity) return null;
  const {
    temp_c: temp, dew_point_c: dew, heat_index: index,
    heat_penalty_sec: penalty, normalized_pace_sec: norm, average_speed: speed,
  } = activity;

  if (temp == null || dew == null) return null;

  const raw = speed ? 1000 / speed : null;
  const key = levelFor(index);
  const meta = LEVEL[key] || LEVEL.ideal;
  const costly = penalty >= 5 && norm && raw;

  return (
    <div style={{
      backgroundColor: color.surface, borderRadius: radius.lg, padding: space(5),
      marginBottom: space(6),
    }}
    >
      <div style={{ ...font.label, color: color.textMuted }}>Conditions</div>

      <div style={{
        display: 'flex', gap: space(5), flexWrap: 'wrap', alignItems: 'baseline',
        marginTop: space(3),
      }}
      >
        <span style={{ ...font.h2, ...font.numeric, color: color.text }}>
          {Math.round(temp)}°C
        </span>
        <span style={{ ...font.small, color: color.textSecondary }}>
          dew point {Math.round(dew)}°C
        </span>
        <span style={{ ...font.small, color: meta.tone, fontWeight: 600 }}>
          {meta.label}
        </span>
      </div>

      {costly ? (
        <div style={{ marginTop: space(5) }}>
          <div style={{ display: 'flex', gap: space(6), flexWrap: 'wrap' }}>
            <div>
              <div style={{ ...font.label, color: color.textMuted }}>You ran</div>
              <div style={{ ...font.h2, ...font.numeric, color: color.textSecondary, marginTop: space(1) }}>
                {fmt(raw)}<span style={{ ...font.small }}> /km</span>
              </div>
            </div>
            <div>
              <div style={{ ...font.label, color: color.good }}>Cool-day equivalent</div>
              <div style={{ ...font.h2, ...font.numeric, color: color.good, marginTop: space(1) }}>
                {fmt(norm)}<span style={{ ...font.small }}> /km</span>
              </div>
            </div>
          </div>
          <p style={{ ...font.small, color: color.textSecondary, margin: `${space(4)} 0 0` }}>
            The air cost you about <strong style={{ color: color.text }}>{Math.round(penalty)} s/km</strong>.
            The same effort on a neutral day would have read {fmt(norm)}/km — so judge this
            run against that, not against the raw number.
          </p>
        </div>
      ) : (
        <p style={{ ...font.small, color: color.textSecondary, margin: `${space(3)} 0 0` }}>
          No meaningful weather penalty — this pace is a fair read of the effort.
        </p>
      )}
    </div>
  );
}
