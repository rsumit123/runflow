import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
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

function sprintMainSetLines(mainSet) {
  if (!Array.isArray(mainSet)) return [];
  return mainSet.map((m) => {
    const reps = m.reps != null ? m.reps : '';
    const dist = m.distance_m != null ? `${m.distance_m}m` : '';
    const eff = m.effort_pct != null ? ` @ ${m.effort_pct}%` : '';
    return `${reps} × ${dist}${eff}`.trim();
  });
}

// Compact rep-scheme summary shared by the Today card and the day cards.
function SprintSummary({ structure, dc }) {
  if (!structure) return null;
  const lines = sprintMainSetLines(structure.main_set);
  const recovery = Array.isArray(structure.main_set) && structure.main_set.length
    ? structure.main_set[0].recovery : null;
  const vol = structure.total_volume_m;
  return (
    <>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', fontSize: '13px', color: '#e0e0e0', alignItems: 'center' }}>
        {lines.map((l, i) => <span key={i}>{l}</span>)}
        {vol != null && (
          <span style={{
            color: dc, backgroundColor: `${dc}22`, padding: '1px 8px', borderRadius: '4px',
            fontSize: '11px', fontWeight: 700,
          }}>
            {vol} m
          </span>
        )}
      </div>
      {recovery && (
        <div style={{ fontSize: '11px', color: '#777', marginTop: '6px' }}>Recovery: {recovery}</div>
      )}
    </>
  );
}

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
  const navigate = useNavigate();
  const [plan, setPlan] = useState(null);
  const [workouts, setWorkouts] = useState([]);
  const [adherence, setAdherence] = useState(null);
  const [projections, setProjections] = useState(null);
  const [progress, setProgress] = useState(null); // sprint plan progress
  const [goalType, setGoalType] = useState('5k'); // builder goal toggle: '5k' | 'sprint'
  const [sprintProjections, setSprintProjections] = useState(null);
  const [sprintLoading, setSprintLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState(null); // holds weeks being built, or null
  const [suggestions, setSuggestions] = useState([]);
  const [dismissedIds, setDismissedIds] = useState(() => new Set());
  const [applyingId, setApplyingId] = useState(null);
  const [selectedWeek, setSelectedWeek] = useState(null);
  const [editingId, setEditingId] = useState(null); // workout id whose move-editor is open
  const [editDate, setEditDate] = useState('');
  const [moving, setMoving] = useState(false);
  const [moveError, setMoveError] = useState(null);
  const [moveWarning, setMoveWarning] = useState(null);
  const [moveNotice, setMoveNotice] = useState(null); // { text, tone: 'good' | 'warn' }
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState(null); // { pushed, total_failed }
  const [syncError, setSyncError] = useState(null);

  const loadProjections = () => api.get('/plan/projections').then((res) => setProjections(res.data));

  const loadSuggestions = () => api.get('/plan/suggestions')
    .then((res) => setSuggestions(res.data.suggestions || []))
    .catch(() => setSuggestions([]));

  const loadPlan = () => {
    setLoading(true);
    return api.get('/plan')
      .then((res) => {
        setPlan(res.data.plan || null);
        setWorkouts(res.data.workouts || []);
        setAdherence(res.data.adherence || null);
        setProgress(res.data.progress || null);
        if (!res.data.plan) return loadProjections();
        // Sprint plans have no suggestions pipeline.
        if (res.data.plan.goal_type === 'sprint_100m') return undefined;
        return loadSuggestions();
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadPlan(); }, []);

  // Default the selected week to the plan's current week whenever the plan identity changes.
  useEffect(() => {
    if (!plan) { setSelectedWeek(null); return; }
    let cw = 1;
    if (plan.start_date && plan.weeks) {
      const start = new Date(plan.start_date);
      const diffDays = Math.floor((Date.now() - start.getTime()) / 86400000);
      cw = Math.floor(diffDays / 7) + 1;
      if (cw < 1) cw = 1;
      if (cw > plan.weeks) cw = plan.weeks;
    }
    setSelectedWeek(cw);
    // key on plan.id so applying a suggestion (which replaces `plan` but keeps
    // the same id) doesn't snap the user back to the week they were viewing.
  }, [plan && plan.id]);

  const applySuggestion = (id) => {
    setApplyingId(id);
    api.post('/plan/suggestions/apply', { id })
      .then((res) => {
        setPlan(res.data.plan || null);
        setWorkouts(res.data.workouts || []);
        setAdherence(res.data.adherence || null);
        return loadSuggestions();
      })
      .catch(() => {})
      .finally(() => setApplyingId(null));
  };

  const dismissSuggestion = (id) => setDismissedIds((prev) => {
    const next = new Set(prev);
    next.add(id);
    return next;
  });

  // Open the inline move-editor for a single workout (closes any other open one).
  const startEditMove = (w) => {
    setEditingId(w.id);
    setEditDate(w.date ? String(w.date).slice(0, 10) : '');
    setMoveError(null);
  };

  const cancelEditMove = () => {
    setEditingId(null);
    setEditDate('');
    setMoveError(null);
  };

  const saveMove = (id) => {
    if (!editDate) return;
    setMoving(true);
    setMoveError(null);
    api.patch('/plan/workout/' + id, { date: editDate })
      .then((res) => {
        setPlan(res.data.plan || null);
        setWorkouts(res.data.workouts || []);
        setAdherence(res.data.adherence || null);
        setEditingId(null);
        setEditDate('');
        setMoveWarning(res.data.warning || null);
        const synced = res.data.garmin_synced;
        if (synced === true) setMoveNotice({ text: 'Moved — and updated on your watch.', tone: 'good' });
        else if (synced === false) setMoveNotice({ text: "Moved, but couldn't update your watch.", tone: 'warn' });
        else setMoveNotice(null);
      })
      .catch(() => setMoveError("Couldn't move — try again."))
      .finally(() => setMoving(false));
  };

  // Push every upcoming structured workout in the plan to the Garmin watch.
  const syncPlanToWatch = () => {
    setSyncing(true);
    setSyncError(null);
    setSyncResult(null);
    api.post('/plan/sync-to-watch')
      .then((res) => {
        setSyncResult({
          pushed: res.data.pushed || 0,
          total_failed: res.data.total_failed || 0,
        });
        // Refresh so each day card's garmin_workout_id is up to date.
        return loadPlan();
      })
      .catch((err) => {
        setSyncError(err.response?.data?.detail || 'Couldn\'t reach Garmin — try again.');
      })
      .finally(() => setSyncing(false));
  };

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

  const buildSprintPlan = (weeks, target100) => {
    setBuilding(weeks);
    const body = { weeks, goal_type: 'sprint_100m' };
    if (target100 != null) body.target_100m_sec = target100;
    api.post('/plan', body)
      .then((res) => {
        setPlan(res.data.plan || null);
        setWorkouts(res.data.workouts || []);
        setProgress(res.data.progress || null);
      })
      .catch(() => {})
      .finally(() => setBuilding(null));
  };

  // Toggle the builder goal; lazily fetch sprint projections the first time.
  const selectGoal = (g) => {
    setGoalType(g);
    if (g === 'sprint' && !sprintProjections && !sprintLoading) {
      setSprintLoading(true);
      api.get('/plan/sprint/projections')
        .then((res) => setSprintProjections(res.data))
        .catch(() => {})
        .finally(() => setSprintLoading(false));
    }
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
    const isSprint = plan.goal_type === 'sprint_100m';
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

    // Group workouts by week_number (workouts within a week sorted by date)
    const byWeek = {};
    workouts.forEach((w) => {
      const wn = w.week_number;
      if (!byWeek[wn]) byWeek[wn] = [];
      byWeek[wn].push(w);
    });
    Object.keys(byWeek).forEach((k) => {
      byWeek[k].sort((a, b) => new Date(a.date || 0) - new Date(b.date || 0));
    });
    const weekNumbers = Object.keys(byWeek).map(Number).sort((a, b) => a - b);
    const totalWeeks = plan.weeks || (weekNumbers.length ? weekNumbers[weekNumbers.length - 1] : 1);

    // Selected week (fall back to current week until the effect initialises it)
    const sel = selectedWeek || currentWeek;
    const selWorkouts = byWeek[sel] || [];

    // Per-week status → tint for the week chips
    const weekStatus = (wn) => {
      const ws = (byWeek[wn] || []).filter((w) => w.status !== 'rest');
      if (ws.length === 0) return 'neutral';
      if (ws.every((w) => w.status === 'done')) return 'done';
      if (ws.some((w) => w.status === 'missed')) return 'missed';
      return 'neutral';
    };
    const chipBg = { done: '#22c55e18', missed: '#ef444418', neutral: '#16213e' };

    // Selected-week date range + narrative focus
    const selDates = selWorkouts.map((w) => w.date).filter(Boolean).map((d) => new Date(d));
    const rangeStart = selDates.length ? formatDate(new Date(Math.min(...selDates.map((d) => d.getTime())))) : null;
    const rangeEnd = selDates.length ? formatDate(new Date(Math.max(...selDates.map((d) => d.getTime())))) : null;
    const dateRange = rangeStart && rangeEnd
      ? (rangeStart === rangeEnd ? rangeStart : `${rangeStart} – ${rangeEnd}`)
      : null;
    const selFocus = narrative && narrative.weekly
      ? ((narrative.weekly.find((w) => w.week === sel) || {}).focus || null)
      : null;

    const visibleSuggestions = suggestions.filter((s) => !dismissedIds.has(s.id));

    const adh = adherence || null;
    const showAdherence = adh && adh.planned_past != null;

    // Today's workout (or the next upcoming one) for the focal "Today" card
    const now = new Date();
    const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    const dateKey = (w) => (w && w.date ? String(w.date).slice(0, 10) : null);
    const todayWorkout = workouts.find((w) => dateKey(w) === todayStr) || null;
    const nextWorkout = workouts
      .filter((w) => dateKey(w) && dateKey(w) > todayStr && (w.day_type || 'easy') !== 'rest')
      .sort((a, b) => new Date(a.date || 0) - new Date(b.date || 0))[0] || null;
    const todayCardWorkout = todayWorkout || nextWorkout || null;
    const todayCardId = todayCardWorkout ? todayCardWorkout.id : null;

    return (
      <div style={cardStyle}>
        {/* a) Header */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px 16px', alignItems: 'baseline', marginBottom: '14px' }}>
          <div style={{ fontSize: '20px', fontWeight: 700, color: '#fff' }}>
            {isSprint ? (
              <>100m in <span style={{ color: '#fc5200' }}>{plan.sprint_target_sec != null ? `${plan.sprint_target_sec.toFixed(1)}s` : '—'}</span></>
            ) : (
              <>5K in <span style={{ color: '#fc5200' }}>{goalSec != null ? formatPace(goalSec) : '—'}</span></>
            )}
          </div>
          <div style={{ fontSize: '13px', color: '#a0a0b0' }}>
            Goal date {formatDate(plan.goal_date)}
          </div>
          <div style={{ fontSize: '13px', color: '#a0a0b0' }}>
            · Week {currentWeek} of {totalWeeks}
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

        {/* a1) Sync the plan's structured workouts to Garmin (5K plans only) */}
        {!isSprint && (
          <div style={{ marginBottom: '16px' }}>
            <button
              onClick={syncPlanToWatch}
              disabled={syncing}
              style={{
                backgroundColor: '#fc5200', border: 'none', borderRadius: '6px', color: '#fff',
                fontSize: '13px', fontWeight: 700, padding: '10px 16px', minHeight: '44px',
                cursor: syncing ? 'default' : 'pointer', opacity: syncing ? 0.6 : 1,
              }}
            >
              {syncing ? 'Syncing…' : '⌚ Sync plan to watch'}
            </button>
            {syncResult && (
              <div style={{ marginTop: '8px' }}>
                <div style={{ fontSize: '12px', color: '#22c55e' }}>
                  ✓ {syncResult.pushed} workout{syncResult.pushed === 1 ? '' : 's'} on your watch
                </div>
                {syncResult.total_failed > 0 && (
                  <div style={{ fontSize: '12px', color: '#f59e0b', marginTop: '4px' }}>
                    {syncResult.total_failed} couldn't be sent
                  </div>
                )}
              </div>
            )}
            {syncError && (
              <div style={{ fontSize: '12px', color: '#ef4444', marginTop: '8px' }}>{syncError}</div>
            )}
          </div>
        )}

        {/* a2) Today — the primary focal point */}
        <div
          onClick={todayCardId != null ? () => navigate('/plan/workout/' + todayCardId) : undefined}
          style={{
            backgroundColor: '#16213e', border: '1px solid #fc520055', borderLeft: '4px solid #fc5200',
            borderRadius: '8px', padding: '16px', marginBottom: '18px',
            cursor: todayCardId != null ? 'pointer' : 'default',
          }}
        >
          {todayWorkout ? (() => {
            const w = todayWorkout;
            const dt = w.day_type || 'easy';
            const meta = isSprint ? (SPRINT_DAY_TYPE[dt] || SPRINT_DAY_TYPE.rest) : null;
            const dc = isSprint ? meta.color : (DAY_TYPE_COLORS[dt] || DAY_TYPE_COLORS.easy);
            const dtLabel = isSprint ? meta.label : dt;
            const km = w.target_distance_m != null ? +(w.target_distance_m / 1000).toFixed(1) : null;
            const hasPace = w.pace_low_sec != null && w.pace_high_sec != null;
            const status = w.status || 'upcoming';
            const actual = w.actual || null;
            const done = status === 'done';
            return (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                  <span style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1px', color: '#fc5200' }}>Today</span>
                  <span style={{
                    color: dc, backgroundColor: `${dc}22`, padding: '2px 7px', borderRadius: '4px',
                    fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px',
                  }}>
                    {dtLabel}
                  </span>
                  <span style={{ marginLeft: 'auto', fontSize: '15px', color: done ? '#22c55e' : '#64748b' }}>
                    {done ? '●' : '○'}
                  </span>
                </div>
                <div style={{ fontSize: '17px', fontWeight: 700, color: '#fff', marginBottom: '8px' }}>
                  {w.title || 'Workout'}
                </div>
                {isSprint ? (
                  <SprintSummary structure={w.structure} dc={dc} />
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', fontSize: '13px', color: '#e0e0e0' }}>
                    {km != null && <span>{km} km</span>}
                    {hasPace && <span>{formatPace(w.pace_low_sec)}–{formatPace(w.pace_high_sec)}/km</span>}
                    {w.hr_ceiling != null && <span>≤{w.hr_ceiling} bpm</span>}
                  </div>
                )}
                {isSprint && done && actual && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px', marginTop: '10px' }}>
                    <span style={{ fontSize: '12px', color: '#c8c8d4' }}>
                      Best 100m: {actual.best_100m_sec != null ? `${actual.best_100m_sec}s` : '—'}
                      {actual.fade_pct != null ? ` · fade ${actual.fade_pct}%` : ''}
                    </span>
                  </div>
                )}
                {!isSprint && done && actual && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px', marginTop: '10px' }}>
                    <span style={{ fontSize: '12px', color: '#c8c8d4' }}>
                      Ran: {actual.pace_sec != null ? `${formatPace(actual.pace_sec)}/km` : '—'}
                      {actual.avg_hr != null ? ` · ${Math.round(actual.avg_hr)} bpm` : ''}
                    </span>
                    {w.compliance === 'ran_hard' && (
                      <span style={{
                        fontSize: '10px', fontWeight: 700, color: '#ef4444', backgroundColor: '#ef444422',
                        padding: '2px 7px', borderRadius: '4px',
                      }}>
                        ran hard
                      </span>
                    )}
                    {w.compliance === 'on_target' && (
                      <span style={{
                        fontSize: '10px', fontWeight: 700, color: '#22c55e', backgroundColor: '#22c55e22',
                        padding: '2px 7px', borderRadius: '4px',
                      }}>
                        ✓ easy
                      </span>
                    )}
                  </div>
                )}
              </>
            );
          })() : nextWorkout ? (
            <>
              <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '1px', color: '#fc5200', marginBottom: '8px' }}>
                Next · {formatDate(nextWorkout.date)}
              </div>
              <div style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>
                {nextWorkout.title || 'Workout'}
              </div>
            </>
          ) : (
            <div style={{ fontSize: '15px', fontWeight: 700, color: '#fff' }}>
              <span style={{ color: '#fc5200' }}>Today</span> · Rest day 😌
            </div>
          )}
        </div>

        {narrative && narrative.overview && (
          <div style={{
            backgroundColor: '#16213e', borderLeft: '4px solid #fc5200', borderRadius: '6px',
            padding: '10px 12px', marginBottom: '16px', fontSize: '12.5px', color: '#c8c8d4',
            lineHeight: 1.45,
          }}>
            {narrative.overview}
          </div>
        )}

        {/* b) Sprint progress strip */}
        {isSprint && progress && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '18px' }}>
            <span style={{
              fontSize: '12px', fontWeight: 600, color: '#e0e0e0', backgroundColor: '#16213e',
              borderRadius: '20px', padding: '5px 12px',
            }}>
              {progress.sessions_done != null ? progress.sessions_done : 0}/{progress.sessions_planned_past != null ? progress.sessions_planned_past : 0} done
            </span>
            {progress.adherence_pct != null && (
              <span style={{
                fontSize: '12px', fontWeight: 700, color: '#22c55e', backgroundColor: '#22c55e18',
                borderRadius: '20px', padding: '5px 12px',
              }}>
                {progress.adherence_pct}%
              </span>
            )}
            {progress.latest_best_100m_sec != null && (
              <span style={{
                fontSize: '12px', fontWeight: 700, color: '#fc5200', backgroundColor: '#fc520018',
                borderRadius: '20px', padding: '5px 12px',
              }}>
                Best 100m: {progress.latest_best_100m_sec}s
              </span>
            )}
          </div>
        )}

        {/* b) Adherence summary strip */}
        {!isSprint && showAdherence && (
          adh.planned_past > 0 ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '18px' }}>
              <span style={{
                fontSize: '12px', fontWeight: 600, color: '#e0e0e0', backgroundColor: '#16213e',
                borderRadius: '20px', padding: '5px 12px',
              }}>
                {adh.done != null ? adh.done : 0}/{adh.planned_past} done
              </span>
              {adh.adherence_pct != null && (
                <span style={{
                  fontSize: '12px', fontWeight: 700, color: '#22c55e', backgroundColor: '#22c55e18',
                  borderRadius: '20px', padding: '5px 12px',
                }}>
                  {adh.adherence_pct}%
                </span>
              )}
              {adh.easy_run_hard > 0 && (
                <span style={{
                  fontSize: '12px', fontWeight: 600, color: '#f59e0b', backgroundColor: '#f59e0b18',
                  borderRadius: '20px', padding: '5px 12px',
                }}>
                  ⚠ {adh.easy_run_hard} easy day{adh.easy_run_hard === 1 ? '' : 's'} run hard
                </span>
              )}
            </div>
          ) : (adh.done || 0) > 0 ? (
            <div style={{ fontSize: '12px', color: '#a0a0b0', marginBottom: '18px' }}>
              {adh.done} run{adh.done === 1 ? '' : 's'} logged so far
              {adh.easy_run_hard > 0 && (
                <span style={{ color: '#f59e0b' }}>
                  {' · '}{adh.easy_run_hard} run hard — ease up
                </span>
              )}
            </div>
          ) : (
            <div style={{ fontSize: '12px', color: '#777', marginBottom: '18px' }}>
              Your plan starts now — track your runs here as you go.
            </div>
          )
        )}

        {/* c) Suggestions panel */}
        {!isSprint && visibleSuggestions.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginBottom: '20px' }}>
            {visibleSuggestions.map((s) => {
              const isOnTrack = s.type === 'on_track';
              const accent = isOnTrack ? '#22c55e' : '#f59e0b';
              const isApplying = applyingId === s.id;
              return (
                <div key={s.id} style={{
                  backgroundColor: '#16213e', borderLeft: `4px solid ${accent}`, borderRadius: '6px',
                  padding: '12px 14px',
                }}>
                  <div style={{ fontSize: '14px', fontWeight: 700, color: '#fff', marginBottom: '4px' }}>
                    {s.title}
                  </div>
                  {s.detail && (
                    <div style={{ fontSize: '13px', color: '#a0a0b0', lineHeight: 1.5 }}>{s.detail}</div>
                  )}
                  {!isOnTrack && (
                    <div style={{ display: 'flex', gap: '8px', marginTop: '10px', flexWrap: 'wrap' }}>
                      <button
                        onClick={() => applySuggestion(s.id)}
                        disabled={applyingId != null}
                        style={{
                          backgroundColor: '#fc5200', border: 'none', borderRadius: '6px', color: '#fff',
                          fontSize: '12px', fontWeight: 700, padding: '8px 16px', minHeight: '44px',
                          cursor: applyingId != null ? 'default' : 'pointer', opacity: applyingId != null && !isApplying ? 0.5 : 1,
                        }}
                      >
                        {isApplying ? 'Applying…' : 'Accept'}
                      </button>
                      <button
                        onClick={() => dismissSuggestion(s.id)}
                        disabled={applyingId != null}
                        style={{
                          backgroundColor: 'transparent', border: '1px solid #333', borderRadius: '6px', color: '#a0a0b0',
                          fontSize: '12px', fontWeight: 600, padding: '8px 16px', minHeight: '44px',
                          cursor: applyingId != null ? 'default' : 'pointer',
                        }}
                      >
                        Dismiss
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* d) Week-at-a-time calendar — month/plan strip */}
        <div style={{ ...chartWrap, marginBottom: '12px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {weekNumbers.map((wn) => {
              const st = weekStatus(wn);
              const isSel = wn === sel;
              return (
                <button
                  key={wn}
                  onClick={() => setSelectedWeek(wn)}
                  style={{
                    fontSize: '12px', fontWeight: 700, color: '#e0e0e0',
                    backgroundColor: chipBg[st] || chipBg.neutral,
                    border: isSel ? '1px solid #fc5200' : '1px solid #2a2a45',
                    borderRadius: '6px', padding: '6px 10px', minHeight: '36px', cursor: 'pointer',
                    whiteSpace: 'nowrap',
                  }}
                >
                  W{wn}
                </button>
              );
            })}
          </div>
        </div>

        {/* Week switcher */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px' }}>
          <button
            onClick={() => setSelectedWeek(Math.max(1, sel - 1))}
            disabled={sel <= 1}
            style={{
              backgroundColor: '#16213e', border: '1px solid #2a2a45', borderRadius: '6px', color: '#e0e0e0',
              fontSize: '16px', fontWeight: 700, width: '40px', minHeight: '40px',
              cursor: sel <= 1 ? 'default' : 'pointer', opacity: sel <= 1 ? 0.4 : 1,
            }}
          >
            ‹
          </button>
          <div style={{ flex: 1, textAlign: 'center' }}>
            <div style={{ fontSize: '15px', fontWeight: 700, color: '#fff' }}>
              Week {sel}{dateRange ? <span style={{ fontSize: '12px', fontWeight: 400, color: '#a0a0b0' }}> ({dateRange})</span> : null}
            </div>
            {selFocus && <div style={{ fontSize: '12px', color: '#a0a0b0', marginTop: '2px' }}>{selFocus}</div>}
          </div>
          <button
            onClick={() => setSelectedWeek(Math.min(totalWeeks, sel + 1))}
            disabled={sel >= totalWeeks}
            style={{
              backgroundColor: '#16213e', border: '1px solid #2a2a45', borderRadius: '6px', color: '#e0e0e0',
              fontSize: '16px', fontWeight: 700, width: '40px', minHeight: '40px',
              cursor: sel >= totalWeeks ? 'default' : 'pointer', opacity: sel >= totalWeeks ? 0.4 : 1,
            }}
          >
            ›
          </button>
        </div>

        {/* Move warning (amber, dismissible) */}
        {moveWarning && (
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: '8px', backgroundColor: '#f59e0b18',
            border: '1px solid #f59e0b55', borderRadius: '6px', padding: '10px 12px', marginBottom: '12px',
          }}>
            <span style={{ flex: 1, fontSize: '12px', color: '#f59e0b', lineHeight: 1.4 }}>{moveWarning}</span>
            <button
              onClick={() => setMoveWarning(null)}
              style={{
                background: 'none', border: 'none', color: '#f59e0b', fontSize: '14px',
                fontWeight: 700, cursor: 'pointer', padding: '0 2px', lineHeight: 1,
              }}
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        )}

        {/* Garmin auto-resync feedback after a move */}
        {moveNotice && (
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: '8px',
            backgroundColor: moveNotice.tone === 'good' ? '#22c55e18' : '#f59e0b18',
            border: `1px solid ${moveNotice.tone === 'good' ? '#22c55e55' : '#f59e0b55'}`,
            borderRadius: '6px', padding: '10px 12px', marginBottom: '12px',
          }}>
            <span style={{
              flex: 1, fontSize: '12px', lineHeight: 1.4,
              color: moveNotice.tone === 'good' ? '#22c55e' : '#f59e0b',
            }}>
              {moveNotice.tone === 'good' ? '⌚ ' : ''}{moveNotice.text}
            </span>
            <button
              onClick={() => setMoveNotice(null)}
              style={{
                background: 'none', border: 'none', fontSize: '14px', fontWeight: 700,
                cursor: 'pointer', padding: '0 2px', lineHeight: 1,
                color: moveNotice.tone === 'good' ? '#22c55e' : '#f59e0b',
              }}
              aria-label="Dismiss"
            >
              ×
            </button>
          </div>
        )}

        {/* Day cards for the selected week */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {selWorkouts.length === 0 ? (
            <div style={{ fontSize: '13px', color: '#666' }}>No workouts scheduled this week.</div>
          ) : selWorkouts.map((w) => {
            const dt = w.day_type || 'easy';
            const meta = isSprint ? (SPRINT_DAY_TYPE[dt] || SPRINT_DAY_TYPE.rest) : null;
            const dc = isSprint ? meta.color : (DAY_TYPE_COLORS[dt] || DAY_TYPE_COLORS.easy);
            const dtLabel = isSprint ? meta.label : dt;
            const km = w.target_distance_m != null ? +(w.target_distance_m / 1000).toFixed(1) : null;
            const hasPace = w.pace_low_sec != null && w.pace_high_sec != null;
            const status = w.status || 'upcoming';
            const actual = w.actual || null;
            const isRest = dt === 'rest' || status === 'rest';
            const isEditing = editingId === w.id;

            // Left status marker
            let marker = null;
            if (status === 'done') marker = { ch: '●', color: '#22c55e' };
            else if (status === 'missed') marker = { ch: '✗', color: '#ef4444' };
            else if (status === 'upcoming') marker = { ch: '○', color: '#64748b' };

            return (
              <div
                key={w.id}
                onClick={() => navigate('/plan/workout/' + w.id)}
                style={{
                  display: 'flex', gap: '10px', backgroundColor: '#16213e', borderRadius: '8px',
                  padding: '12px', borderLeft: `3px solid ${dc}`, cursor: 'pointer',
                }}
              >
                <div style={{
                  width: '18px', flex: '0 0 18px', textAlign: 'center', fontSize: '15px',
                  color: marker ? marker.color : 'transparent', lineHeight: '20px',
                }}>
                  {marker ? marker.ch : ''}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', marginBottom: '6px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '11px', color: '#a0a0b0' }}>{formatDate(w.date)}</span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      {!isSprint && w.garmin_workout_id != null && (
                        <span style={{ fontSize: '10px', fontWeight: 600, color: '#22c55e' }}>
                          ⌚ on watch
                        </span>
                      )}
                      <span style={{
                        color: dc, backgroundColor: `${dc}22`, padding: '2px 7px', borderRadius: '4px',
                        fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px',
                      }}>
                        {dtLabel}
                      </span>
                    </span>
                  </div>
                  <div style={{ fontSize: '14px', fontWeight: 600, color: '#fff', marginBottom: '6px' }}>
                    {w.title || 'Workout'}
                  </div>
                  {isSprint ? (
                    <div style={{ marginBottom: w.description ? '8px' : 0 }}>
                      <SprintSummary structure={w.structure} dc={dc} />
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', fontSize: '12px', color: '#e0e0e0', marginBottom: w.description ? '8px' : 0 }}>
                      {km != null && <span>{km} km</span>}
                      {hasPace && <span>{formatPace(w.pace_low_sec)}–{formatPace(w.pace_high_sec)}/km</span>}
                      {w.hr_ceiling != null && <span>≤{w.hr_ceiling} bpm</span>}
                    </div>
                  )}
                  {w.description && (
                    <div style={{ fontSize: '11px', color: '#777', lineHeight: 1.4 }}>{w.description}</div>
                  )}
                  {isSprint && status === 'done' && actual && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px', marginTop: '8px' }}>
                      <span style={{ fontSize: '12px', color: '#c8c8d4' }}>
                        Best 100m: {actual.best_100m_sec != null ? `${actual.best_100m_sec}s` : '—'}
                        {actual.fade_pct != null ? ` · fade ${actual.fade_pct}%` : ''}
                        {actual.fastest_rep_sec != null ? ` · fastest ${actual.fastest_rep_sec}s` : ''}
                      </span>
                    </div>
                  )}
                  {!isSprint && status === 'done' && actual && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px', marginTop: '8px' }}>
                      <span style={{ fontSize: '12px', color: '#c8c8d4' }}>
                        Ran: {actual.pace_sec != null ? `${formatPace(actual.pace_sec)}/km` : '—'}
                        {actual.avg_hr != null ? ` · ${Math.round(actual.avg_hr)} bpm` : ''}
                      </span>
                      {w.compliance === 'ran_hard' && (
                        <span style={{
                          fontSize: '10px', fontWeight: 700, color: '#ef4444', backgroundColor: '#ef444422',
                          padding: '2px 7px', borderRadius: '4px',
                        }}>
                          ran hard
                        </span>
                      )}
                      {w.compliance === 'on_target' && (
                        <span style={{
                          fontSize: '10px', fontWeight: 700, color: '#22c55e', backgroundColor: '#22c55e22',
                          padding: '2px 7px', borderRadius: '4px',
                        }}>
                          ✓ easy
                        </span>
                      )}
                    </div>
                  )}
                  {!isRest && !isEditing && (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '8px' }}>
                      <button
                        onClick={(e) => { e.stopPropagation(); startEditMove(w); }}
                        style={{
                          background: 'none', border: 'none', color: '#a0a0b0',
                          fontSize: '12px', fontWeight: 600, cursor: 'pointer', padding: '4px 0',
                        }}
                      >
                        Move
                      </button>
                    </div>
                  )}
                  {!isRest && isEditing && (
                    <div style={{ marginTop: '8px' }} onClick={(e) => e.stopPropagation()}>
                      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
                        <input
                          type="date"
                          value={editDate}
                          onChange={(e) => setEditDate(e.target.value)}
                          style={{
                            colorScheme: 'dark', backgroundColor: '#0f0f1a', color: '#e0e0e0',
                            border: '1px solid #2a2a45', borderRadius: '6px', padding: '8px 10px',
                            fontSize: '16px', minHeight: '44px', flex: '1 1 140px', minWidth: 0,
                          }}
                        />
                        <button
                          onClick={() => saveMove(w.id)}
                          disabled={moving || !editDate}
                          style={{
                            backgroundColor: '#fc5200', border: 'none', borderRadius: '6px', color: '#fff',
                            fontSize: '12px', fontWeight: 700, padding: '8px 16px', minHeight: '44px',
                            cursor: moving || !editDate ? 'default' : 'pointer', opacity: moving || !editDate ? 0.5 : 1,
                          }}
                        >
                          {moving ? 'Saving…' : 'Save'}
                        </button>
                        <button
                          onClick={cancelEditMove}
                          disabled={moving}
                          style={{
                            backgroundColor: 'transparent', border: '1px solid #333', borderRadius: '6px', color: '#a0a0b0',
                            fontSize: '12px', fontWeight: 600, padding: '8px 16px', minHeight: '44px',
                            cursor: moving ? 'default' : 'pointer',
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                      {moveError && (
                        <div style={{ fontSize: '11px', color: '#ef4444', marginTop: '6px' }}>{moveError}</div>
                      )}
                    </div>
                  )}
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

  const sprintProfile = sprintProjections ? sprintProjections.profile : null;
  const sprintHorizons = (sprintProjections && sprintProjections.horizons) || [];
  const hasSprintBaseline = sprintProfile && sprintProfile.best_100m_sec != null;

  const segBtn = (active) => ({
    flex: 1, minHeight: 44, borderRadius: '8px', border: '1px solid ' + (active ? '#fc5200' : '#2a2a45'),
    backgroundColor: active ? '#fc5200' : '#16213e', color: active ? '#fff' : '#a0a0b0',
    fontSize: '14px', fontWeight: 700, cursor: 'pointer',
  });

  const horizonBtnStyle = (isBuilding) => ({
    textAlign: 'left', backgroundColor: '#16213e', border: '1px solid #333',
    borderRadius: '8px', padding: '14px', cursor: building != null ? 'default' : 'pointer',
    color: 'inherit', minHeight: '44px', opacity: building != null && !isBuilding ? 0.5 : 1,
  });

  return (
    <div style={cardStyle}>
      {/* Goal-type toggle */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '18px' }}>
        <button onClick={() => selectGoal('5k')} style={segBtn(goalType === '5k')}>5K</button>
        <button onClick={() => selectGoal('sprint')} style={segBtn(goalType === 'sprint')}>100m Sprint</button>
      </div>

      {goalType === '5k' ? (
        <>
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
                    style={horizonBtnStyle(isBuilding)}
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
        </>
      ) : (
        <>
          <h2 style={sectionTitle}>Build your 100m sprint plan</h2>

          {sprintLoading && !sprintProjections ? (
            <div style={{ fontSize: '13px', color: '#a0a0b0' }}>Loading sprint profile…</div>
          ) : (
            <>
              {/* Baseline card */}
              <div style={{ marginBottom: '18px' }}>
                {hasSprintBaseline ? (
                  <>
                    <div style={{ fontSize: '14px', color: '#e0e0e0' }}>
                      Best 100m: <span style={{ fontSize: '28px', fontWeight: 800, color: '#fc5200' }}>{sprintProfile.best_100m_sec}s</span>
                      {sprintProfile.best_100m_date && (
                        <span style={{ marginLeft: '8px', color: '#666', fontSize: '11px' }}>
                          {formatDate(sprintProfile.best_100m_date)}
                        </span>
                      )}
                    </div>
                    {sprintProfile.diagnosis_detail && (
                      <div style={{ fontSize: '12.5px', color: '#a0a0b0', marginTop: '6px', lineHeight: 1.45 }}>
                        {sprintProfile.diagnosis_detail}
                      </div>
                    )}
                    {Array.isArray(sprintProfile.supporting_efforts) && sprintProfile.supporting_efforts.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '10px' }}>
                        {sprintProfile.supporting_efforts.map((e, i) => (
                          <span key={i} style={{
                            fontSize: '11px', color: '#c8c8d4', backgroundColor: '#16213e',
                            borderRadius: '20px', padding: '4px 10px',
                          }}>
                            {e.distance_m}m {e.time_sec}s
                          </span>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ fontSize: '13px', color: '#a0a0b0' }}>
                    Not enough sprint data — pick a horizon and we'll estimate.
                  </div>
                )}
              </div>

              {/* Horizon cards */}
              {sprintHorizons.length > 0 ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '10px' }}>
                  {sprintHorizons.map((h) => {
                    const isBuilding = building === h.weeks;
                    return (
                      <button
                        key={h.weeks}
                        onClick={() => buildSprintPlan(h.weeks, h.target_100m_sec)}
                        disabled={building != null}
                        style={horizonBtnStyle(isBuilding)}
                      >
                        {isBuilding ? (
                          <div style={{ fontSize: '13px', color: '#fc5200', fontWeight: 600 }}>Building your plan…</div>
                        ) : (
                          <>
                            <div style={{ fontSize: '14px', color: '#e0e0e0', marginBottom: '4px' }}>
                              {h.weeks} weeks → <span style={{ fontSize: '18px', fontWeight: 700, color: '#fc5200' }}>{h.target_100m_sec != null ? `${h.target_100m_sec}s` : '—'}</span>
                            </div>
                            {h.improvement_pct != null && (
                              <div style={{ fontSize: '11px', color: '#666' }}>{h.improvement_pct}%</div>
                            )}
                          </>
                        )}
                      </button>
                    );
                  })}
                </div>
              ) : (
                !sprintLoading && (
                  <div style={{ fontSize: '13px', color: '#666' }}>No horizons available yet.</div>
                )
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

function Training() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [activeTab, setActiveTab] = useState('plan');
  const [hasActivePlan, setHasActivePlan] = useState(false);
  const [flagsExpanded, setFlagsExpanded] = useState(false);

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

  // Lightweight second fetch so the Fitness tab can make coaching flags plan-aware.
  useEffect(() => {
    api.get('/plan')
      .then((res) => setHasActivePlan(!!res.data.plan))
      .catch(() => setHasActivePlan(false));
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

  const acwr = fm.acwr;

  const warningItems = warnings.map((w, i) => {
    const c = WARN_COLORS[w.level] || WARN_COLORS.info;
    return (
      <div key={i} style={{ backgroundColor: '#16213e', borderLeft: `4px solid ${c}`, borderRadius: '6px', padding: '12px 14px' }}>
        <div style={{ fontSize: '14px', fontWeight: 700, color: '#fff', marginBottom: '4px' }}>{w.title}</div>
        <div style={{ fontSize: '13px', color: '#a0a0b0' }}>{w.detail}</div>
      </div>
    );
  });

  const tabBtn = (active) => ({
    flex: 1, minHeight: 44, borderRadius: '8px', border: '1px solid ' + (active ? '#fc5200' : '#2a2a45'),
    backgroundColor: active ? '#fc5200' : '#16213e', color: active ? '#fff' : '#a0a0b0',
    fontSize: '14px', fontWeight: 700, cursor: 'pointer',
  });

  return (
    <div>
      <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff', marginBottom: '16px' }}>Training</h1>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '20px' }}>
        <button onClick={() => setActiveTab('plan')} style={tabBtn(activeTab === 'plan')}>Plan</button>
        <button onClick={() => setActiveTab('fitness')} style={tabBtn(activeTab === 'fitness')}>Fitness</button>
      </div>

      {activeTab === 'plan' ? (
        <PlanSection />
      ) : (
        <>
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

      {/* b) Coaching flags — plan-aware (collapsed when a plan is active) */}
      <div style={cardStyle}>
        {warnings.length === 0 ? (
          <>
            <h2 style={sectionTitle}>Coaching Flags</h2>
            <div style={{ color: '#22c55e', fontSize: '13px' }}>No flags — nice work.</div>
          </>
        ) : hasActivePlan ? (
          <>
            <button
              onClick={() => setFlagsExpanded((v) => !v)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%',
                background: 'none', border: 'none', padding: 0, cursor: 'pointer', minHeight: 44,
              }}
              aria-expanded={flagsExpanded}
            >
              <span style={{ fontSize: '16px', fontWeight: 700, color: '#f59e0b' }}>
                ⚠ {warnings.length} coaching flag{warnings.length === 1 ? '' : 's'}
              </span>
              <span style={{ fontSize: '13px', color: '#a0a0b0' }}>{flagsExpanded ? '▲' : '▼'}</span>
            </button>
            {flagsExpanded && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', marginTop: '12px' }}>
                {warningItems}
              </div>
            )}
          </>
        ) : (
          <>
            <h2 style={sectionTitle}>Coaching Flags</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {warningItems}
            </div>
          </>
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

        </>
      )}
    </div>
  );
}

export default Training;
