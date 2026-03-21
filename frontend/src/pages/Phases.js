import React, { useState, useEffect } from 'react';
import api from '../api';

function formatPace(secPerKm) {
  if (!secPerKm) return '-';
  const mins = Math.floor(secPerKm / 60);
  const secs = Math.round(secPerKm % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatDate(dateString) {
  if (!dateString) return '-';
  const d = new Date(dateString);
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatDateShort(dateString) {
  if (!dateString) return '-';
  const d = new Date(dateString);
  return d.toLocaleDateString('en-US', { month: 'short', year: '2-digit' });
}

function formatDuration(days) {
  if (days < 7) return `${days}d`;
  if (days < 30) return `${Math.round(days / 7)}w`;
  const months = Math.round(days / 30);
  return `${months}mo`;
}

function formatTime(seconds) {
  if (!seconds) return '-';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (hrs > 0) return `${hrs}h ${mins}m`;
  return `${mins}m`;
}

function getPhaseQuality(phase) {
  // Score based on consistency and volume
  const score = (phase.runs_per_week * 2) + (phase.total_distance_km / 10) + (phase.duration_days / 7);
  if (score > 50) return { label: 'Peak', color: '#4ade80', bg: 'rgba(74, 222, 128, 0.08)' };
  if (score > 20) return { label: 'Strong', color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.08)' };
  if (score > 8) return { label: 'Building', color: '#fc5200', bg: 'rgba(252, 82, 0, 0.08)' };
  if (phase.total_runs <= 2) return { label: 'Restart', color: '#a0a0b0', bg: 'rgba(160, 160, 176, 0.05)' };
  return { label: 'Active', color: '#fbbf24', bg: 'rgba(251, 191, 36, 0.08)' };
}

function Phases() {
  const [phases, setPhases] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [gapDays, setGapDays] = useState(14);

  const loadPhases = (gap) => {
    setLoading(true);
    api.get(`/phases?gap_days=${gap}`)
      .then((res) => {
        setPhases(res.data.phases || []);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to load phases');
        setLoading(false);
      });
  };

  useEffect(() => { loadPhases(gapDays); }, []); // eslint-disable-line

  // Compute overall insights
  const insights = phases.length > 0 ? (() => {
    const sorted = [...phases].sort((a, b) => b.total_distance_km - a.total_distance_km);
    const bestPhase = sorted[0];
    const longestStreak = [...phases].sort((a, b) => b.duration_days - a.duration_days)[0];
    const avgBreak = phases.filter(p => p.break_before_days).reduce((s, p) => s + p.break_before_days, 0) / Math.max(phases.filter(p => p.break_before_days).length, 1);
    const mostConsistent = [...phases].filter(p => p.total_runs > 3).sort((a, b) => b.runs_per_week - a.runs_per_week)[0];
    const totalDistance = phases.reduce((s, p) => s + p.total_distance_km, 0);
    const totalRuns = phases.reduce((s, p) => s + p.total_runs, 0);
    return { bestPhase, longestStreak, avgBreak: Math.round(avgBreak), mostConsistent, totalDistance, totalRuns };
  })() : null;

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '60px', color: '#a0a0b0' }}>Analyzing your running phases...</div>;
  }

  if (error) {
    return <div style={{ padding: '16px', backgroundColor: '#3d1515', border: '1px solid #6b2020', borderRadius: '8px', color: '#ff6b6b' }}>{error}</div>;
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px', flexWrap: 'wrap', gap: '12px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff' }}>Running Phases</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '13px', color: '#a0a0b0' }}>Break threshold:</span>
          <select value={gapDays} onChange={(e) => { setGapDays(Number(e.target.value)); loadPhases(Number(e.target.value)); }}
            style={{ padding: '6px 10px', borderRadius: '6px', border: '1px solid #333', backgroundColor: '#16213e', color: '#e0e0e0', fontSize: '13px' }}>
            <option value={7}>7 days</option>
            <option value={14}>14 days</option>
            <option value={21}>21 days</option>
            <option value={30}>30 days</option>
          </select>
        </div>
      </div>

      <p style={{ fontSize: '14px', color: '#a0a0b0', marginBottom: '24px' }}>
        Detected {phases.length} distinct running phases based on gaps of {gapDays}+ days between runs.
      </p>

      {/* Insights */}
      {insights && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '12px', marginBottom: '28px' }}>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Total Phases</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{phases.length}</div>
          </div>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Total Runs</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{insights.totalRuns}</div>
          </div>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Total Distance</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{insights.totalDistance.toFixed(0)}</div>
            <div style={{ fontSize: '12px', color: '#a0a0b0' }}>km</div>
          </div>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Avg Break</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{insights.avgBreak}</div>
            <div style={{ fontSize: '12px', color: '#a0a0b0' }}>days</div>
          </div>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Longest Streak</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{insights.longestStreak?.duration_days || 0}</div>
            <div style={{ fontSize: '12px', color: '#a0a0b0' }}>days</div>
          </div>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Best Frequency</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{insights.mostConsistent?.runs_per_week || 0}</div>
            <div style={{ fontSize: '12px', color: '#a0a0b0' }}>runs/week</div>
          </div>
        </div>
      )}

      {/* Timeline visualization */}
      <div style={{ marginBottom: '28px' }}>
        <h2 style={{ fontSize: '16px', fontWeight: 600, color: '#fff', marginBottom: '12px' }}>Timeline</h2>
        <div style={{ display: 'flex', gap: '2px', alignItems: 'flex-end', height: '80px', overflowX: 'auto', WebkitOverflowScrolling: 'touch', paddingBottom: '4px' }}>
          {phases.map((phase, i) => {
            const quality = getPhaseQuality(phase);
            const maxRuns = Math.max(...phases.map(p => p.total_runs));
            const barHeight = Math.max(8, (phase.total_runs / maxRuns) * 70);
            return (
              <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px' }}>
                {phase.break_before_days && (
                  <div style={{ width: Math.max(4, Math.min(phase.break_before_days / 5, 20)), height: '4px', backgroundColor: '#252540', borderRadius: '2px' }} />
                )}
                <div title={`Phase ${phase.phase_number}: ${phase.total_runs} runs, ${phase.total_distance_km} km`}
                  style={{ width: Math.max(12, Math.min(phase.duration_days / 2, 40)), height: `${barHeight}px`, backgroundColor: quality.color, borderRadius: '3px 3px 0 0', opacity: 0.8, cursor: 'pointer', minWidth: '12px' }} />
                <div style={{ fontSize: '8px', color: '#666', whiteSpace: 'nowrap' }}>{formatDateShort(phase.start_date)}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Phase cards */}
      <h2 style={{ fontSize: '16px', fontWeight: 600, color: '#fff', marginBottom: '12px' }}>Phase Details</h2>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {[...phases].reverse().map((phase) => {
          const quality = getPhaseQuality(phase);
          return (
            <div key={phase.phase_number} style={{ backgroundColor: quality.bg, border: `1px solid ${quality.color}22`, borderRadius: '8px', padding: '16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '8px', marginBottom: '12px' }}>
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <span style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>Phase {phase.phase_number}</span>
                    <span style={{ fontSize: '11px', fontWeight: 600, color: quality.color, backgroundColor: `${quality.color}15`, padding: '2px 8px', borderRadius: '10px' }}>{quality.label}</span>
                  </div>
                  <div style={{ fontSize: '13px', color: '#a0a0b0', marginTop: '2px' }}>
                    {formatDate(phase.start_date)} — {formatDate(phase.end_date)}
                    <span style={{ marginLeft: '8px', color: '#666' }}>({formatDuration(phase.duration_days)})</span>
                  </div>
                </div>
                {phase.break_before_days && (
                  <div style={{ fontSize: '12px', color: '#ff6b6b', backgroundColor: 'rgba(255,107,107,0.08)', padding: '4px 10px', borderRadius: '6px' }}>
                    {phase.break_before_days}d break before
                  </div>
                )}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))', gap: '8px' }}>
                <div>
                  <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Runs</div>
                  <div style={{ fontSize: '18px', fontWeight: 700, color: '#fff' }}>{phase.total_runs}</div>
                </div>
                <div>
                  <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Distance</div>
                  <div style={{ fontSize: '18px', fontWeight: 700, color: '#fff' }}>{phase.total_distance_km} <span style={{ fontSize: '12px', color: '#666' }}>km</span></div>
                </div>
                <div>
                  <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Avg Pace</div>
                  <div style={{ fontSize: '18px', fontWeight: 700, color: '#fff' }}>{formatPace(phase.avg_pace_sec_per_km)} <span style={{ fontSize: '12px', color: '#666' }}>/km</span></div>
                </div>
                <div>
                  <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Best Pace</div>
                  <div style={{ fontSize: '18px', fontWeight: 700, color: '#4ade80' }}>{formatPace(phase.best_pace_sec_per_km)} <span style={{ fontSize: '12px', color: '#666' }}>/km</span></div>
                </div>
                <div>
                  <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Frequency</div>
                  <div style={{ fontSize: '18px', fontWeight: 700, color: '#fff' }}>{phase.runs_per_week} <span style={{ fontSize: '12px', color: '#666' }}>/wk</span></div>
                </div>
                <div>
                  <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Elevation</div>
                  <div style={{ fontSize: '18px', fontWeight: 700, color: '#fff' }}>{Math.round(phase.total_elevation_m)} <span style={{ fontSize: '12px', color: '#666' }}>m</span></div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default Phases;
