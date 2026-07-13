import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid,
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
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

const cardStyle = { backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', marginBottom: '20px', overflow: 'hidden' };
const chartWrap = { width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' };
const chartInner = (minW) => ({ minWidth: minW ? `${minW}px` : undefined, width: '100%' });
const sectionTitle = { fontSize: '18px', fontWeight: 600, color: '#fff', marginBottom: '16px' };

const ZONE_COLORS = { easy: '#22c55e', gray: '#f59e0b', hard: '#ef4444', unknown: '#666' };
const WARN_COLORS = { danger: '#ef4444', warn: '#f59e0b', info: '#3b82f6' };
const DAY_TYPE_COLORS = { easy: '#22c55e', long: '#3b82f6', quality: '#ef4444', strides: '#f59e0b', rest: '#64748b' };

const GrayTooltip = ({ active, payload, label }) => {
  if (!active || !payload || !payload.length) return null;
  return (
    <div style={{ backgroundColor: '#16213e', border: '1px solid #333', borderRadius: '6px', padding: '10px 14px', fontSize: '13px' }}>
      <div style={{ color: '#a0a0b0', marginBottom: '4px' }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color || '#f59e0b' }}>
          {p.name}: {p.value}%
        </div>
      ))}
    </div>
  );
};

function StatCard({ label, value, sub, color }) {
  return (
    <div style={{ backgroundColor: '#16213e', borderRadius: '8px', padding: '14px 10px', textAlign: 'center' }}>
      <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>{label}</div>
      <div style={{ fontSize: '22px', fontWeight: 700, color: color || '#fc5200' }}>{value}</div>
      {sub && <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>{sub}</div>}
    </div>
  );
}

