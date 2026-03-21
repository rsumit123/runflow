import React, { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';

function formatTime(seconds) {
  if (!seconds && seconds !== 0) return '-';
  const s = Math.round(seconds);
  const mins = Math.floor(s / 60);
  const secs = s % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatDate(dateString) {
  if (!dateString) return '-';
  const d = new Date(dateString);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatDistanceLabel(meters) {
  const m = parseInt(meters, 10);
  if (m >= 1000) return `${m / 1000}km`;
  return `${m}m`;
}

const DISTANCES = [100, 200, 400, 500, 1000, 2000];

const cardStyle = {
  backgroundColor: '#1a1a2e',
  borderRadius: '8px',
  padding: '16px',
  marginBottom: '16px',
  position: 'relative',
};

const sectionTitle = {
  fontSize: '18px',
  fontWeight: 600,
  color: '#fff',
  marginBottom: '16px',
};

const btnBase = {
  border: 'none',
  borderRadius: '6px',
  padding: '10px 18px',
  cursor: 'pointer',
  fontSize: '14px',
  fontWeight: 600,
  transition: 'opacity 0.2s',
};

const accentBtn = {
  ...btnBase,
  backgroundColor: '#fc5200',
  color: '#fff',
};

const outlineBtn = {
  ...btnBase,
  backgroundColor: 'transparent',
  border: '1px solid #333',
  color: '#a0a0b0',
};

const selectedBtn = {
  ...btnBase,
  backgroundColor: '#fc5200',
  color: '#fff',
  border: '1px solid #fc5200',
};

const unselectedBtn = {
  ...btnBase,
  backgroundColor: '#16213e',
  color: '#a0a0b0',
  border: '1px solid #333',
};

function ProgressBar({ current, target, achieved }) {
  const pct = target > 0 ? Math.min(100, Math.max(0, (current / target) * 100)) : 0;
  return (
    <div style={{ width: '100%', height: '8px', backgroundColor: '#0f0f1a', borderRadius: '4px', overflow: 'hidden', marginTop: '8px' }}>
      <div
        style={{
          width: `${pct}%`,
          height: '100%',
          backgroundColor: achieved ? '#4ade80' : '#fc5200',
          borderRadius: '4px',
          transition: 'width 0.4s ease',
        }}
      />
    </div>
  );
}

function GoalCard({ goal, onDelete }) {
  const { goal_type, progress } = goal;
  const achieved = progress?.achieved;

  const renderContent = () => {
    if (goal_type === 'speed') {
      const currentBest = progress?.current_best;
      const target = progress?.target;
      const gap = progress?.gap;
      const pctVal = target > 0 ? Math.max(0, (1 - (gap || 0) / target)) * 100 : 0;

      return (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ color: '#a0a0b0', fontSize: '13px' }}>
              {formatDistanceLabel(goal.distance_target)}
              <span style={{
                marginLeft: '8px', fontSize: '10px', fontWeight: 600, padding: '1px 6px', borderRadius: '4px',
                backgroundColor: (goal.mode || progress?.mode) === 'sprint' ? '#4ade8018' : '#fc520018',
                color: (goal.mode || progress?.mode) === 'sprint' ? '#4ade80' : '#fc5200',
              }}>
                {(goal.mode || progress?.mode) === 'sprint' ? 'SPRINT' : 'ANY RUN'}
              </span>
            </span>
            {achieved && (
              <span style={{ backgroundColor: '#4ade80', color: '#0f0f1a', fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px' }}>
                ACHIEVED
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', marginTop: '8px' }}>
            <div>
              <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>Best ({(goal.mode || progress?.mode) === 'sprint' ? 'sprint' : 'any'})</div>
              <div style={{ color: '#fff', fontSize: '16px', fontWeight: 600 }}>
                {currentBest != null ? formatTime(currentBest) : '-'}
              </div>
            </div>
            <div>
              <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>Target</div>
              <div style={{ color: '#fff', fontSize: '16px', fontWeight: 600 }}>{formatTime(target)}</div>
            </div>
            {gap != null && !achieved && (
              <div>
                <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>Gap</div>
                <div style={{ color: '#fc5200', fontSize: '16px', fontWeight: 600 }}>+{gap.toFixed(1)}s</div>
              </div>
            )}
            {progress?.other_mode_best && (
              <div>
                <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>
                  {(goal.mode || progress?.mode) === 'sprint' ? 'In-run best' : 'Sprint best'}
                </div>
                <div style={{ color: '#a0a0b0', fontSize: '16px', fontWeight: 600 }}>{formatTime(progress.other_mode_best)}</div>
              </div>
            )}
          </div>
          <ProgressBar current={Math.max(0, pctVal)} target={100} achieved={achieved} />
        </>
      );
    }

    if (goal_type === 'consistency') {
      const current = progress?.current_best || 0;
      const target = progress?.target || goal.weekly_runs_target || 0;
      return (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ color: '#a0a0b0', fontSize: '13px' }}>Consistency Goal</span>
            {achieved && (
              <span style={{ backgroundColor: '#4ade80', color: '#0f0f1a', fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px' }}>
                ACHIEVED
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: '16px', marginTop: '8px' }}>
            <div>
              <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>This Week</div>
              <div style={{ color: '#fff', fontSize: '16px', fontWeight: 600 }}>{current} runs</div>
            </div>
            <div>
              <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>Target</div>
              <div style={{ color: '#fff', fontSize: '16px', fontWeight: 600 }}>{target} runs</div>
            </div>
          </div>
          <ProgressBar current={current} target={target} achieved={achieved} />
        </>
      );
    }

    if (goal_type === 'volume') {
      const current = progress?.current_best || 0;
      const target = progress?.target || goal.weekly_km_target || 0;
      return (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
            <span style={{ color: '#a0a0b0', fontSize: '13px' }}>Volume Goal</span>
            {achieved && (
              <span style={{ backgroundColor: '#4ade80', color: '#0f0f1a', fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '10px' }}>
                ACHIEVED
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: '16px', marginTop: '8px' }}>
            <div>
              <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>This Week</div>
              <div style={{ color: '#fff', fontSize: '16px', fontWeight: 600 }}>{typeof current === 'number' ? current.toFixed(1) : current} km</div>
            </div>
            <div>
              <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>Target</div>
              <div style={{ color: '#fff', fontSize: '16px', fontWeight: 600 }}>{typeof target === 'number' ? target.toFixed(1) : target} km</div>
            </div>
          </div>
          <ProgressBar current={current} target={target} achieved={achieved} />
        </>
      );
    }

    return null;
  };

  return (
    <div style={cardStyle}>
      <button
        onClick={() => onDelete(goal.id)}
        style={{
          position: 'absolute',
          top: '8px',
          right: '8px',
          background: 'none',
          border: 'none',
          color: '#666',
          cursor: 'pointer',
          fontSize: '16px',
          padding: '4px 8px',
          borderRadius: '4px',
          lineHeight: 1,
        }}
        title="Delete goal"
      >
        &times;
      </button>
      {renderContent()}
    </div>
  );
}

function EffortTimeline({ efforts }) {
  if (!efforts || efforts.length === 0) return null;
  const recent = efforts.slice(0, 5);
  const times = recent.map((e) => e.time_seconds);
  const minT = Math.min(...times);
  const maxT = Math.max(...times);
  const range = maxT - minT || 1;

  return (
    <div style={{ marginTop: '12px' }}>
      <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase', marginBottom: '8px' }}>Recent Efforts</div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: '8px', height: '48px' }}>
        {recent.map((e, i) => {
          const normalized = 1 - (e.time_seconds - minT) / range;
          const dotBottom = 4 + normalized * 32;
          return (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1, position: 'relative', height: '100%' }}>
              <div
                style={{
                  position: 'absolute',
                  bottom: `${dotBottom}px`,
                  width: '10px',
                  height: '10px',
                  borderRadius: '50%',
                  backgroundColor: i === 0 ? '#fc5200' : '#4a4a6a',
                }}
                title={`${formatTime(e.time_seconds)} on ${formatDate(e.date)}`}
              />
              <div style={{ position: 'absolute', bottom: '0', fontSize: '9px', color: '#666', whiteSpace: 'nowrap' }}>
                {formatDate(e.date)}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '4px', fontSize: '10px', color: '#555' }}>
        <span>{formatTime(maxT)}</span>
        <span>{formatTime(minT)}</span>
      </div>
    </div>
  );
}

function AddGoalFlow({ onGoalCreated }) {
  const [step, setStep] = useState(1);
  const [goalType, setGoalType] = useState(null);
  const [distance, setDistance] = useState(null);
  const [recommendation, setRecommendation] = useState(null);
  const [targetSeconds, setTargetSeconds] = useState(null);
  const [targetRuns, setTargetRuns] = useState(null);
  const [targetKm, setTargetKm] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [goalMode, setGoalMode] = useState('any'); // 'sprint' or 'any'

  const reset = () => {
    setStep(1);
    setGoalType(null);
    setDistance(null);
    setRecommendation(null);
    setTargetSeconds(null);
    setTargetRuns(null);
    setTargetKm(null);
    setGoalMode('any');
    setError(null);
  };

  const selectType = (type) => {
    setGoalType(type);
    setError(null);
    if (type === 'speed') {
      setStep(2);
    } else {
      fetchRecommendation(type, null);
    }
  };

  const selectDistance = (dist) => {
    setDistance(dist);
    fetchRecommendation('speed', dist);
  };

  const fetchRecommendation = async (type, dist) => {
    setLoading(true);
    setError(null);
    try {
      let res;
      if (type === 'speed') {
        res = await api.get(`/goals/recommend/speed/${dist}`);
        setTargetSeconds(res.data.recommended_target);
      } else if (type === 'consistency') {
        res = await api.get('/goals/recommend/consistency');
        setTargetRuns(res.data.recommended_target);
      } else if (type === 'volume') {
        res = await api.get('/goals/recommend/volume');
        setTargetKm(res.data.recommended_target);
      }
      setRecommendation(res.data);
      setStep(3);
    } catch (err) {
      setError('Failed to load recommendation. Try again.');
    }
    setLoading(false);
  };

  const saveGoal = async () => {
    setSaving(true);
    setError(null);
    try {
      const body = { goal_type: goalType };
      if (goalType === 'speed') {
        body.distance_target = distance;
        body.time_target = targetSeconds;
        body.mode = goalMode;
      } else if (goalType === 'consistency') {
        body.weekly_runs_target = targetRuns;
      } else if (goalType === 'volume') {
        body.weekly_km_target = targetKm;
      }
      await api.post('/goals', body);
      onGoalCreated();
      reset();
    } catch (err) {
      setError('Failed to save goal. Try again.');
    }
    setSaving(false);
  };

  const adjustTarget = (delta) => {
    if (goalType === 'speed') {
      setTargetSeconds((prev) => Math.max(1, (prev || 0) + delta));
    } else if (goalType === 'consistency') {
      setTargetRuns((prev) => Math.max(1, (prev || 0) + delta));
    } else if (goalType === 'volume') {
      setTargetKm((prev) => Math.max(0.5, Math.round(((prev || 0) + delta) * 10) / 10));
    }
  };

  const renderTargetInput = () => {
    if (goalType === 'speed') {
      const mins = Math.floor((targetSeconds || 0) / 60);
      const secs = Math.round((targetSeconds || 0) % 60);
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '12px' }}>
          <button onClick={() => adjustTarget(1)} style={unselectedBtn}>+1s (slower)</button>
          <div style={{ textAlign: 'center' }}>
            <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>Target</div>
            <input
              type="text"
              value={`${mins}:${secs.toString().padStart(2, '0')}`}
              onChange={(e) => {
                const parts = e.target.value.split(':');
                if (parts.length === 2) {
                  const m = parseInt(parts[0], 10) || 0;
                  const s = parseInt(parts[1], 10) || 0;
                  setTargetSeconds(m * 60 + s);
                }
              }}
              style={{
                backgroundColor: '#0f0f1a',
                border: '1px solid #333',
                borderRadius: '6px',
                color: '#fff',
                fontSize: '20px',
                fontWeight: 700,
                textAlign: 'center',
                width: '100px',
                padding: '6px',
              }}
            />
          </div>
          <button onClick={() => adjustTarget(-1)} style={unselectedBtn}>-1s (faster)</button>
        </div>
      );
    }

    if (goalType === 'consistency') {
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '12px' }}>
          <button onClick={() => adjustTarget(-1)} style={unselectedBtn}>-</button>
          <div style={{ textAlign: 'center' }}>
            <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>Runs / Week</div>
            <div style={{ color: '#fff', fontSize: '24px', fontWeight: 700 }}>{targetRuns}</div>
          </div>
          <button onClick={() => adjustTarget(1)} style={unselectedBtn}>+</button>
        </div>
      );
    }

    if (goalType === 'volume') {
      return (
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginTop: '12px' }}>
          <button onClick={() => adjustTarget(-1)} style={unselectedBtn}>-1 km</button>
          <div style={{ textAlign: 'center' }}>
            <div style={{ color: '#666', fontSize: '11px', textTransform: 'uppercase' }}>KM / Week</div>
            <div style={{ color: '#fff', fontSize: '24px', fontWeight: 700 }}>{targetKm}</div>
          </div>
          <button onClick={() => adjustTarget(1)} style={unselectedBtn}>+1 km</button>
        </div>
      );
    }

    return null;
  };

  return (
    <div style={cardStyle}>
      <div style={sectionTitle}>Add New Goal</div>

      {/* Step 1: Pick Type */}
      {step >= 1 && (
        <div style={{ marginBottom: '16px' }}>
          <div style={{ color: '#666', fontSize: '12px', marginBottom: '8px' }}>Goal Type</div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {['speed', 'consistency', 'volume'].map((t) => (
              <button
                key={t}
                onClick={() => selectType(t)}
                style={goalType === t ? selectedBtn : unselectedBtn}
              >
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Step 2: Pick Distance (speed only) */}
      {step >= 2 && goalType === 'speed' && (
        <div style={{ marginBottom: '16px' }}>
          <div style={{ color: '#666', fontSize: '12px', marginBottom: '8px' }}>Distance</div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {DISTANCES.map((d) => (
              <button
                key={d}
                onClick={() => selectDistance(d)}
                style={distance === d ? selectedBtn : unselectedBtn}
              >
                {formatDistanceLabel(d)}
              </button>
            ))}
          </div>
        </div>
      )}

      {loading && (
        <div style={{ color: '#a0a0b0', padding: '20px 0', textAlign: 'center' }}>Loading recommendation...</div>
      )}

      {error && (
        <div style={{ color: '#ef4444', fontSize: '13px', padding: '8px 0' }}>{error}</div>
      )}

      {/* Step 3: Show Recommendation */}
      {step === 3 && recommendation && !loading && (
        <div>
          {goalType === 'speed' && (
            <div style={{ backgroundColor: '#0f0f1a', borderRadius: '6px', padding: '14px', marginBottom: '12px' }}>
              {/* Mode toggle */}
              <div style={{ display: 'flex', gap: '6px', marginBottom: '12px' }}>
                <button onClick={() => {
                  setGoalMode('any');
                  setTargetSeconds(recommendation.recommended_target);
                }} style={{
                  padding: '6px 14px', borderRadius: '4px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', border: 'none',
                  backgroundColor: goalMode === 'any' ? '#fc5200' : '#16213e', color: goalMode === 'any' ? '#fff' : '#a0a0b0',
                }}>
                  Any Run
                </button>
                <button onClick={() => {
                  setGoalMode('sprint');
                  setTargetSeconds(recommendation.sprint_recommended || recommendation.recommended_target);
                }} style={{
                  padding: '6px 14px', borderRadius: '4px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', border: 'none',
                  backgroundColor: goalMode === 'sprint' ? '#4ade80' : '#16213e', color: goalMode === 'sprint' ? '#0a2a0a' : '#a0a0b0',
                }}>
                  Sprint Only
                </button>
              </div>
              <div style={{ fontSize: '11px', color: '#666', marginBottom: '10px' }}>
                {goalMode === 'sprint'
                  ? 'Only counts dedicated short runs (total distance < 2x target)'
                  : 'Counts best segment from any run length'}
              </div>

              <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13px', color: '#a0a0b0', lineHeight: 1.8 }}>
                <span>
                  Best ({goalMode === 'sprint' ? 'sprint' : 'any'}): <strong style={{ color: '#fff' }}>
                    {formatTime(goalMode === 'sprint' ? recommendation.sprint_best : recommendation.all_time_best)}
                  </strong>
                  {goalMode === 'sprint' && !recommendation.sprint_best && <span style={{ color: '#666' }}> (no sprint runs)</span>}
                </span>
                <span>
                  Phase best: <strong style={{ color: '#fff' }}>{formatTime(recommendation.current_phase_best)}</strong>
                </span>
                {recommendation.trend_direction && (
                  <span>
                    Trend: <strong style={{ color: recommendation.trend_direction === 'improving' ? '#4ade80' : '#fc5200' }}>
                      {recommendation.trend_direction}
                    </strong>
                  </span>
                )}
              </div>
              <div style={{ marginTop: '6px', fontSize: '13px', color: '#a0a0b0' }}>
                Recommended: <strong style={{ color: '#fc5200' }}>
                  {formatTime(goalMode === 'sprint' ? (recommendation.sprint_recommended || recommendation.recommended_target) : recommendation.recommended_target)}
                </strong>
              </div>
              <div style={{ fontSize: '11px', color: '#555', marginTop: '4px' }}>
                {recommendation.total_efforts} total efforts ({recommendation.sprint_count || 0} sprint)
              </div>
              <EffortTimeline efforts={recommendation.recent_efforts} />
            </div>
          )}

          {goalType === 'consistency' && (
            <div style={{ backgroundColor: '#0f0f1a', borderRadius: '6px', padding: '14px', marginBottom: '12px' }}>
              <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13px', color: '#a0a0b0', lineHeight: 1.8 }}>
                <span>
                  Avg runs/week: <strong style={{ color: '#fff' }}>{recommendation.current_avg_per_week?.toFixed(1)}</strong>
                </span>
                <span>
                  Best week: <strong style={{ color: '#fff' }}>{recommendation.best_week} runs</strong>
                </span>
                <span>
                  Recommended: <strong style={{ color: '#fc5200' }}>{recommendation.recommended_target} runs/week</strong>
                </span>
              </div>
            </div>
          )}

          {goalType === 'volume' && (
            <div style={{ backgroundColor: '#0f0f1a', borderRadius: '6px', padding: '14px', marginBottom: '12px' }}>
              <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13px', color: '#a0a0b0', lineHeight: 1.8 }}>
                <span>
                  Avg km/week: <strong style={{ color: '#fff' }}>{recommendation.current_avg_km_per_week?.toFixed(1)}</strong>
                </span>
                <span>
                  Best week: <strong style={{ color: '#fff' }}>{recommendation.best_week_km?.toFixed(1)} km</strong>
                </span>
                <span>
                  Recommended: <strong style={{ color: '#fc5200' }}>{recommendation.recommended_target} km/week</strong>
                </span>
              </div>
            </div>
          )}

          {renderTargetInput()}

          <div style={{ display: 'flex', gap: '8px', marginTop: '16px' }}>
            <button onClick={saveGoal} disabled={saving} style={{ ...accentBtn, opacity: saving ? 0.6 : 1 }}>
              {saving ? 'Saving...' : 'Set Goal'}
            </button>
            <button onClick={reset} style={outlineBtn}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Goals() {
  const [goals, setGoals] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchGoals = useCallback(async () => {
    try {
      const res = await api.get('/goals');
      setGoals(res.data.goals || []);
    } catch (err) {
      // silent
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchGoals();
  }, [fetchGoals]);

  const deleteGoal = async (id) => {
    try {
      await api.delete(`/goals/${id}`);
      setGoals((prev) => prev.filter((g) => g.id !== id));
    } catch (err) {
      // silent
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '60px', color: '#a0a0b0' }}>
        Loading goals...
      </div>
    );
  }

  return (
    <div style={{ backgroundColor: '#0f0f1a', minHeight: '100vh', padding: '20px', maxWidth: '700px', margin: '0 auto' }}>
      <h1 style={{ color: '#fff', fontSize: '24px', fontWeight: 700, marginBottom: '24px' }}>Goals</h1>

      {/* Active Goals */}
      <div style={{ marginBottom: '32px' }}>
        <div style={sectionTitle}>Active Goals</div>
        {goals.length === 0 ? (
          <div style={{ ...cardStyle, color: '#666', textAlign: 'center', padding: '32px' }}>
            No goals yet. Add one below to get started.
          </div>
        ) : (
          goals.map((goal) => (
            <GoalCard key={goal.id} goal={goal} onDelete={deleteGoal} />
          ))
        )}
      </div>

      {/* Add New Goal */}
      <AddGoalFlow onGoalCreated={fetchGoals} />
    </div>
  );
}

export default Goals;
