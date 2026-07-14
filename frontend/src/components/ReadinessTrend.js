import React from 'react';
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer,
} from 'recharts';
import { color, font, space } from '../theme';

const shortDate = (d) => {
  const parts = (d || '').split('-');
  return parts.length === 3 ? `${parts[2]}/${parts[1]}` : d;
};

function TrendTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0].payload;
  const row = (k, v, unit) => (v == null ? null : (
    <div key={k} style={{ color: color.textSecondary }}>
      {k} <span style={{ color: color.text, ...font.numeric }}>{v}{unit}</span>
    </div>
  ));
  return (
    <div style={{
      backgroundColor: color.surfaceRaised, borderRadius: '8px', padding: space(3),
      border: `1px solid ${color.hairline}`, ...font.small,
    }}
    >
      <div style={{ color: color.text, fontWeight: 600 }}>{label}</div>
      <div style={{ marginTop: space(1) }}>
        {row('Readiness', d.readiness_score, '')}
        {row('Sleep', d.sleep_hours, ' h')}
        {row('Body Battery', d.body_battery_peak, '')}
        {row('Resting HR', d.resting_hr, ' bpm')}
      </div>
    </div>
  );
}

/**
 * Readiness over time. A single day's score tells you almost nothing — the
 * question is always "compared to what?", and only the trend answers it.
 */
export default function ReadinessTrend({ data }) {
  const days = (data && data.days) || [];
  if (days.length < 2) {
    return (
      <div>
        <div style={{ ...font.label, color: color.textMuted }}>Recovery trend</div>
        <p style={{ ...font.small, color: color.textSecondary, marginTop: space(2) }}>
          Building your trend — one day recorded so far. A few more and the shape
          of your recovery becomes readable.
        </p>
      </div>
    );
  }

  const rows = days.map((d) => ({ ...d, label: shortDate(d.date) }));
  const hrvLanded = rows.find((d) => d.hrv_status && d.hrv_status !== 'NONE');

  return (
    <div>
      <div style={{ ...font.label, color: color.textMuted }}>Recovery trend</div>
      <h2 style={{ ...font.h2, color: color.text, margin: `${space(2)} 0 ${space(5)}` }}>
        Readiness over {rows.length} day{rows.length === 1 ? '' : 's'}
      </h2>

      <div style={{ width: '100%', height: 220 }}>
        <ResponsiveContainer>
          <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}>
            <defs>
              <linearGradient id="readinessFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={color.accent} stopOpacity={0.35} />
                <stop offset="100%" stopColor={color.accent} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={color.hairline} strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="label" stroke={color.textMuted}
              tick={{ fontSize: 11, fill: color.textMuted }} tickLine={false}
            />
            <YAxis
              domain={[0, 100]} stroke={color.textMuted}
              tick={{ fontSize: 11, fill: color.textMuted }} tickLine={false} axisLine={false}
            />
            <Tooltip content={<TrendTooltip />} />

            {/* The line below which we ease a hard session back. */}
            <ReferenceLine
              y={50} stroke={color.warn} strokeOpacity={0.4} strokeDasharray="4 4"
              label={{ value: 'ease back below 50', position: 'insideTopRight',
                       fill: color.warn, fontSize: 10 }}
            />
            {hrvLanded && (
              <ReferenceLine
                x={hrvLanded.label} stroke={color.good} strokeOpacity={0.5}
                label={{ value: 'HRV baseline', position: 'top', fill: color.good, fontSize: 10 }}
              />
            )}

            <Area
              type="monotone" dataKey="readiness_score" stroke={color.accent}
              strokeWidth={2} fill="url(#readinessFill)" connectNulls
            />
            <Line
              type="monotone" dataKey="body_battery_peak" stroke={color.textMuted}
              strokeWidth={1} dot={false} strokeDasharray="3 3" connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
