import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';
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
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' });
}

function formatDateShort(dateString) {
  if (!dateString) return '-';
  const d = new Date(dateString);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload || !payload.length) return null;
  return (
    <div style={{ backgroundColor: '#16213e', border: '1px solid #333', borderRadius: '6px', padding: '8px 12px', fontSize: '12px' }}>
      <div style={{ color: '#a0a0b0', marginBottom: '2px' }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>
          {p.name}: {formatPace(p.value)}/km
        </div>
      ))}
    </div>
  );
};

function Routes() {
  const [routes, setRoutes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedRoute, setExpandedRoute] = useState(null);

  useEffect(() => {
    api.get('/routes')
      .then((res) => {
        setRoutes(res.data.routes || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '60px', color: '#a0a0b0' }}>Analyzing routes...</div>;
  }

  return (
    <div>
      <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff', marginBottom: '8px' }}>Routes</h1>
      <p style={{ fontSize: '14px', color: '#a0a0b0', marginBottom: '24px' }}>
        {routes.length} routes detected from your runs (routes with 2+ attempts).
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {routes.map((route) => {
          const isExpanded = expandedRoute === route.route_id;
          const activities = route.activities || [];

          // Pace trend data for chart
          const chartData = activities.map(a => ({
            date: formatDateShort(a.date),
            pace: a.pace_sec_per_km,
          }));

          // Find best and worst
          const paces = activities.filter(a => a.pace_sec_per_km).map(a => a.pace_sec_per_km);
          const bestPace = paces.length > 0 ? Math.min(...paces) : null;
          const worstPace = paces.length > 0 ? Math.max(...paces) : null;

          // Recent trend
          const recent3 = activities.slice(-3).filter(a => a.pace_sec_per_km);
          const older3 = activities.slice(-6, -3).filter(a => a.pace_sec_per_km);
          let trend = null;
          if (recent3.length > 0 && older3.length > 0) {
            const recentAvg = recent3.reduce((s, a) => s + a.pace_sec_per_km, 0) / recent3.length;
            const olderAvg = older3.reduce((s, a) => s + a.pace_sec_per_km, 0) / older3.length;
            const diff = olderAvg - recentAvg;
            if (diff > 3) trend = { dir: 'improving', diff: Math.round(diff) };
            else if (diff < -3) trend = { dir: 'slowing', diff: Math.round(Math.abs(diff)) };
            else trend = { dir: 'steady', diff: 0 };
          }

          return (
            <div key={route.route_id} style={{
              backgroundColor: '#1a1a2e',
              borderRadius: '8px',
              overflow: 'hidden',
            }}>
              {/* Route header - clickable */}
              <div
                onClick={() => setExpandedRoute(isExpanded ? null : route.route_id)}
                style={{
                  padding: '16px',
                  cursor: 'pointer',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: '8px',
                }}
              >
                <div style={{ flex: '1 1 auto', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>
                      ~{route.avg_distance_km} km route
                    </span>
                    <span style={{ fontSize: '12px', color: '#fc5200', backgroundColor: '#fc520015', padding: '2px 8px', borderRadius: '10px', fontWeight: 600 }}>
                      {route.run_count} runs
                    </span>
                    {trend && (
                      <span style={{
                        fontSize: '11px',
                        padding: '2px 8px',
                        borderRadius: '10px',
                        fontWeight: 600,
                        color: trend.dir === 'improving' ? '#4ade80' : trend.dir === 'slowing' ? '#ff6b6b' : '#a0a0b0',
                        backgroundColor: trend.dir === 'improving' ? '#4ade8010' : trend.dir === 'slowing' ? '#ff6b6b10' : '#a0a0b010',
                      }}>
                        {trend.dir === 'improving' ? `\u2191 ${trend.diff}s faster` : trend.dir === 'slowing' ? `\u2193 ${trend.diff}s slower` : 'Steady'}
                      </span>
                    )}
                  </div>
                  <div style={{ fontSize: '13px', color: '#a0a0b0', marginTop: '4px' }}>
                    Best: <span style={{ color: '#4ade80', fontWeight: 600 }}>{formatPace(route.best_pace_sec_per_km)}/km</span>
                    <span style={{ margin: '0 8px', color: '#333' }}>|</span>
                    {formatDate(activities[0]?.date)} — {formatDate(activities[activities.length - 1]?.date)}
                  </div>
                </div>
                <span style={{ color: '#666', fontSize: '16px', flexShrink: 0 }}>
                  {isExpanded ? '\u25B2' : '\u25BC'}
                </span>
              </div>

              {/* Expanded: pace chart + run list */}
              {isExpanded && (
                <div style={{ padding: '0 16px 16px' }}>
                  {/* Pace trend chart */}
                  {chartData.length > 2 && (
                    <div style={{ marginBottom: '16px' }}>
                      <div style={{ fontSize: '12px', color: '#666', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>
                        Pace Over Time (lower = faster)
                      </div>
                      <ResponsiveContainer width="100%" height={180}>
                        <LineChart data={chartData} margin={{ top: 5, right: 10, left: -10, bottom: 5 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#252540" />
                          <XAxis dataKey="date" tick={{ fill: '#666', fontSize: 9 }} interval={Math.max(0, Math.floor(chartData.length / 8))} />
                          <YAxis tick={{ fill: '#666', fontSize: 10 }} domain={['auto', 'auto']} tickFormatter={v => formatPace(v)} reversed width={45} />
                          <Tooltip content={<CustomTooltip />} />
                          <Line type="monotone" dataKey="pace" name="Pace" stroke="#fc5200" strokeWidth={2} dot={{ r: 3, fill: '#fc5200' }} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}

                  {/* Run list */}
                  <div style={{ fontSize: '12px', color: '#666', marginBottom: '8px', textTransform: 'uppercase', letterSpacing: '0.5px', fontWeight: 600 }}>
                    All Attempts
                  </div>
                  <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: '1fr 80px 70px 70px',
                      gap: '4px',
                      padding: '6px 10px',
                      fontSize: '10px',
                      fontWeight: 600,
                      textTransform: 'uppercase',
                      color: '#555',
                      borderBottom: '1px solid #252540',
                      minWidth: '340px',
                    }}>
                      <div>Date</div>
                      <div>Time</div>
                      <div>Pace</div>
                      <div>vs Best</div>
                    </div>
                    {[...activities].reverse().map((a) => {
                      const isBest = a.pace_sec_per_km && bestPace && Math.abs(a.pace_sec_per_km - bestPace) < 0.5;
                      const isWorst = a.pace_sec_per_km && worstPace && Math.abs(a.pace_sec_per_km - worstPace) < 0.5;
                      const diff = a.pace_sec_per_km && bestPace ? a.pace_sec_per_km - bestPace : null;
                      return (
                        <Link to={`/activity/${a.id}`} key={a.id} style={{ textDecoration: 'none' }}>
                          <div style={{
                            display: 'grid',
                            gridTemplateColumns: '1fr 80px 70px 70px',
                            gap: '4px',
                            padding: '8px 10px',
                            borderBottom: '1px solid #1e1e35',
                            fontSize: '13px',
                            alignItems: 'center',
                            backgroundColor: isBest ? 'rgba(74,222,128,0.05)' : 'transparent',
                            minWidth: '340px',
                          }}>
                            <div style={{ color: '#e0e0e0' }}>{formatDate(a.date)}</div>
                            <div style={{ color: '#e0e0e0' }}>{formatTime(a.moving_time)}</div>
                            <div style={{ color: isBest ? '#4ade80' : isWorst ? '#ff6b6b' : '#a0a0b0', fontWeight: isBest ? 700 : 400 }}>
                              {formatPace(a.pace_sec_per_km)}
                            </div>
                            <div>
                              {isBest ? (
                                <span style={{ color: '#4ade80', fontSize: '11px', fontWeight: 700 }}>Best</span>
                              ) : diff != null ? (
                                <span style={{ color: diff <= 10 ? '#fbbf24' : '#ff6b6b', fontSize: '12px' }}>+{Math.round(diff)}s</span>
                              ) : '-'}
                            </div>
                          </div>
                        </Link>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default Routes;
