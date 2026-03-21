import React, { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';
import { MapContainer, TileLayer, Polyline } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import api from '../api';

// Decode Google-encoded polyline
function decodePolyline(encoded) {
  if (!encoded) return [];
  const points = [];
  let index = 0, lat = 0, lng = 0;
  while (index < encoded.length) {
    let b, shift = 0, result = 0;
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lat += (result & 1) ? ~(result >> 1) : (result >> 1);
    shift = 0; result = 0;
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lng += (result & 1) ? ~(result >> 1) : (result >> 1);
    points.push([lat / 1e5, lng / 1e5]);
  }
  return points;
}

function MiniRouteMap({ polyline }) {
  const positions = decodePolyline(polyline);
  if (positions.length < 2) return null;

  // Calculate center and bounds
  const lats = positions.map(p => p[0]);
  const lngs = positions.map(p => p[1]);
  const center = [(Math.min(...lats) + Math.max(...lats)) / 2, (Math.min(...lngs) + Math.max(...lngs)) / 2];

  return (
    <div style={{ width: '100px', height: '80px', borderRadius: '6px', overflow: 'hidden', flexShrink: 0 }}>
      <MapContainer
        center={center}
        bounds={[[Math.min(...lats) - 0.002, Math.min(...lngs) - 0.002], [Math.max(...lats) + 0.002, Math.max(...lngs) + 0.002]]}
        style={{ height: '100%', width: '100%' }}
        zoomControl={false}
        attributionControl={false}
        dragging={false}
        scrollWheelZoom={false}
        doubleClickZoom={false}
        touchZoom={false}
      >
        <TileLayer url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png" />
        <Polyline positions={positions} pathOptions={{ color: '#fc5200', weight: 2, opacity: 0.9 }} />
      </MapContainer>
    </div>
  );
}

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

// Compute route highlight badges
function computeBadges(routes) {
  if (!routes || routes.length === 0) return {};

  const badges = {};

  // Fastest: route with the best pace (lowest best_pace_sec_per_km)
  const withPace = routes.filter(r => r.best_pace_sec_per_km);
  if (withPace.length > 0) {
    const fastest = withPace.reduce((a, b) => a.best_pace_sec_per_km < b.best_pace_sec_per_km ? a : b);
    badges[fastest.route_key] = badges[fastest.route_key] || [];
    badges[fastest.route_key].push({ label: 'Fastest', color: '#4ade80', bg: '#4ade8018' });
  }

  // Most Improved: biggest pace decrease between first half and second half of attempts
  let bestImprovement = -Infinity;
  let mostImprovedKey = null;
  for (const route of routes) {
    const acts = (route.activities || []).filter(a => a.pace_sec_per_km);
    if (acts.length < 4) continue;
    const mid = Math.floor(acts.length / 2);
    const firstHalf = acts.slice(0, mid);
    const secondHalf = acts.slice(mid);
    const firstAvg = firstHalf.reduce((s, a) => s + a.pace_sec_per_km, 0) / firstHalf.length;
    const secondAvg = secondHalf.reduce((s, a) => s + a.pace_sec_per_km, 0) / secondHalf.length;
    const improvement = firstAvg - secondAvg; // positive = improved (lower pace = faster)
    if (improvement > bestImprovement) {
      bestImprovement = improvement;
      mostImprovedKey = route.route_key;
    }
  }
  if (mostImprovedKey && bestImprovement > 0) {
    badges[mostImprovedKey] = badges[mostImprovedKey] || [];
    badges[mostImprovedKey].push({ label: 'Most Improved', color: '#60a5fa', bg: '#60a5fa18' });
  }

  // Hilliest: highest average elevation gain across activities
  let bestElev = -Infinity;
  let hilliestKey = null;
  for (const route of routes) {
    const acts = (route.activities || []).filter(a => a.elevation_gain && a.elevation_gain > 0);
    if (acts.length === 0) continue;
    const avgElev = acts.reduce((s, a) => s + a.elevation_gain, 0) / acts.length;
    if (avgElev > bestElev) {
      bestElev = avgElev;
      hilliestKey = route.route_key;
    }
  }
  if (hilliestKey && bestElev > 0) {
    badges[hilliestKey] = badges[hilliestKey] || [];
    badges[hilliestKey].push({ label: 'Hilliest', color: '#fb923c', bg: '#fb923c18' });
  }

  // Most Consistent: lowest pace variance
  let bestVariance = Infinity;
  let consistentKey = null;
  for (const route of routes) {
    const paces = (route.activities || []).filter(a => a.pace_sec_per_km).map(a => a.pace_sec_per_km);
    if (paces.length < 3) continue;
    const mean = paces.reduce((s, v) => s + v, 0) / paces.length;
    const variance = paces.reduce((s, v) => s + (v - mean) ** 2, 0) / paces.length;
    if (variance < bestVariance) {
      bestVariance = variance;
      consistentKey = route.route_key;
    }
  }
  if (consistentKey && bestVariance < Infinity) {
    badges[consistentKey] = badges[consistentKey] || [];
    badges[consistentKey].push({ label: 'Most Consistent', color: '#c084fc', bg: '#c084fc18' });
  }

  return badges;
}

function Routes() {
  const [routes, setRoutes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedRoute, setExpandedRoute] = useState(null);
  const [editingRoute, setEditingRoute] = useState(null);
  const [editName, setEditName] = useState('');
  const [mergingRoute, setMergingRoute] = useState(null); // route_key of route being merged
  const [mergeLoading, setMergeLoading] = useState(false);

  const loadRoutes = () => {
    setLoading(true);
    api.get('/routes')
      .then((res) => {
        setRoutes(res.data.routes || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => {
    loadRoutes();
  }, []);

  const badges = useMemo(() => computeBadges(routes), [routes]);

  const handleRename = async (routeKey) => {
    if (!editName.trim()) return;
    try {
      await api.post('/routes/label', { route_key: routeKey, name: editName.trim() });
      setRoutes(routes.map(r =>
        r.route_key === routeKey ? { ...r, custom_name: editName.trim() } : r
      ));
      setEditingRoute(null);
      setEditName('');
    } catch (err) {
      alert('Failed to save name');
    }
  };

  const handleMerge = async (sourceKey, targetKey) => {
    setMergeLoading(true);
    try {
      await api.post('/routes/merge', { source_route_key: sourceKey, target_route_key: targetKey });
      setMergingRoute(null);
      setExpandedRoute(null);
      loadRoutes();
    } catch (err) {
      alert('Failed to merge routes');
    } finally {
      setMergeLoading(false);
    }
  };

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

          const routeBadges = badges[route.route_key] || [];
          const isMerging = mergingRoute === route.route_key;

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
                  padding: '14px',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                }}
              >
                {route.polyline && <MiniRouteMap polyline={route.polyline} />}
                <div style={{ flex: '1 1 auto', minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                    {editingRoute === route.route_key ? (
                      <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }} onClick={(e) => e.stopPropagation()}>
                        <input
                          type="text"
                          value={editName}
                          onChange={(e) => setEditName(e.target.value)}
                          onKeyDown={(e) => e.key === 'Enter' && handleRename(route.route_key)}
                          placeholder="Route name..."
                          autoFocus
                          style={{ padding: '4px 8px', borderRadius: '4px', border: '1px solid #333', backgroundColor: '#16213e', color: '#fff', fontSize: '14px', width: '160px' }}
                        />
                        <button onClick={() => handleRename(route.route_key)}
                          style={{ padding: '4px 10px', borderRadius: '4px', border: 'none', backgroundColor: '#fc5200', color: '#fff', fontSize: '12px', cursor: 'pointer', fontWeight: 600 }}>
                          Save
                        </button>
                        <button onClick={() => { setEditingRoute(null); setEditName(''); }}
                          style={{ padding: '4px 8px', borderRadius: '4px', border: '1px solid #333', backgroundColor: 'transparent', color: '#666', fontSize: '12px', cursor: 'pointer' }}>
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <>
                        <span style={{ fontSize: '16px', fontWeight: 700, color: '#fff' }}>
                          {route.custom_name || `~${route.avg_distance_km} km route`}
                        </span>
                        {route.custom_name && (
                          <span style={{ fontSize: '12px', color: '#666' }}>~{route.avg_distance_km} km</span>
                        )}
                        <button
                          onClick={(e) => { e.stopPropagation(); setEditingRoute(route.route_key); setEditName(route.custom_name || ''); }}
                          style={{ padding: '2px 6px', borderRadius: '4px', border: '1px solid #333', backgroundColor: 'transparent', color: '#666', fontSize: '11px', cursor: 'pointer' }}
                          title="Rename route"
                        >
                          &#9998;
                        </button>
                      </>
                    )}
                    <span style={{ fontSize: '12px', color: '#fc5200', backgroundColor: '#fc520015', padding: '2px 8px', borderRadius: '10px', fontWeight: 600 }}>
                      {route.run_count} runs
                    </span>
                    {routeBadges.map((badge, i) => (
                      <span key={i} style={{
                        fontSize: '11px',
                        padding: '2px 8px',
                        borderRadius: '10px',
                        fontWeight: 600,
                        color: badge.color,
                        backgroundColor: badge.bg,
                      }}>
                        {badge.label}
                      </span>
                    ))}
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
                      gridTemplateColumns: '1fr 80px 70px 90px',
                      gap: '4px',
                      padding: '6px 10px',
                      fontSize: '10px',
                      fontWeight: 600,
                      textTransform: 'uppercase',
                      color: '#555',
                      borderBottom: '1px solid #252540',
                      minWidth: '360px',
                    }}>
                      <div>Date</div>
                      <div>Time</div>
                      <div>Pace</div>
                      <div>vs Route PR</div>
                    </div>
                    {[...activities].reverse().map((a) => {
                      const isBest = a.pace_sec_per_km && bestPace && Math.abs(a.pace_sec_per_km - bestPace) < 0.5;
                      const isWorst = a.pace_sec_per_km && worstPace && Math.abs(a.pace_sec_per_km - worstPace) < 0.5;
                      const diff = a.pace_sec_per_km && bestPace ? a.pace_sec_per_km - bestPace : null;
                      return (
                        <Link to={`/activity/${a.id}`} key={a.id} style={{ textDecoration: 'none' }}>
                          <div style={{
                            display: 'grid',
                            gridTemplateColumns: '1fr 80px 70px 90px',
                            gap: '4px',
                            padding: '8px 10px',
                            borderBottom: '1px solid #1e1e35',
                            fontSize: '13px',
                            alignItems: 'center',
                            backgroundColor: isBest ? 'rgba(74,222,128,0.05)' : 'transparent',
                            minWidth: '360px',
                          }}>
                            <div style={{ color: '#e0e0e0' }}>{formatDate(a.date)}</div>
                            <div style={{ color: '#e0e0e0' }}>{formatTime(a.moving_time)}</div>
                            <div style={{ color: isBest ? '#4ade80' : isWorst ? '#ff6b6b' : '#a0a0b0', fontWeight: isBest ? 700 : 400 }}>
                              {formatPace(a.pace_sec_per_km)}
                            </div>
                            <div>
                              {isBest ? (
                                <span style={{ color: '#4ade80', fontSize: '11px', fontWeight: 700 }}>Route PR!</span>
                              ) : diff != null ? (
                                <span style={{ color: diff <= 10 ? '#fbbf24' : '#ff6b6b', fontSize: '12px' }}>+{Math.round(diff)}s from PR</span>
                              ) : '-'}
                            </div>
                          </div>
                        </Link>
                      );
                    })}
                  </div>

                  {/* Merge button */}
                  <div style={{ marginTop: '16px', borderTop: '1px solid #252540', paddingTop: '12px' }}>
                    {!isMerging ? (
                      <button
                        onClick={(e) => { e.stopPropagation(); setMergingRoute(route.route_key); }}
                        style={{
                          padding: '6px 14px',
                          borderRadius: '6px',
                          border: '1px solid #333',
                          backgroundColor: 'transparent',
                          color: '#a0a0b0',
                          fontSize: '12px',
                          cursor: 'pointer',
                          fontWeight: 600,
                        }}
                      >
                        Merge with another route...
                      </button>
                    ) : (
                      <div onClick={(e) => e.stopPropagation()}>
                        <div style={{ fontSize: '12px', color: '#a0a0b0', marginBottom: '8px', fontWeight: 600 }}>
                          Select a route to merge into this one:
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', maxHeight: '200px', overflowY: 'auto' }}>
                          {routes.filter(r => r.route_key !== route.route_key).map(r => (
                            <button
                              key={r.route_key}
                              disabled={mergeLoading}
                              onClick={() => handleMerge(r.route_key, route.route_key)}
                              style={{
                                padding: '8px 12px',
                                borderRadius: '6px',
                                border: '1px solid #333',
                                backgroundColor: '#16213e',
                                color: '#e0e0e0',
                                fontSize: '13px',
                                cursor: mergeLoading ? 'wait' : 'pointer',
                                textAlign: 'left',
                                opacity: mergeLoading ? 0.5 : 1,
                              }}
                            >
                              {r.custom_name || `~${r.avg_distance_km} km route`}
                              <span style={{ color: '#666', marginLeft: '8px', fontSize: '11px' }}>
                                {r.avg_distance_km} km · {r.run_count} runs
                              </span>
                            </button>
                          ))}
                        </div>
                        <button
                          onClick={() => setMergingRoute(null)}
                          style={{
                            marginTop: '8px',
                            padding: '4px 10px',
                            borderRadius: '4px',
                            border: '1px solid #333',
                            backgroundColor: 'transparent',
                            color: '#666',
                            fontSize: '12px',
                            cursor: 'pointer',
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    )}
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
