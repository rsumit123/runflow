import React from 'react';
import { color, font, space, radius, button } from '../theme';

const fmtPace = (sec) => `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}`;

const RING_TONE = {
  high: color.good, moderate: color.good, low: color.warn, very_low: color.bad,
};

/** Readiness as a ring. A number you can read without reading. */
function ReadinessRing({ score, level, size = 56 }) {
  const stroke = 4;
  const r = (size - stroke) / 2;
  const circ = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score || 0)) / 100;
  const tone = RING_TONE[level] || color.neutral;
  return (
    <svg width={size} height={size} style={{ flexShrink: 0 }} aria-label={`Readiness ${score}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none"
              stroke={color.hairline} strokeWidth={stroke} />
      <circle
        cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke={tone} strokeWidth={stroke} strokeLinecap="round"
        strokeDasharray={`${circ * pct} ${circ}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text
        x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
        fill={color.text} fontSize="15" fontWeight="600"
        style={font.numeric}
      >
        {score != null ? score : '–'}
      </text>
    </svg>
  );
}

/**
 * The one card that answers "what am I doing today, and how fast".
 *
 * Everything else on the page is a drill-down. This is the page.
 */
export default function TodayHero({ workout, guidance, onOpen, onAccept, applying }) {
  const r = (guidance && guidance.readiness) || {};
  const rec = (guidance && guidance.recommendation) || {};
  const heat = guidance && guidance.heat;

  if (!workout || workout.day_type === 'rest') {
    return (
      <section style={{
        backgroundColor: color.surface, borderRadius: radius.lg, padding: space(7),
        marginBottom: space(6),
      }}
      >
        <div style={{ ...font.label, color: color.textMuted }}>Today</div>
        <h1 style={{ ...font.h1, color: color.text, margin: `${space(3)} 0 0` }}>Rest day</h1>
        <p style={{ ...font.small, color: color.textSecondary, margin: `${space(2)} 0 0` }}>
          Rest is when the training you already did turns into fitness.
        </p>
      </section>
    );
  }

  // Heat widens the band — show the number you should actually run.
  const adjusted = heat && heat.adjusted;
  const low = adjusted ? heat.pace_low_sec : workout.pace_low_sec;
  const high = adjusted ? heat.pace_high_sec : workout.pace_high_sec;
  const km = workout.target_distance_m ? (workout.target_distance_m / 1000).toFixed(1) : null;
  const canAct = guidance && guidance.can_apply;

  return (
    <section style={{
      backgroundColor: color.surface, borderRadius: radius.lg, padding: space(7),
      marginBottom: space(6),
      borderLeft: `3px solid ${canAct ? color.warn : color.accent}`,
    }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: space(4) }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ ...font.label, color: color.textMuted }}>
            Today · Week {workout.week_number}
          </div>
          <h1
            onClick={onOpen}
            style={{ ...font.h1, color: color.text, margin: `${space(3)} 0 0`, cursor: onOpen ? 'pointer' : 'default' }}
          >
            {workout.title}
          </h1>
          {km && (
            <div style={{ ...font.body, ...font.numeric, color: color.textSecondary, marginTop: space(1) }}>
              {km} km
            </div>
          )}
        </div>

        {r.available && (
          <div style={{ textAlign: 'center' }}>
            <ReadinessRing score={r.score} level={r.level} />
            <div style={{ ...font.label, color: color.textMuted, marginTop: space(1), fontSize: '10px' }}>
              Ready
            </div>
          </div>
        )}
      </div>

      {/* The number you came for. */}
      {low && high && (
        <div style={{ marginTop: space(6) }}>
          <div style={{ ...font.hero, ...font.numeric, color: color.text }}>
            {fmtPace(low)}<span style={{ color: color.textMuted }}> – </span>{fmtPace(high)}
            <span style={{ ...font.h2, color: color.textMuted, fontWeight: 400 }}> /km</span>
          </div>

          <div style={{ display: 'flex', gap: space(2), flexWrap: 'wrap', marginTop: space(3) }}>
            {adjusted && (
              <span style={{
                ...font.small, color: color.warn, backgroundColor: `${color.warn}18`,
                padding: `${space(1)} ${space(2)}`, borderRadius: radius.sm, fontWeight: 600,
              }}
              >
                +{heat.penalty_sec}s for heat · {Math.round(heat.temp_c)}°C
              </span>
            )}
            {workout.hr_ceiling && (
              <span style={{
                ...font.small, color: color.textSecondary, backgroundColor: color.surfaceRaised,
                padding: `${space(1)} ${space(2)}`, borderRadius: radius.sm,
              }}
              >
                HR ≤ {workout.hr_ceiling} bpm
              </span>
            )}
          </div>

          {adjusted && (
            <p style={{ ...font.small, color: color.textMuted, margin: `${space(3)} 0 0` }}>
              Same effort as {fmtPace(workout.pace_low_sec)}–{fmtPace(workout.pace_high_sec)} on a cool day —
              not an easier run.
            </p>
          )}
        </div>
      )}

      {/* Readiness only speaks up when it has something to say. */}
      {rec.reason && (
        <p style={{ ...font.small, color: color.textSecondary, margin: `${space(5)} 0 0` }}>
          {rec.reason}
        </p>
      )}

      <div style={{ display: 'flex', gap: space(3), alignItems: 'center', marginTop: space(5), flexWrap: 'wrap' }}>
        {canAct && (
          <button onClick={onAccept} disabled={applying} style={{ ...button.primary, opacity: applying ? 0.6 : 1 }}>
            {applying ? 'Adjusting…' : rec.action === 'rest' ? 'Take today off' : 'Ease today back'}
          </button>
        )}
        {onOpen && (
          <button onClick={onOpen} style={{ ...button.quiet, color: canAct ? color.textSecondary : color.accent }}>
            Open session →
          </button>
        )}
      </div>
    </section>
  );
}
