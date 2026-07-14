import React from 'react';
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip,
  ReferenceArea, ReferenceLine, ResponsiveContainer, Cell,
} from 'recharts';
import { color, font, space } from '../theme';

const fmtPace = (sec) => `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}`;

function PaceTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;
  const r = payload[0].payload;
  return (
    <div style={{
      backgroundColor: color.surfaceRaised, borderRadius: '8px', padding: space(3),
      border: `1px solid ${color.hairline}`, ...font.small, color: color.text,
    }}
    >
      <div style={{ fontWeight: 600 }}>{r.date}</div>
      <div style={{ color: color.textSecondary, marginTop: '2px' }}>
        {r.distance_km} km · {fmtPace(r.pace_sec)}/km
      </div>
      <div style={{ color: r.in_easy_zone ? color.good : color.bad, marginTop: '2px', fontWeight: 600 }}>
        {r.avg_hr} bpm {r.in_easy_zone ? '· easy' : '· above easy'}
      </div>
    </div>
  );
}

/**
 * Pace against heart rate, with the easy zone shaded.
 *
 * The point of the chart is the EMPTY green box: if none of your runs sit inside
 * it, no amount of prose will land the way this does.
 */
export default function PaceHrScatter({ data }) {
  if (!data || !data.runs || data.runs.length === 0) return null;

  const { runs, easy_hr_ceiling: ceiling, in_easy_zone: inZone, total } = data;

  const paces = runs.map((r) => r.pace_sec);
  const hrs = runs.map((r) => r.avg_hr);
  const padP = 20;
  const padH = 8;
  const xMin = Math.min(...paces) - padP;
  const xMax = Math.max(...paces) + padP;
  const yMin = Math.min(...hrs, ceiling || 999) - padH;
  const yMax = Math.max(...hrs) + padH;

  return (
    <div>
      <div style={{ ...font.label, color: color.textMuted }}>Effort check</div>
      <h2 style={{ ...font.h2, color: color.text, margin: `${space(2)} 0 0` }}>
        {inZone === 0
          ? 'None of your runs are in the easy zone'
          : `${inZone} of ${total} runs are genuinely easy`}
      </h2>
      <p style={{ ...font.small, color: color.textSecondary, margin: `${space(2)} 0 ${space(5)}` }}>
        Every run plotted by pace and average heart rate. The green band is easy —
        at or below {ceiling} bpm. Runs above it cost you recovery without
        building more fitness.
      </p>

      <div style={{ width: '100%', height: 300 }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 8, right: 12, bottom: 28, left: 4 }}>
            <CartesianGrid stroke={color.hairline} strokeDasharray="2 4" />

            {/* The easy zone. The story is whether anything lands in here. */}
            {ceiling && (
              <ReferenceArea
                x1={xMin} x2={xMax} y1={yMin} y2={ceiling}
                fill={color.good} fillOpacity={0.10}
                stroke={color.good} strokeOpacity={0.35} strokeDasharray="4 4"
              />
            )}
            {ceiling && (
              <ReferenceLine
                y={ceiling} stroke={color.good} strokeOpacity={0.5}
                label={{
                  value: `easy ceiling ${ceiling} bpm`, position: 'insideBottomLeft',
                  fill: color.good, fontSize: 11,
                }}
              />
            )}

            <XAxis
              type="number" dataKey="pace_sec" domain={[xMin, xMax]} reversed
              tickFormatter={fmtPace} stroke={color.textMuted}
              tick={{ fontSize: 11, fill: color.textMuted }}
              label={{
                value: 'pace /km (faster →)', position: 'insideBottom', offset: -16,
                fill: color.textMuted, fontSize: 11,
              }}
            />
            <YAxis
              type="number" dataKey="avg_hr" domain={[yMin, yMax]}
              stroke={color.textMuted} tick={{ fontSize: 11, fill: color.textMuted }}
              label={{
                value: 'avg HR', angle: -90, position: 'insideLeft',
                fill: color.textMuted, fontSize: 11,
              }}
            />
            <ZAxis type="number" dataKey="distance_km" range={[40, 180]} />
            <Tooltip content={<PaceTooltip />} cursor={{ strokeDasharray: '3 3', stroke: color.textMuted }} />

            <Scatter data={runs} fillOpacity={0.85}>
              {runs.map((r) => (
                <Cell key={r.id} fill={r.in_easy_zone ? color.good : color.bad} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