function PlanSection() {
  const [plan, setPlan] = useState(null);
  const [workouts, setWorkouts] = useState([]);
  const [projections, setProjections] = useState(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(null); // holds weeks being built, or null

  const loadProjections = () => api.get('/plan/projections').then((res) => setProjections(res.data));

  const loadPlan = () => {
    setLoading(true);
    return api.get('/plan')
      .then((res) => {
        setPlan(res.data.plan || null);
        setWorkouts(res.data.workouts || []);
        if (!res.data.plan) return loadProjections();
        return undefined;
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadPlan(); }, []);

  const buildPlan = (weeks, targetTimeSec) => {
    setBuilding(weeks);
    api.post('/plan', { weeks, target_time_sec: targetTimeSec })
      .then((res) => {
        setPlan(res.data.plan || null);
        setWorkouts(res.data.workouts || []);
      })
      .catch(() => {})
      .finally(() => setBuilding(null));
  };

  const abandonPlan = () => {
    if (!plan) return;
    if (!window.confirm('Abandon this plan? Your workouts will be removed.')) return;
    api.delete(`/plan/${plan.id}`)
      .then(() => {
        setPlan(null);
        setWorkouts([]);
        return loadProjections();
      })
      .catch(() => {});
  };

  if (loading) {
    return (
      <div style={cardStyle}>
        <div style={{ color: '#a0a0b0', fontSize: '13px' }}>Loading plan...</div>
      </div>
    );
  }

  // ---------- State B: active plan ----------
  if (plan) {
    const goalSec = plan.target_time_sec;
    const narrative = plan.narrative || null;

    // Current week: today vs start_date, clamped 1..weeks
    let currentWeek = 1;
    if (plan.start_date && plan.weeks) {
      const start = new Date(plan.start_date);
      const diffDays = Math.floor((Date.now() - start.getTime()) / 86400000);
      currentWeek = Math.floor(diffDays / 7) + 1;
      if (currentWeek < 1) currentWeek = 1;
      if (currentWeek > plan.weeks) currentWeek = plan.weeks;
    }

    // Group workouts by week_number
    const byWeek = {};
    workouts.forEach((w) => {
      const wn = w.week_number;
      if (!byWeek[wn]) byWeek[wn] = [];
      byWeek[wn].push(w);
    });
    const weekNumbers = Object.keys(byWeek).map(Number).sort((a, b) => a - b);

    return (
      <div style={cardStyle}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 16px', alignItems: 'baseline', marginBottom: '14px' }}>
          <div style={{ fontSize: '20px', fontWeight: 700, color: '#fff' }}>
            5K in <span style={{ color: '#fc5200' }}>{goalSec != null ? formatPace(goalSec) : '—'}</span>
          </div>
          <div style={{ fontSize: '13px', color: '#a0a0b0' }}>
            Goal date {formatDate(plan.goal_date)}
          </div>
          <div style={{ fontSize: '13px', color: '#a0a0b0' }}>
            · Week {currentWeek} of {plan.weeks}
          </div>
          <button
            onClick={abandonPlan}
            style={{
              marginLeft: 'auto', background: 'none', border: 'none', color: '#ef4444',
              fontSize: '12px', fontWeight: 600, cursor: 'pointer', padding: '4px 0',
            }}
          >
            Abandon plan
          </button>
        </div>

        {narrative && narrative.overview && (
          <div style={{
            backgroundColor: '#16213e', borderLeft: '4px solid #fc5200', borderRadius: '6px',
            padding: '12px 14px', marginBottom: '18px', fontSize: '13px', color: '#c8c8d4',
            fontStyle: 'italic', lineHeight: 1.5,
          }}>
            {narrative.overview}
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
          {weekNumbers.map((wn) => {
            const focus = narrative && narrative.weekly
              ? (narrative.weekly.find((w) => w.week === wn) || {}).focus
              : null;
            return (
              <div key={wn}>
                <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '10px', marginBottom: '10px' }}>
                  <span style={{
                    fontSize: '13px', fontWeight: 700, color: '#fff',
                    backgroundColor: wn === currentWeek ? '#fc520022' : 'transparent',
                    border: wn === currentWeek ? '1px solid #fc5200' : '1px solid #333',
                    borderRadius: '4px', padding: '2px 8px',
                  }}>
                    Week {wn}
                  </span>
                  {focus && <span style={{ fontSize: '12px', color: '#a0a0b0' }}>{focus}</span>}
                </div>
                <div style={{
                  display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: '10px',
                }}>
                  {byWeek[wn].map((w) => {
                    const dt = w.day_type || 'easy';
                    const dc = DAY_TYPE_COLORS[dt] || DAY_TYPE_COLORS.easy;
                    const km = w.target_distance_m != null ? +(w.target_distance_m / 1000).toFixed(1) : null;
                    const hasPace = w.pace_low_sec != null && w.pace_high_sec != null;
                    return (
                      <div key={w.id} style={{
                        backgroundColor: '#16213e', borderRadius: '8px', padding: '12px',
                        borderTop: `3px solid ${dc}`,
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                          <span style={{ fontSize: '11px', color: '#a0a0b0' }}>{formatDate(w.date)}</span>
                          <span style={{
                            color: dc, backgroundColor: `${dc}22`, padding: '2px 7px', borderRadius: '4px',
                            fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px',
                          }}>
                            {dt}
                          </span>
                        </div>
                        <div style={{ fontSize: '14px', fontWeight: 600, color: '#fff', marginBottom: '6px' }}>
                          {w.title || 'Workout'}
                        </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', fontSize: '12px', color: '#e0e0e0', marginBottom: w.description ? '8px' : 0 }}>
                          {km != null && <span>{km} km</span>}
                          {hasPace && <span>{formatPace(w.pace_low_sec)}–{formatPace(w.pace_high_sec)}/km</span>}
                          {w.hr_ceiling != null && <span>≤{w.hr_ceiling} bpm</span>}
                        </div>
                        {w.description && (
                          <div style={{ fontSize: '11px', color: '#777', lineHeight: 1.4 }}>{w.description}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  // ---------- State A: no active plan ----------
  const currentSec = projections ? projections.current_5k_sec : null;
  const horizons = (projections && projections.horizons) || [];
  const confidence = projections ? projections.confidence : null;
  const hasEstimate = currentSec != null;

  return (
    <div style={cardStyle}>
      <h2 style={sectionTitle}>Build your 5K plan</h2>

      <div style={{ marginBottom: hasEstimate ? '18px' : 0 }}>
        {hasEstimate ? (
          <div style={{ fontSize: '14px', color: '#e0e0e0' }}>
            Projected 5K now: <span style={{ fontSize: '20px', fontWeight: 700, color: '#fc5200' }}>{formatPace(currentSec)}</span>
            <span style={{ marginLeft: '8px', color: '#666', fontSize: '11px' }}>
              estimate{confidence === 'low' ? ' (rough — improves as you log runs)' : ''}
            </span>
          </div>
        ) : (
          <div style={{ fontSize: '13px', color: '#a0a0b0' }}>
            Log a few runs and I'll project a 5K target.
          </div>
        )}
      </div>

      {hasEstimate && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '10px' }}>
          {horizons.map((h) => {
            const impliedPace = h.target_time_sec != null ? Math.round(h.target_time_sec / 5) : null;
            const isBuilding = building === h.weeks;
            return (
              <button
                key={h.weeks}
                onClick={() => buildPlan(h.weeks, h.target_time_sec)}
                disabled={building != null}
                style={{
                  textAlign: 'left', backgroundColor: '#16213e', border: '1px solid #333',
                  borderRadius: '8px', padding: '14px', cursor: building != null ? 'default' : 'pointer',
                  color: 'inherit', minHeight: '44px', opacity: building != null && !isBuilding ? 0.5 : 1,
                }}
              >
                {isBuilding ? (
                  <div style={{ fontSize: '13px', color: '#fc5200', fontWeight: 600 }}>Building your plan…</div>
                ) : (
                  <>
                    <div style={{ fontSize: '14px', color: '#e0e0e0', marginBottom: '4px' }}>
                      {h.weeks} weeks → <span style={{ fontSize: '18px', fontWeight: 700, color: '#fc5200' }}>{formatPace(h.target_time_sec)}</span>
                    </div>
                    <div style={{ fontSize: '11px', color: '#666' }}>
                      {impliedPace != null && <>≈{formatPace(impliedPace)}/km</>}
                      {h.improvement_pct != null && <>, {h.improvement_pct}% faster</>}
                    </div>
                  </>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Training() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.get('/training')
      .then((res) => {
        setData(res.data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to load training data');
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '60px', color: '#a0a0b0' }}>Loading training...</div>;
  }

  if (error) {
    return (
      <div>
        <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff', marginBottom: '16px' }}>Training</h1>
        <div style={{ padding: '16px', backgroundColor: '#3d1515', border: '1px solid #6b2020', borderRadius: '8px', color: '#ff6b6b' }}>{error}</div>
      </div>
    );
  }

  const fm = data?.fitness_model || {};
  const gz = data?.gray_zone || {};
  const warnings = data?.warnings || [];
  const counts = gz.counts_14d || {};
  const pctEasy = gz.pct_easy_14d;
  const hasRecent = pctEasy !== null && pctEasy !== undefined;

  // Easy % hero color
  let easyColor = '#22c55e';
  if (hasRecent) {
    if (pctEasy === 0) easyColor = '#ef4444';
    else if (pctEasy < 50) easyColor = '#f59e0b';
    else if (pctEasy < 70) easyColor = '#fbbf24';
  }

  // Zone breakdown counts
  const zoneCounts = [
    { key: 'easy', label: 'Easy', n: counts.easy || 0 },
    { key: 'gray', label: 'Gray', n: counts.gray || 0 },
    { key: 'hard', label: 'Hard', n: counts.hard || 0 },
  ];
  const zoneTotal = zoneCounts.reduce((s, z) => s + z.n, 0);

  // Weekly gray trend: oldest -> newest, skip null pct_gray
  const trend = (gz.trend_weekly || [])
    .filter((t) => t && t.pct_gray !== null && t.pct_gray !== undefined)
    .slice()
    .sort((a, b) => b.weeks_ago - a.weeks_ago)
    .map((t) => ({
      label: t.weeks_ago === 0 ? 'now' : `${t.weeks_ago}w`,
      pct_gray: t.pct_gray,
      runs: t.runs,
    }));

  const recentRuns = gz.recent_runs || [];
  const acwr = fm.acwr;

  return (
    <div>
      <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff', marginBottom: '24px' }}>Training</h1>

      {/* Plan section */}
      <PlanSection />

      {/* a) Headline hero */}
      <div style={cardStyle}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'center' }}>
          <div style={{ flex: '1 1 180px', textAlign: 'center' }}>
            <div style={{ fontSize: '56px', fontWeight: 800, color: easyColor, lineHeight: 1 }}>
              {hasRecent ? `${pctEasy}%` : '—'}
            </div>
            <div style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginTop: '8px' }}>
              Easy (last 14 days)
            </div>
            <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
              {hasRecent ? 'Target: ~80%' : 'No recent runs'}
            </div>
          </div>
          <div style={{ flex: '1 1 200px', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 90px', backgroundColor: '#16213e', borderRadius: '8px', padding: '14px 10px', textAlign: 'center' }}>
              <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>HR Ceiling</div>
              <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>
                {fm.easy_hr_ceiling != null ? fm.easy_hr_ceiling : '—'}
              </div>
              <div style={{ fontSize: '11px', color: '#666' }}>bpm easy max</div>
            </div>
            <div style={{ flex: '1 1 90px', backgroundColor: '#16213e', borderRadius: '8px', padding: '14px 10px', textAlign: 'center' }}>
              <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Easy Pace</div>
              <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>
                {fm.easy_pace_sec != null ? formatPace(fm.easy_pace_sec) : '—'}
              </div>
              <div style={{ fontSize: '11px', color: '#666' }}>
                /km
                {fm.easy_pace_method === 'estimate' && (
                  <span style={{ marginLeft: '4px', color: '#f59e0b', backgroundColor: '#f59e0b18', padding: '1px 5px', borderRadius: '4px', fontSize: '9px', fontWeight: 600 }}>estimate</span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* b) Warnings */}
      <div style={cardStyle}>
        <h2 style={sectionTitle}>Coaching Flags</h2>
        {warnings.length === 0 ? (
          <div style={{ color: '#22c55e', fontSize: '13px' }}>No flags — nice work.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {warnings.map((w, i) => {
              const c = WARN_COLORS[w.level] || WARN_COLORS.info;
              return (
                <div key={i} style={{ backgroundColor: '#16213e', borderLeft: `4px solid ${c}`, borderRadius: '6px', padding: '12px 14px' }}>
                  <div style={{ fontSize: '14px', fontWeight: 700, color: '#fff', marginBottom: '4px' }}>{w.title}</div>
                  <div style={{ fontSize: '13px', color: '#a0a0b0' }}>{w.detail}</div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* c) Fitness model stats grid */}
      <div style={cardStyle}>
        <h2 style={sectionTitle}>Fitness Model</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '10px' }}>
          <StatCard label="Weekly Vol (28d)" value={fm.weekly_volume_28d_km != null ? fm.weekly_volume_28d_km : '—'} sub="km" />
          <StatCard
            label="ACWR"
            value={acwr != null ? acwr : '—'}
            sub="acute:chronic"
            color={acwr != null && acwr > 1.3 ? '#ef4444' : '#4ade80'}
          />
          <StatCard label="Longest 28d" value={fm.longest_run_28d_km != null ? fm.longest_run_28d_km : '—'} sub="km" />
          <StatCard label="Longest 90d" value={fm.longest_run_90d_km != null ? fm.longest_run_90d_km : '—'} sub="km" />
          <StatCard label="Threshold Pace" value={fm.threshold_pace_sec != null ? formatPace(fm.threshold_pace_sec) : '—'} sub="/km" />
          <StatCard
            label="HR Coverage"
            value={fm.hr_coverage != null ? fm.hr_coverage : '—'}
            sub={fm.total_runs != null ? `of ${fm.total_runs} runs` : 'runs'}
          />
        </div>
      </div>

      {/* d) 14-day zone breakdown */}
      <div style={cardStyle}>
        <h2 style={sectionTitle}>14-Day Zone Breakdown</h2>
        {zoneTotal === 0 ? (
          <div style={{ color: '#666', fontSize: '13px' }}>No classified runs in the last 14 days.</div>
        ) : (
          <>
            <div style={{ display: 'flex', width: '100%', height: '28px', borderRadius: '6px', overflow: 'hidden', marginBottom: '12px' }}>
              {zoneCounts.filter((z) => z.n > 0).map((z) => (
                <div key={z.key} title={`${z.label}: ${z.n}`} style={{
                  width: `${(z.n / zoneTotal) * 100}%`,
                  backgroundColor: ZONE_COLORS[z.key],
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: '12px', fontWeight: 700, color: '#0f0f1a',
                }}>
                  {z.n}
                </div>
              ))}
            </div>
            <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
              {zoneCounts.map((z) => (
                <div key={z.key} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: '#a0a0b0' }}>
                  <span style={{ width: '10px', height: '10px', borderRadius: '2px', backgroundColor: ZONE_COLORS[z.key] }} />
                  {z.label}: <span style={{ color: '#fff', fontWeight: 600 }}>{z.n}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* e) Weekly gray-zone trend */}
      <div style={cardStyle}>
        <h2 style={sectionTitle}>Weekly Gray-Zone Trend</h2>
        {trend.length === 0 ? (
          <div style={{ color: '#666', fontSize: '13px' }}>Not enough data to chart the trend yet.</div>
        ) : (
          <div style={chartWrap}>
            <div style={chartInner(400)}>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={trend} margin={{ top: 5, right: 10, left: -15, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#252540" />
                  <XAxis dataKey="label" tick={{ fill: '#666', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#666', fontSize: 10 }} domain={[0, 100]} width={35} tickFormatter={(v) => `${v}%`} />
                  <Tooltip content={<GrayTooltip />} cursor={{ fill: '#ffffff08' }} />
                  <Bar dataKey="pct_gray" name="Gray %" fill="#f59e0b" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>

      {/* f) Recent runs list */}
      <div style={cardStyle}>
        <h2 style={sectionTitle}>Recent Runs</h2>
        {recentRuns.length === 0 ? (
          <div style={{ color: '#666', fontSize: '13px' }}>No recent runs.</div>
        ) : (
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <div style={{
              display: 'grid', gridTemplateColumns: '90px 1fr 70px 80px 70px 90px', gap: '6px',
              padding: '8px 10px', fontSize: '10px', fontWeight: 600, textTransform: 'uppercase',
              letterSpacing: '0.5px', color: '#666', borderBottom: '1px solid #252540', minWidth: '520px',
            }}>
              <div>Date</div>
              <div>Name</div>
              <div style={{ textAlign: 'right' }}>Dist</div>
              <div style={{ textAlign: 'right' }}>Pace</div>
              <div style={{ textAlign: 'right' }}>HR</div>
              <div style={{ textAlign: 'right' }}>Zone</div>
            </div>
            {recentRuns.map((r) => {
              const zone = r.zone || 'unknown';
              const zc = ZONE_COLORS[zone] || ZONE_COLORS.unknown;
              return (
                <Link key={r.id} to={`/activity/${r.id}`} style={{ textDecoration: 'none', color: 'inherit' }}>
                  <div style={{
                    display: 'grid', gridTemplateColumns: '90px 1fr 70px 80px 70px 90px', gap: '6px',
                    padding: '10px', alignItems: 'center', borderBottom: '1px solid #1e1e35',
                    fontSize: '13px', minWidth: '520px', minHeight: '44px', cursor: 'pointer',
                  }}>
                    <div style={{ color: '#a0a0b0', fontSize: '12px', whiteSpace: 'nowrap' }}>{formatDate(r.start_date)}</div>
                    <div style={{ color: '#fc5200', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.name || 'Untitled'}
                    </div>
                    <div style={{ textAlign: 'right', color: '#e0e0e0' }}>{r.distance_km != null ? `${r.distance_km}` : '—'}</div>
                    <div style={{ textAlign: 'right', color: '#e0e0e0' }}>{r.pace_sec != null ? formatPace(r.pace_sec) : '—'}</div>
                    <div style={{ textAlign: 'right', color: '#e0e0e0' }}>{r.avg_hr != null ? Math.round(r.avg_hr) : '—'}</div>
                    <div style={{ textAlign: 'right' }}>
                      <span style={{ color: zc, backgroundColor: `${zc}22`, padding: '2px 7px', borderRadius: '4px', fontSize: '11px', fontWeight: 600, textTransform: 'capitalize' }}>
                        {zone}
                      </span>
                      {r.basis === 'pace' && (
                        <span title="No HR for this run — zone inferred from pace"
                              style={{ marginLeft: '6px', color: '#777', fontSize: '10px', fontStyle: 'italic' }}>(pace-est)</span>
                      )}
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export default Training;
