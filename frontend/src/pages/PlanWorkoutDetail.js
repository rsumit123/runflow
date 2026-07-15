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

const DAY_TYPE_COLORS = { easy: '#3ddc84', long: '#3b82f6', quality: '#ff4d4f', strides: '#f5a623', rest: '#5d6b7a' };

// Sprint (100m) plan day types — colored label + display text.
const SPRINT_DAY_TYPE = {
  accel: { color: '#ff5a1f', label: 'Accel' },
  max_velocity: { color: '#ff8a3d', label: 'Max velocity' },
  speed_endurance: { color: '#e0245e', label: 'Speed endurance' },
  technique: { color: '#3ddc84', label: 'Technique' },
  plyometrics: { color: '#b10dc9', label: 'Plyometrics' },
  test: { color: '#f1c40f', label: 'Test' },
  rest: { color: '#5a5a6a', label: 'Rest' },
};

const STATUS_META = {
  done: { label: 'Done', color: '#3ddc84' },
  missed: { label: 'Missed', color: '#ff4d4f' },
  rest: { label: 'Rest', color: '#5d6b7a' },
  upcoming: { label: 'Upcoming', color: '#5d6b7a' },
};

const cardStyle = { backgroundColor: '#0b0f14', borderRadius: '8px', padding: '16px', marginBottom: '16px' };
const cardHeading = { fontSize: '13px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', color: '#93a1b1', marginBottom: '12px' };
const backLink = { display: 'inline-block', marginBottom: '20px', color: '#93a1b1', fontSize: '14px' };
const chartTooltipStyle = { backgroundColor: '#111820', border: '1px solid #333', borderRadius: '6px', fontSize: '13px' };

const STEP_TYPE_COLORS = {
  warmup: '#3ddc84',
  run: '#ff5a1f',
  recovery: '#6b7280',
  cooldown: '#3ddc84',
};

function stepColor(type) {
  return STEP_TYPE_COLORS[type] || '#6b7280';
}

function stepAmount(step) {
  const v = step.end_value;
  if (v == null) return '—';
  if (step.end_kind === 'distance') {
    return v >= 1000 ? `${(v / 1000).toFixed(1)} km` : `${v} m`;
  }
  if (step.end_kind === 'time') {
    return v < 60 ? `${v}s` : `${Math.round(v / 60)} min`;
  }
  return String(v);
}

function stepTarget(step) {
  if (step.target_kind === 'pace' && step.pace_low_sec != null && step.pace_high_sec != null) {
    return { text: `${formatPace(step.pace_low_sec)}–${formatPace(step.pace_high_sec)} /km`, muted: false };
  }
  return { text: 'no pace target', muted: true };
}

function StepRow({ step }) {
  const color = stepColor(step.type);
  const target = stepTarget(step);
  return (
    <div style={{ display: 'flex', alignItems: 'stretch', gap: '10px', padding: '8px 0' }}>
      <div style={{ width: '4px', borderRadius: '2px', backgroundColor: color, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '15px', fontWeight: 700, color: '#e8edf2' }}>{stepAmount(step)}</span>
          <span style={{
            color, backgroundColor: `${color}22`, padding: '2px 7px', borderRadius: '4px',
            fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px',
          }}>
            {step.type}
          </span>
        </div>
        <div style={{ fontSize: '13px', color: target.muted ? '#666' : '#93a1b1', marginTop: '2px' }}>
          {target.text}
        </div>
        {step.note && (
          <div style={{ fontSize: '12px', color: '#666', marginTop: '2px', lineHeight: 1.5 }}>{step.note}</div>
        )}
      </div>
    </div>
  );
}

