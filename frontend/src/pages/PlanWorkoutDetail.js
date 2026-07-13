import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  ResponsiveContainer, LineChart, Line, ReferenceLine,
  XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts';
import api from '../api';

function formatPace(secPerKm) {
  if (secPerKm === null || secPerKm === undefined) return '-';
  const mins = Math.floor(secPerKm / 60);
  const secs = Math.round(secPerKm % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatDate(dateString) {
  if (!dateString) return '-';
  const d = new Date(dateString);
  return d.toLocaleDateString('en-US', { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric' });
}

const DAY_TYPE_COLORS = { easy: '#22c55e', long: '#3b82f6', quality: '#ef4444', strides: '#f59e0b', rest: '#64748b' };

// Sprint (100m) plan day types — colored label + display text.
const SPRINT_DAY_TYPE = {
  accel: { color: '#fc5200', label: 'Accel' },
  max_velocity: { color: '#ff8a3d', label: 'Max velocity' },
  speed_endurance: { color: '#e0245e', label: 'Speed endurance' },
  technique: { color: '#3d9970', label: 'Technique' },
  plyometrics: { color: '#b10dc9', label: 'Plyometrics' },
  test: { color: '#f1c40f', label: 'Test' },
  rest: { color: '#5a5a6a', label: 'Rest' },
};

const STATUS_META = {
  done: { label: 'Done', color: '#22c55e' },
  missed: { label: 'Missed', color: '#ef4444' },
  rest: { label: 'Rest', color: '#64748b' },
  upcoming: { label: 'Upcoming', color: '#64748b' },
};

const cardStyle = { backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', marginBottom: '16px' };
const cardHeading = { fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: '#a0a0b0', marginBottom: '12px' };
const backLink = { display: 'inline-block', marginBottom: '20px', color: '#a0a0b0', fontSize: '14px' };
const chartTooltipStyle = { backgroundColor: '#16213e', border: '1px solid #333', borderRadius: '6px', fontSize: '13px' };

function StatRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '12px', padding: '8px 0', borderBottom: '1px solid #252540' }}>
      <span style={{ fontSize: '13px', color: '#a0a0b0' }}>{label}</span>
      <span style={{ fontSize: '15px', fontWeight: 600, color: '#e0e0e0' }}>{value}</span>
    </div>
  );
}

function PlanWorkoutDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api.get('/plan/workout/' + id)
      .then((res) => setData(res.data))
      .catch((err) => setError(err.response?.data?.detail || 'Failed to load workout'))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '60px', color: '#a0a0b0', fontSize: '16px' }}>Loading workout...</div>;
  }

  if (error || !data || !data.workout) {
    return (
      <div>
        <Link to="/training" style={backLink}>&lsaquo; Back to plan</Link>
        <div style={{ padding: '16px', backgroundColor: '#3d1515', border: '1px solid #6b2020', borderRadius: '8px', color: '#ff6b6b' }}>
          {error || 'Workout not found'}
        </div>
      </div>
    );
  }

  const w = data.workout;
  const plan = data.plan || null;
  const actual = data.actual || null;
  const verdict = data.verdict || null;

  const isSprint = plan && plan.goal_type === 'sprint_100m';
  const dt = w.day_type || 'easy';
  const sprintMeta = isSprint ? (SPRINT_DAY_TYPE[dt] || SPRINT_DAY_TYPE.rest) : null;
  const dc = isSprint ? sprintMeta.color : (DAY_TYPE_COLORS[dt] || DAY_TYPE_COLORS.easy);
  const dtLabel = isSprint ? sprintMeta.label : dt;
  const structure = w.structure || null;
  const status = w.status || 'upcoming';
  const statusMeta = STATUS_META[status] || STATUS_META.upcoming;
  const compliance = w.compliance || null;

  const km = w.target_distance_m != null ? +(w.target_distance_m / 1000).toFixed(1) : null;
  const hasPace = w.pace_low_sec != null && w.pace_high_sec != null;

  // Verdict border color by compliance
  const verdictColor = compliance === 'ran_hard' ? '#f59e0b'
    : compliance === 'on_target' ? '#22c55e'
    : '#fc5200';

  const showActual = status === 'done' && actual;
  const hrSeries = showActual && Array.isArray(actual.heartrate) && actual.heartrate.length > 0
    ? actual.heartrate.map((v, i) => ({ i, bpm: v }))
    : null;
  const hrZones = showActual && Array.isArray(actual.hr_zones) && actual.hr_zones.length > 0
    ? actual.hr_zones
    : null;
  const maxZoneSecs = hrZones ? hrZones.reduce((m, z) => Math.max(m, z.secs || 0), 0) : 0;

  const formatSecs = (s) => {
    if (s == null) return '-';
    const mins = Math.floor(s / 60);
    const secs = Math.round(s % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  return (
    <div>
      <Link to="/training" style={backLink}>&lsaquo; Back to plan</Link>

      {/* Header */}
      <div style={{ marginBottom: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', marginBottom: '8px' }}>
          <span style={{ fontSize: '13px', color: '#a0a0b0' }}>{formatDate(w.date)}</span>
          <span style={{
            color: dc, backgroundColor: `${dc}22`, padding: '2px 8px', borderRadius: '4px',
            fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px',
          }}>
            {dtLabel}
          </span>
          <span style={{
            marginLeft: 'auto', color: statusMeta.color, backgroundColor: `${statusMeta.color}22`,
            padding: '3px 10px', borderRadius: '20px', fontSize: '11px', fontWeight: 700,
          }}>
            {statusMeta.label}
          </span>
        </div>
        <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff', marginBottom: '4px', wordBreak: 'break-word' }}>
          {w.title || 'Workout'}
        </h1>
        <div style={{ fontSize: '13px', color: '#a0a0b0' }}>
          Week {w.week_number != null ? w.week_number : '?'}{plan && plan.weeks != null ? ` of ${plan.weeks}` : ''} &middot; {isSprint ? '100m sprint plan' : '5K plan'}
        </div>
      </div>

      {/* Verdict */}
      {verdict && (
        <div style={{
          backgroundColor: '#16213e', borderLeft: `4px solid ${verdictColor}`, borderRadius: '8px',
          padding: '14px 16px', marginBottom: '16px', fontSize: '14px', color: '#e8e8ef', lineHeight: 1.55,
        }}>
          {verdict}
        </div>
      )}

      {/* Planned — sprint */}
      {isSprint ? (
        <div style={cardStyle}>
          <div style={cardHeading}>Planned</div>
          {structure ? (
            <>
              {structure.warmup && (
                <div style={{ marginBottom: '14px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: '#a0a0b0', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>Warm-up</div>
                  <div style={{ fontSize: '13px', color: '#e0e0e0', lineHeight: 1.5 }}>{structure.warmup}</div>
                </div>
              )}

              {Array.isArray(structure.main_set) && structure.main_set.length > 0 && (
                <div style={{ width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch', marginBottom: '4px' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                    <thead>
                      <tr>
                        {['Reps', 'Distance', 'Effort', 'Recovery'].map((h) => (
                          <th key={h} style={{
                            textAlign: 'left', color: '#a0a0b0', fontWeight: 600, padding: '8px',
                            borderBottom: '1px solid #252540', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px',
                            whiteSpace: 'nowrap',
                          }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {structure.main_set.map((m, i) => (
                        <tr key={i}>
                          <td style={{ padding: '8px', borderBottom: '1px solid #252540', color: '#e0e0e0', whiteSpace: 'nowrap' }}>{m.reps != null ? m.reps : '—'}</td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #252540', color: '#e0e0e0', whiteSpace: 'nowrap' }}>{m.distance_m != null ? `${m.distance_m}m` : '—'}</td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #252540', color: '#e0e0e0', whiteSpace: 'nowrap' }}>{m.effort_pct != null ? `${m.effort_pct}%` : '—'}</td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #252540', color: '#a0a0b0' }}>{m.recovery || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {structure.finisher && (
                <div style={{ marginTop: '14px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: '#a0a0b0', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>Finisher</div>
                  <div style={{ fontSize: '13px', color: '#e0e0e0', lineHeight: 1.5 }}>{structure.finisher}</div>
                </div>
              )}

              {Array.isArray(structure.cues) && structure.cues.length > 0 && (
                <div style={{ marginTop: '14px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: '#a0a0b0', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '6px' }}>Cues</div>
                  <ul style={{ margin: 0, paddingLeft: '18px', color: '#c8c8d4', fontSize: '13px', lineHeight: 1.6 }}>
                    {structure.cues.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}
            </>
          ) : (
            <div style={{ fontSize: '13px', color: '#666' }}>No prescribed structure for this day.</div>
          )}
          {w.description && (
            <div style={{ marginTop: '12px', fontSize: '13px', color: '#a0a0b0', lineHeight: 1.5 }}>
              {w.description}
            </div>
          )}
        </div>
      ) : (
        <div style={cardStyle}>
          <div style={cardHeading}>Planned</div>
          {km != null && <StatRow label="Target distance" value={`${km} km`} />}
          {hasPace && <StatRow label="Pace band" value={`${formatPace(w.pace_low_sec)}–${formatPace(w.pace_high_sec)} /km`} />}
          {w.hr_ceiling != null && <StatRow label="HR ceiling" value={`≤ ${w.hr_ceiling} bpm`} />}
          {km == null && !hasPace && w.hr_ceiling == null && (
            <div style={{ fontSize: '13px', color: '#666' }}>No prescribed targets for this day.</div>
          )}
          {w.description && (
            <div style={{ marginTop: '12px', fontSize: '13px', color: '#a0a0b0', lineHeight: 1.5 }}>
              {w.description}
            </div>
          )}
        </div>
      )}

      {/* Actual — sprint (whenever actual present) */}
      {isSprint && actual && (
        <div style={cardStyle}>
          <div style={{ ...cardHeading, marginBottom: '12px' }}>How it went</div>
          {actual.best_100m_sec != null && <StatRow label="Best 100m" value={`${actual.best_100m_sec}s`} />}
          {actual.fade_pct != null && <StatRow label="Fade" value={`${actual.fade_pct}%`} />}
          {actual.fastest_rep_sec != null && <StatRow label="Fastest rep" value={`${actual.fastest_rep_sec}s`} />}

          {Array.isArray(actual.reps) && actual.reps.length > 0 && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#a0a0b0', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Reps
              </div>
              <div style={{ width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                  <thead>
                    <tr>
                      {['Rep', 'Dist', 'Time', 'Pace'].map((h) => (
                        <th key={h} style={{
                          textAlign: 'left', color: '#a0a0b0', fontWeight: 600, padding: '8px',
                          borderBottom: '1px solid #252540', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px',
                          whiteSpace: 'nowrap',
                        }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {actual.reps.map((r, i) => (
                      <tr key={i}>
                        <td style={{ padding: '8px', borderBottom: '1px solid #252540', color: '#e0e0e0', whiteSpace: 'nowrap' }}>{r.rep != null ? r.rep : i + 1}</td>
                        <td style={{ padding: '8px', borderBottom: '1px solid #252540', color: '#e0e0e0', whiteSpace: 'nowrap' }}>{r.distance_m != null ? `${r.distance_m}m` : '—'}</td>
                        <td style={{ padding: '8px', borderBottom: '1px solid #252540', color: '#e0e0e0', whiteSpace: 'nowrap' }}>{r.duration_s != null ? `${r.duration_s}s` : '—'}</td>
                        <td style={{ padding: '8px', borderBottom: '1px solid #252540', color: '#e0e0e0', whiteSpace: 'nowrap' }}>{r.pace_sec_per_km != null ? `${formatPace(r.pace_sec_per_km)}/km` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {actual.activity_id != null && (
            <Link to={`/activity/${actual.activity_id}`} style={{
              display: 'block', marginTop: '16px', textAlign: 'center',
              backgroundColor: '#fc5200', color: '#fff', fontSize: '14px', fontWeight: 700,
              padding: '12px 16px', borderRadius: '8px', minHeight: '44px', boxSizing: 'border-box',
            }}>
              View full interval analysis &rsaquo;
            </Link>
          )}
        </div>
      )}

      {/* Actual — 5K, only when done */}
      {!isSprint && showActual && (
        <div style={cardStyle}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
            <div style={{ ...cardHeading, marginBottom: 0 }}>How it went</div>
            {compliance === 'ran_hard' && (
              <span style={{ fontSize: '11px', fontWeight: 700, color: '#ef4444', backgroundColor: '#ef444422', padding: '3px 9px', borderRadius: '4px' }}>
                ran hard
              </span>
            )}
            {compliance === 'on_target' && (
              <span style={{ fontSize: '11px', fontWeight: 700, color: '#22c55e', backgroundColor: '#22c55e22', padding: '3px 9px', borderRadius: '4px' }}>
                ✓ on target
              </span>
            )}
          </div>

          {actual.distance_m != null && <StatRow label="Distance" value={`${(actual.distance_m / 1000).toFixed(2)} km`} />}
          {actual.pace_sec != null && <StatRow label="Pace" value={`${formatPace(actual.pace_sec)} /km`} />}
          {actual.avg_hr != null && <StatRow label="Avg HR" value={`${Math.round(actual.avg_hr)} bpm`} />}
          {actual.max_hr != null && <StatRow label="Max HR" value={`${Math.round(actual.max_hr)} bpm`} />}

          {/* HR over the run */}
          {hrSeries && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#a0a0b0', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Heart rate
              </div>
              <div style={{ width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                <ResponsiveContainer width="100%" height={180} minWidth={280}>
                  <LineChart data={hrSeries} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#252540" />
                    <XAxis dataKey="i" tick={false} height={4} />
                    <YAxis tick={{ fill: '#666', fontSize: 10 }} domain={['auto', 'auto']} width={35} />
                    <Tooltip contentStyle={chartTooltipStyle} labelFormatter={() => ''} formatter={(v) => [`${Math.round(v)} bpm`, 'HR']} />
                    {w.hr_ceiling != null && (
                      <ReferenceLine y={w.hr_ceiling} stroke="#f59e0b" strokeDasharray="4 4"
                        label={{ value: 'ceiling', position: 'insideTopRight', fill: '#f59e0b', fontSize: 10 }} />
                    )}
                    <Line type="monotone" dataKey="bpm" name="HR" stroke="#ff6b6b" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Time in zones */}
          {hrZones && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#a0a0b0', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Time in zones
              </div>
              {hrZones.map((z, i) => {
                const secs = z.secs || 0;
                const pct = maxZoneSecs > 0 ? (secs / maxZoneSecs) * 100 : 0;
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px', fontSize: '12px' }}>
                    <span style={{ width: '70px', color: '#a0a0b0', flexShrink: 0 }}>
                      Z{z.zone}{z.low_bpm != null ? ` (${z.low_bpm}+)` : ''}
                    </span>
                    <div style={{ flex: 1, backgroundColor: '#16213e', borderRadius: '4px', height: '16px', overflow: 'hidden' }}>
                      <div style={{ width: `${pct}%`, height: '100%', backgroundColor: '#fc5200', borderRadius: '4px' }} />
                    </div>
                    <span style={{ width: '54px', textAlign: 'right', color: '#e0e0e0', flexShrink: 0 }}>{formatSecs(secs)}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Link to full run */}
          {actual.activity_id != null && (
            <Link to={`/activity/${actual.activity_id}`} style={{
              display: 'block', marginTop: '16px', textAlign: 'center',
              backgroundColor: '#fc5200', color: '#fff', fontSize: '14px', fontWeight: 700,
              padding: '12px 16px', borderRadius: '8px', minHeight: '44px', boxSizing: 'border-box',
            }}>
              View full run details &rsaquo;
            </Link>
          )}
        </div>
      )}
    </div>
  );
}

export default PlanWorkoutDetail;
