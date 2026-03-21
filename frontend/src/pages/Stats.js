import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, CartesianGrid, ComposedChart, Area,
} from 'recharts';
import api from '../api';

function formatPace(secPerKm) {
  if (!secPerKm) return '-';
  const mins = Math.floor(secPerKm / 60);
  const secs = Math.round(secPerKm % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatTime(seconds) {
  if (!seconds) return '-';
  const mins = Math.floor(seconds / 60);
  const secs = Math.round(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatDate(dateString) {
  if (!dateString) return '-';
  const d = new Date(dateString);
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatDistanceLabel(meters) {
  const m = parseInt(meters, 10);
  if (m >= 1000) return `${m / 1000}km`;
  return `${m}m`;
}

const cardStyle = { backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', marginBottom: '20px', overflow: 'hidden' };
const chartWrap = { width: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' };
const chartInner = (minW) => ({ minWidth: minW ? `${minW}px` : undefined, width: '100%' });
const sectionTitle = { fontSize: '18px', fontWeight: 600, color: '#fff', marginBottom: '16px' };

const CustomTooltip = ({ active, payload, label, formatter }) => {
  if (!active || !payload || !payload.length) return null;
  return (
    <div style={{ backgroundColor: '#16213e', border: '1px solid #333', borderRadius: '6px', padding: '10px 14px', fontSize: '13px' }}>
      <div style={{ color: '#a0a0b0', marginBottom: '4px' }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color || '#fc5200' }}>
          {p.name}: {formatter ? formatter(p.value, p.name) : p.value}
        </div>
      ))}
    </div>
  );
};

function Stats() {
  const [monthly, setMonthly] = useState([]);
  const [phases, setPhases] = useState([]);
  const [prs, setPrs] = useState(null);
  const [heatmap, setHeatmap] = useState([]);
  const [bestEfforts, setBestEfforts] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.get('/stats/monthly'),
      api.get('/phases?gap_days=14'),
      api.get('/stats/personal-records'),
      api.get('/stats/heatmap'),
      api.get('/best-efforts/records'),
    ]).then(([monthlyRes, phasesRes, prsRes, heatmapRes, bestEffortsRes]) => {
      setMonthly(monthlyRes.data.months || []);
      setPhases(phasesRes.data.phases || []);
      setPrs(prsRes.data);
      setHeatmap(heatmapRes.data.days || []);
      setBestEfforts(bestEffortsRes.data);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '60px', color: '#a0a0b0' }}>Loading stats...</div>;
  }

  // Prepare phase pace data for chart
  const phasePaceData = phases
    .filter(p => p.avg_pace_sec_per_km && p.total_runs > 2)
    .map(p => ({
      name: `P${p.phase_number}`,
      label: `Phase ${p.phase_number}`,
      pace: Math.round(p.avg_pace_sec_per_km),
      paceMin: formatPace(p.avg_pace_sec_per_km),
      bestPace: Math.round(p.best_pace_sec_per_km || 0),
      bestPaceMin: formatPace(p.best_pace_sec_per_km),
      runs: p.total_runs,
      distance: p.total_distance_km,
      start: p.start_date?.substring(0, 7) || '',
    }));

  // Monthly chart data
  const monthlyChart = monthly.map(m => ({
    month: m.month,
    distance: m.distance_km,
    runs: m.runs,
    pace: m.avg_pace_sec_per_km ? Math.round(m.avg_pace_sec_per_km) : null,
    elevation: m.elevation_m,
  }));

  // Heatmap: build 52-week grid
  const heatmapMap = {};
  heatmap.forEach(d => { heatmapMap[d.date] = d; });

  const today = new Date();
  const weeks = [];
  for (let w = 51; w >= 0; w--) {
    const week = [];
    for (let d = 0; d < 7; d++) {
      const date = new Date(today);
      date.setDate(date.getDate() - (w * 7 + (6 - d)));
      const key = date.toISOString().split('T')[0];
      const data = heatmapMap[key];
      week.push({
        date: key,
        distance: data ? data.distance_km : 0,
        runs: data ? data.runs : 0,
      });
    }
    weeks.push(week);
  }

  const prEntries = prs?.personal_records ? Object.entries(prs.personal_records) : [];

  // Best efforts data
  const allTime = bestEfforts?.all_time || {};
  const currentPhase = bestEfforts?.current_phase || {};
  const currentPhaseRuns = bestEfforts?.current_phase_runs || 0;
  const distanceKeys = Object.keys(allTime).sort((a, b) => parseInt(a) - parseInt(b));
  const hasSpeedTracker = distanceKeys.length > 0;

  return (
    <div>
      <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff', marginBottom: '24px' }}>Statistics</h1>

      {/* Speed Tracker - Best Efforts Comparison */}
      {hasSpeedTracker && (
        <div style={cardStyle}>
          <h2 style={sectionTitle}>Speed Tracker</h2>
          <p style={{ fontSize: '13px', color: '#666', marginBottom: '18px' }}>
            Current phase: {currentPhaseRuns} run{currentPhaseRuns !== 1 ? 's' : ''}
          </p>

          {/* Desktop table view */}
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            {/* Header */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: '70px 1fr 1fr 1fr 1fr 80px',
              gap: '8px',
              padding: '8px 12px',
              fontSize: '10px',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
              color: '#666',
              borderBottom: '1px solid #252540',
              minWidth: '520px',
            }}>
              <div>Distance</div>
              <div>All-Time Best</div>
              <div>All-Time Pace</div>
              <div>Phase Best</div>
              <div>Phase Pace</div>
              <div style={{ textAlign: 'right' }}>Diff</div>
            </div>

            {/* Rows */}
            {distanceKeys.map((distKey) => {
              const at = allTime[distKey];
              const cp = currentPhase[distKey];
              let diffSeconds = null;
              let diffColor = '#a0a0b0';
              if (at && cp) {
                diffSeconds = cp.time_seconds - at.time_seconds;
                if (diffSeconds <= 0) {
                  diffColor = '#4ade80'; // green - current phase matches or beats all-time
                } else if (diffSeconds <= 5) {
                  diffColor = '#4ade80'; // green - very close
                } else if (diffSeconds <= 15) {
                  diffColor = '#fbbf24'; // yellow - moderate gap
                } else {
                  diffColor = '#ff6b6b'; // red - far off
                }
              }

              return (
                <div key={distKey} style={{
                  display: 'grid',
                  gridTemplateColumns: '70px 1fr 1fr 1fr 1fr 80px',
                  gap: '8px',
                  padding: '10px 12px',
                  alignItems: 'center',
                  borderBottom: '1px solid #1e1e35',
                  fontSize: '14px',
                  minWidth: '520px',
                }}>
                  <div style={{ color: '#e0e0e0', fontWeight: 600, fontSize: '13px' }}>
                    {formatDistanceLabel(distKey)}
                  </div>
                  <div>
                    {at ? (
                      <Link to={`/activity/${at.activity_id}`} style={{ color: '#fc5200', textDecoration: 'none', fontWeight: 600 }}>
                        {formatTime(at.time_seconds)}
                      </Link>
                    ) : (
                      <span style={{ color: '#555' }}>-</span>
                    )}
                  </div>
                  <div style={{ color: '#a0a0b0' }}>
                    {at ? `${formatPace(at.pace_sec_per_km)}/km` : '-'}
                  </div>
                  <div style={{ color: '#e0e0e0' }}>
                    {cp ? formatTime(cp.time_seconds) : <span style={{ color: '#555' }}>-</span>}
                  </div>
                  <div style={{ color: '#a0a0b0' }}>
                    {cp ? `${formatPace(cp.pace_sec_per_km)}/km` : '-'}
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    {at && cp ? (
                      diffSeconds <= 0 ? (
                        <span style={{ color: '#4ade80', fontWeight: 700, fontSize: '12px' }}>PR!</span>
                      ) : (
                        <span style={{ color: diffColor, fontSize: '13px' }}>+{Math.round(diffSeconds)}s</span>
                      )
                    ) : (
                      <span style={{ color: '#555' }}>-</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Personal Records */}
      {(prEntries.length > 0 || prs?.best_1km_split) && (
        <div style={cardStyle}>
          <h2 style={sectionTitle}>Personal Records</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '12px' }}>
            {prs?.best_1km_split && (
              <div style={{ backgroundColor: '#16213e', borderRadius: '8px', padding: '16px', textAlign: 'center', border: '1px solid #fc520033' }}>
                <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Best 1km Split</div>
                <div style={{ fontSize: '26px', fontWeight: 700, color: '#fc5200' }}>{formatTime(prs.best_1km_split.time)}</div>
                <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                  {formatPace(prs.best_1km_split.time)}/km
                </div>
              </div>
            )}
            {prEntries.map(([dist, pr]) => (
              <Link to={`/activity/${pr.activity_id}`} key={dist} style={{ textDecoration: 'none' }}>
                <div style={{ backgroundColor: '#16213e', borderRadius: '8px', padding: '16px', textAlign: 'center', cursor: 'pointer', transition: 'border-color 0.2s', border: '1px solid transparent' }}>
                  <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>{dist} PR</div>
                  <div style={{ fontSize: '26px', fontWeight: 700, color: '#4ade80' }}>{formatTime(pr.time_seconds)}</div>
                  <div style={{ fontSize: '11px', color: '#666', marginTop: '2px' }}>
                    {formatPace(pr.pace_sec_per_km)}/km &middot; {formatDate(pr.date)}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Phase Pace Trend */}
      {phasePaceData.length > 0 && (
        <div style={cardStyle}>
          <h2 style={sectionTitle}>Pace Across Phases</h2>
          <p style={{ fontSize: '13px', color: '#666', marginBottom: '16px' }}>Only phases with 3+ runs shown. Lower = faster.</p>
          <div style={chartWrap}>
            <div style={chartInner(500)}>
              <ResponsiveContainer width="100%" height={260}>
                <ComposedChart data={phasePaceData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#252540" />
                  <XAxis dataKey="name" tick={{ fill: '#666', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#666', fontSize: 10 }} domain={['auto', 'auto']} tickFormatter={(v) => formatPace(v)} reversed width={45} />
                  <Tooltip content={<CustomTooltip formatter={(v, name) => {
                    if (name === 'Avg Pace' || name === 'Best Pace') return formatPace(v);
                    if (name === 'Distance') return `${v} km`;
                    return v;
                  }} />} />
                  <Area type="monotone" dataKey="pace" name="Avg Pace" fill="#fc520015" stroke="#fc5200" strokeWidth={2} dot={{ fill: '#fc5200', r: 3 }} />
                  <Line type="monotone" dataKey="bestPace" name="Best Pace" stroke="#4ade80" strokeWidth={1.5} strokeDasharray="4 4" dot={{ fill: '#4ade80', r: 2 }} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Monthly Distance + Runs */}
      {monthlyChart.length > 0 && (
        <div style={cardStyle}>
          <h2 style={sectionTitle}>Monthly Volume</h2>
          <div style={chartWrap}>
            <div style={chartInner(600)}>
              <ResponsiveContainer width="100%" height={240}>
                <ComposedChart data={monthlyChart} margin={{ top: 5, right: 5, left: -10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#252540" />
                  <XAxis dataKey="month" tick={{ fill: '#666', fontSize: 9 }} interval={2} angle={-45} textAnchor="end" height={45} />
                  <YAxis yAxisId="km" tick={{ fill: '#666', fontSize: 10 }} width={35} />
                  <YAxis yAxisId="runs" orientation="right" tick={{ fill: '#666', fontSize: 10 }} width={30} />
                  <Tooltip content={<CustomTooltip formatter={(v, name) => {
                    if (name === 'Distance') return `${v} km`;
                    if (name === 'Pace') return formatPace(v);
                    return v;
                  }} />} />
                  <Bar yAxisId="km" dataKey="distance" name="Distance" fill="#fc520066" radius={[3, 3, 0, 0]} />
                  <Line yAxisId="runs" type="monotone" dataKey="runs" name="Runs" stroke="#60a5fa" strokeWidth={1.5} dot={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Monthly Pace Trend */}
      {monthlyChart.length > 0 && (
        <div style={cardStyle}>
          <h2 style={sectionTitle}>Monthly Avg Pace</h2>
          <div style={chartWrap}>
            <div style={chartInner(500)}>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={monthlyChart.filter(m => m.pace)} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#252540" />
                  <XAxis dataKey="month" tick={{ fill: '#666', fontSize: 9 }} interval={3} angle={-45} textAnchor="end" height={45} />
                  <YAxis tick={{ fill: '#666', fontSize: 10 }} domain={['auto', 'auto']} tickFormatter={(v) => formatPace(v)} reversed width={45} />
                  <Tooltip content={<CustomTooltip formatter={(v) => formatPace(v)} />} />
                  <Line type="monotone" dataKey="pace" name="Avg Pace" stroke="#fc5200" strokeWidth={2} dot={{ r: 2, fill: '#fc5200' }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}

      {/* Activity Heatmap */}
      <div style={cardStyle}>
        <h2 style={sectionTitle}>Running Heatmap (Last 12 Months)</h2>
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          {/* Month labels row */}
          <div style={{ display: 'flex', gap: '2px', minWidth: '600px', marginBottom: '4px', paddingLeft: '18px' }}>
            {weeks.map((week, wi) => {
              const monthStr = week[0]?.date?.substring(0, 7) || '';
              const prevMonthStr = wi > 0 ? (weeks[wi - 1][0]?.date?.substring(0, 7) || '') : '';
              const showLabel = monthStr !== prevMonthStr;
              const label = showLabel ? new Date(monthStr + '-15').toLocaleDateString('en-US', { month: 'short' }) : '';
              return (
                <div key={wi} style={{ width: '10px', fontSize: '9px', color: '#666', whiteSpace: 'nowrap', overflow: 'visible' }}>
                  {label}
                </div>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '2px', minWidth: '600px' }}>
            {/* Day labels */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', marginRight: '4px', justifyContent: 'flex-start' }}>
              {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => (
                <div key={i} style={{ width: '14px', height: '10px', fontSize: '9px', color: '#666', lineHeight: '10px', textAlign: 'right' }}>
                  {i % 2 === 1 ? d : ''}
                </div>
              ))}
            </div>
            {weeks.map((week, wi) => (
              <div key={wi} style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                {week.map((day, di) => {
                  let bg = '#16213e';
                  if (day.runs > 0) {
                    if (day.distance > 5) bg = '#fc5200';
                    else if (day.distance > 3) bg = '#fc5200aa';
                    else bg = '#fc520055';
                  }
                  return (
                    <div key={di} title={`${day.date}: ${day.runs > 0 ? `${day.distance}km` : 'Rest day'}`}
                      style={{ width: '10px', height: '10px', borderRadius: '2px', backgroundColor: bg }} />
                  );
                })}
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '10px', fontSize: '11px', color: '#666', marginLeft: '32px' }}>
            <span>Less</span>
            <div style={{ width: '10px', height: '10px', borderRadius: '2px', backgroundColor: '#16213e' }} />
            <div style={{ width: '10px', height: '10px', borderRadius: '2px', backgroundColor: '#fc520055' }} />
            <div style={{ width: '10px', height: '10px', borderRadius: '2px', backgroundColor: '#fc5200aa' }} />
            <div style={{ width: '10px', height: '10px', borderRadius: '2px', backgroundColor: '#fc5200' }} />
            <span>More</span>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Stats;