function StepTimeline({ steps }) {
  return (
    <div>
      {steps.map((s, i) => {
        if (s.type === 'repeat') {
          const children = Array.isArray(s.steps) ? s.steps : [];
          return (
            <div key={i} style={{ margin: '10px 0', backgroundColor: '#111820', borderRadius: '8px', padding: '10px 12px' }}>
              <div style={{ fontSize: '13px', fontWeight: 700, color: '#ff5a1f', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>
                {s.iterations != null ? s.iterations : '?'} × repeat
              </div>
              <div style={{ borderLeft: '2px solid #ff5a1f', paddingLeft: '12px', marginLeft: '2px' }}>
                {children.map((cs, ci) => <StepRow key={ci} step={cs} />)}
              </div>
            </div>
          );
        }
        return <StepRow key={i} step={s} />;
      })}
    </div>
  );
}

function StatRow({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '12px', padding: '8px 0', borderBottom: '1px solid #1e2936' }}>
      <span style={{ fontSize: '13px', color: '#93a1b1' }}>{label}</span>
      <span style={{ fontSize: '15px', fontWeight: 600, color: '#e8edf2' }}>{value}</span>
    </div>
  );
}

function PlanWorkoutDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [garminId, setGarminId] = useState(null);
  const [pushing, setPushing] = useState(false);
  const [pushError, setPushError] = useState(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setPushError(null);
    setPushing(false);
    setGarminId(null);
    api.get('/plan/workout/' + id)
      .then((res) => {
        setData(res.data);
        setGarminId(res.data?.workout?.garmin_workout_id ?? null);
      })
      .catch((err) => setError(err.response?.data?.detail || 'Failed to load workout'))
      .finally(() => setLoading(false));
  }, [id]);

  const pushToWatch = () => {
    setPushing(true);
    setPushError(null);
    api.post(`/plan/workout/${id}/push`)
      .then((res) => setGarminId(res.data?.garmin_workout_id ?? null))
      .catch((err) => setPushError(err.response?.data?.detail || 'Couldn’t reach Garmin — try again.'))
      .finally(() => setPushing(false));
  };

  const removeFromWatch = () => {
    setPushing(true);
    setPushError(null);
    api.delete(`/plan/workout/${id}/push`)
      .then(() => setGarminId(null))
      .catch((err) => setPushError(err.response?.data?.detail || 'Couldn’t reach Garmin — try again.'))
      .finally(() => setPushing(false));
  };

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '60px', color: '#93a1b1', fontSize: '16px' }}>Loading workout...</div>;
  }

  if (error || !data || !data.workout) {
    return (
      <div>
        <Link to="/training" style={backLink}>&lsaquo; Back to plan</Link>
        <div style={{ padding: '16px', backgroundColor: '#3d1515', border: '1px solid #6b2020', borderRadius: '8px', color: '#ff4d4f' }}>
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

  const steps = structure && Array.isArray(structure.steps) && structure.steps.length > 0 ? structure.steps : null;

  const km = w.target_distance_m != null ? +(w.target_distance_m / 1000).toFixed(1) : null;
  const hasPace = w.pace_low_sec != null && w.pace_high_sec != null;

  // Verdict border color by compliance. hr_drift is amber (did the pace right,
  // HR climbed) — distinct from ran_hard (actually ran too fast).
  const verdictColor = compliance === 'ran_hard' ? '#ff4d4f'
    : compliance === 'hr_drift' ? '#f5a623'
    : compliance === 'on_target' ? '#3ddc84'
    : '#ff5a1f';

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
          <span style={{ fontSize: '13px', color: '#93a1b1' }}>{formatDate(w.date)}</span>
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
        <div style={{ fontSize: '13px', color: '#93a1b1' }}>
          Week {w.week_number != null ? w.week_number : '?'}{plan && plan.weeks != null ? ` of ${plan.weeks}` : ''} &middot; {isSprint ? '100m sprint plan' : '5K plan'}
        </div>
      </div>

      {/* Verdict */}
      {verdict && (
        <div style={{
          backgroundColor: '#111820', borderLeft: `4px solid ${verdictColor}`, borderRadius: '8px',
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
                  <div style={{ fontSize: '11px', fontWeight: 600, color: '#93a1b1', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>Warm-up</div>
                  <div style={{ fontSize: '13px', color: '#e8edf2', lineHeight: 1.5 }}>{structure.warmup}</div>
                </div>
              )}

              {Array.isArray(structure.main_set) && structure.main_set.length > 0 && (
                <div style={{ width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch', marginBottom: '4px' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                    <thead>
                      <tr>
                        {['Reps', 'Distance', 'Effort', 'Recovery'].map((h) => (
                          <th key={h} style={{
                            textAlign: 'left', color: '#93a1b1', fontWeight: 600, padding: '8px',
                            borderBottom: '1px solid #1e2936', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px',
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
                          <td style={{ padding: '8px', borderBottom: '1px solid #1e2936', color: '#e8edf2', whiteSpace: 'nowrap' }}>{m.reps != null ? m.reps : '—'}</td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #1e2936', color: '#e8edf2', whiteSpace: 'nowrap' }}>{m.distance_m != null ? `${m.distance_m}m` : '—'}</td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #1e2936', color: '#e8edf2', whiteSpace: 'nowrap' }}>{m.effort_pct != null ? `${m.effort_pct}%` : '—'}</td>
                          <td style={{ padding: '8px', borderBottom: '1px solid #1e2936', color: '#93a1b1' }}>{m.recovery || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {structure.finisher && (
                <div style={{ marginTop: '14px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: '#93a1b1', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>Finisher</div>
                  <div style={{ fontSize: '13px', color: '#e8edf2', lineHeight: 1.5 }}>{structure.finisher}</div>
                </div>
              )}

              {Array.isArray(structure.cues) && structure.cues.length > 0 && (
                <div style={{ marginTop: '14px' }}>
                  <div style={{ fontSize: '11px', fontWeight: 600, color: '#93a1b1', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '6px' }}>Cues</div>
                  <ul style={{ margin: 0, paddingLeft: '18px', color: '#93a1b1', fontSize: '13px', lineHeight: 1.6 }}>
                    {structure.cues.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}
            </>
          ) : (
            <div style={{ fontSize: '13px', color: '#666' }}>No prescribed structure for this day.</div>
          )}
          {w.description && (
            <div style={{ marginTop: '12px', fontSize: '13px', color: '#93a1b1', lineHeight: 1.5 }}>
              {w.description}
            </div>
          )}
        </div>
      ) : (
        <div style={cardStyle}>
          <div style={cardHeading}>Planned</div>

          {steps && (
            <div style={{ marginBottom: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                {garminId == null ? (
                  <button
                    type="button"
                    onClick={pushToWatch}
                    disabled={pushing}
                    style={{
                      minHeight: '44px', padding: '10px 16px', borderRadius: '8px', border: 'none',
                      backgroundColor: pushing ? '#7a3300' : '#ff5a1f', color: '#fff',
                      fontSize: '14px', fontWeight: 700, cursor: pushing ? 'default' : 'pointer',
                    }}
                  >
                    {pushing ? 'Sending…' : '⌚ Send to watch'}
                  </button>
                ) : (
                  <>
                    <span style={{
                      display: 'inline-flex', alignItems: 'center', minHeight: '44px', padding: '10px 16px',
                      borderRadius: '8px', backgroundColor: '#3ddc8422', border: '1px solid #3ddc84',
                      color: '#3ddc84', fontSize: '14px', fontWeight: 700, boxSizing: 'border-box',
                    }}>
                      ✓ On your watch
                    </span>
                    <button
                      type="button"
                      onClick={removeFromWatch}
                      disabled={pushing}
                      style={{
                        minHeight: '44px', padding: '10px 12px', borderRadius: '8px',
                        border: 'none', backgroundColor: 'transparent', color: '#93a1b1',
                        fontSize: '13px', fontWeight: 600, textDecoration: 'underline',
                        cursor: pushing ? 'default' : 'pointer',
                      }}
                    >
                      {pushing ? 'Removing…' : 'Remove'}
                    </button>
                  </>
                )}
              </div>
              {pushError && (
                <div style={{ marginTop: '8px', fontSize: '13px', color: '#ff4d4f', lineHeight: 1.5 }}>{pushError}</div>
              )}
            </div>
          )}

          {structure && structure.warmup && !steps && (
            <div style={{ marginBottom: '12px' }}>
              <div style={{ fontSize: '11px', fontWeight: 600, color: '#3ddc84', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>Warm-up</div>
              <div style={{ fontSize: '13px', color: '#e8edf2', lineHeight: 1.5 }}>{structure.warmup}</div>
            </div>
          )}
          {km != null && <StatRow label="Target distance" value={`${km} km`} />}
          {hasPace && <StatRow label="Pace band" value={`${formatPace(w.pace_low_sec)}–${formatPace(w.pace_high_sec)} /km`} />}
          {w.hr_ceiling != null && <StatRow label="HR ceiling" value={`≤ ${w.hr_ceiling} bpm`} />}
          {structure && structure.cooldown && !steps && (
            <div style={{ marginTop: '12px' }}>
              <div style={{ fontSize: '11px', fontWeight: 600, color: '#3ddc84', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' }}>Cool-down</div>
              <div style={{ fontSize: '13px', color: '#e8edf2', lineHeight: 1.5 }}>{structure.cooldown}</div>
            </div>
          )}

          {steps && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#93a1b1', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Workout steps
              </div>
              <div style={{ width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                <StepTimeline steps={steps} />
              </div>
            </div>
          )}

          {km == null && !hasPace && w.hr_ceiling == null && !structure && (
            <div style={{ fontSize: '13px', color: '#666' }}>No prescribed targets for this day.</div>
          )}
          {w.description && (
            <div style={{ marginTop: '12px', fontSize: '13px', color: '#93a1b1', lineHeight: 1.5 }}>
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
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#93a1b1', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Reps
              </div>
              <div style={{ width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
                  <thead>
                    <tr>
                      {['Rep', 'Dist', 'Time', 'Pace'].map((h) => (
                        <th key={h} style={{
                          textAlign: 'left', color: '#93a1b1', fontWeight: 600, padding: '8px',
                          borderBottom: '1px solid #1e2936', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px',
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
                        <td style={{ padding: '8px', borderBottom: '1px solid #1e2936', color: '#e8edf2', whiteSpace: 'nowrap' }}>{r.rep != null ? r.rep : i + 1}</td>
                        <td style={{ padding: '8px', borderBottom: '1px solid #1e2936', color: '#e8edf2', whiteSpace: 'nowrap' }}>{r.distance_m != null ? `${r.distance_m}m` : '—'}</td>
                        <td style={{ padding: '8px', borderBottom: '1px solid #1e2936', color: '#e8edf2', whiteSpace: 'nowrap' }}>{r.duration_s != null ? `${r.duration_s}s` : '—'}</td>
                        <td style={{ padding: '8px', borderBottom: '1px solid #1e2936', color: '#e8edf2', whiteSpace: 'nowrap' }}>{r.pace_sec_per_km != null ? `${formatPace(r.pace_sec_per_km)}/km` : '—'}</td>
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
              backgroundColor: '#ff5a1f', color: '#fff', fontSize: '14px', fontWeight: 700,
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
              <span style={{ fontSize: '11px', fontWeight: 700, color: '#ff4d4f', backgroundColor: '#ff4d4f22', padding: '3px 9px', borderRadius: '4px' }}>
                ran hard
              </span>
            )}
            {compliance === 'hr_drift' && (
              <span style={{ fontSize: '11px', fontWeight: 700, color: '#f5a623', backgroundColor: '#f5a62322', padding: '3px 9px', borderRadius: '4px' }}>
                paced right · HR drifted
              </span>
            )}
            {compliance === 'on_target' && (
              <span style={{ fontSize: '11px', fontWeight: 700, color: '#3ddc84', backgroundColor: '#3ddc8422', padding: '3px 9px', borderRadius: '4px' }}>
                ✓ on target
              </span>
            )}
          </div>

          {actual.distance_m != null && <StatRow label="Distance" value={`${(actual.distance_m / 1000).toFixed(2)} km`} />}
          {actual.pace_sec != null && <StatRow label="Pace" value={`${formatPace(actual.pace_sec)} /km`} />}
          {actual.avg_hr != null && <StatRow label="Avg HR" value={`${Math.round(actual.avg_hr)} bpm`} />}
          {actual.max_hr != null && <StatRow label="Max HR" value={`${Math.round(actual.max_hr)} bpm`} />}

          {/* Warm-up / cool-down auto-detected from the run */}
          {actual.phases && (actual.phases.main_pace_sec != null) && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#93a1b1', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Warm-up &amp; cool-down
              </div>
              {[['Warm-up', actual.phases.has_warmup, actual.phases.warmup_sec],
                ['Cool-down', actual.phases.has_cooldown, actual.phases.cooldown_sec]].map(([label, has, secs]) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 0', fontSize: '13px' }}>
                  <span style={{ color: '#93a1b1' }}>{label}</span>
                  {has ? (
                    <span style={{ color: '#3ddc84', fontWeight: 600 }}>✓ {Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</span>
                  ) : (
                    <span style={{ color: '#666' }}>not detected</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* HR over the run */}
          {hrSeries && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#93a1b1', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Heart rate
              </div>
              <div style={{ width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                <ResponsiveContainer width="100%" height={180} minWidth={280}>
                  <LineChart data={hrSeries} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e2936" />
                    <XAxis dataKey="i" tick={false} height={4} />
                    <YAxis tick={{ fill: '#666', fontSize: 10 }} domain={['auto', 'auto']} width={35} />
                    <Tooltip contentStyle={chartTooltipStyle} labelFormatter={() => ''} formatter={(v) => [`${Math.round(v)} bpm`, 'HR']} />
                    {w.hr_ceiling != null && (
                      <ReferenceLine y={w.hr_ceiling} stroke="#f5a623" strokeDasharray="4 4"
                        label={{ value: 'ceiling', position: 'insideTopRight', fill: '#f5a623', fontSize: 10 }} />
                    )}
                    <Line type="monotone" dataKey="bpm" name="HR" stroke="#ff4d4f" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Time in zones */}
          {hrZones && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: '#93a1b1', marginBottom: '10px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                Time in zones
              </div>
              {hrZones.map((z, i) => {
                const secs = z.secs || 0;
                const pct = maxZoneSecs > 0 ? (secs / maxZoneSecs) * 100 : 0;
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px', fontSize: '12px' }}>
                    <span style={{ width: '70px', color: '#93a1b1', flexShrink: 0 }}>
                      Z{z.zone}{z.low_bpm != null ? ` (${z.low_bpm}+)` : ''}
                    </span>
                    <div style={{ flex: 1, backgroundColor: '#111820', borderRadius: '4px', height: '16px', overflow: 'hidden' }}>
                      <div style={{ width: `${pct}%`, height: '100%', backgroundColor: '#ff5a1f', borderRadius: '4px' }} />
                    </div>
                    <span style={{ width: '54px', textAlign: 'right', color: '#e8edf2', flexShrink: 0 }}>{formatSecs(secs)}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Link to full run */}
          {actual.activity_id != null && (
            <Link to={`/activity/${actual.activity_id}`} style={{
              display: 'block', marginTop: '16px', textAlign: 'center',
              backgroundColor: '#ff5a1f', color: '#fff', fontSize: '14px', fontWeight: 700,
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
