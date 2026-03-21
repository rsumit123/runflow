import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import api from '../api';

function formatDate(dateString) {
  if (!dateString) return '-';
  const d = new Date(dateString);
  return d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function metersToKm(meters) {
  if (!meters) return '0.00';
  return (meters / 1000).toFixed(2);
}

function formatPace(movingTimeSeconds, distanceMeters) {
  if (!movingTimeSeconds || !distanceMeters) return '-';
  const km = distanceMeters / 1000;
  const paceSeconds = movingTimeSeconds / km;
  const mins = Math.floor(paceSeconds / 60);
  const secs = Math.round(paceSeconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatElevation(meters) {
  if (meters == null) return '-';
  return `${Math.round(meters)} m`;
}

function formatPaceFromSpeed(avgSpeed) {
  if (!avgSpeed || avgSpeed === 0) return '-';
  const paceSeconds = 1000 / avgSpeed;
  const mins = Math.floor(paceSeconds / 60);
  const secs = Math.round(paceSeconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function computeStats(activities, allActivities) {
  // allActivities is the full list from current page (for display stats)
  const valid = (allActivities || activities || []).filter((a) => a.distance > 0);
  if (valid.length === 0) return null;

  // Current streak: count consecutive days with runs from most recent
  let streak = 0;
  if (valid.length > 0) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    let checkDate = new Date(today);
    for (const a of valid) {
      const runDate = new Date(a.start_date);
      runDate.setHours(0, 0, 0, 0);
      const diffDays = Math.round((checkDate - runDate) / 86400000);
      if (diffDays <= 1) {
        streak++;
        checkDate = runDate;
      } else {
        break;
      }
    }
  }

  // This week's distance
  const now = new Date();
  const weekStart = new Date(now);
  weekStart.setDate(now.getDate() - now.getDay());
  weekStart.setHours(0, 0, 0, 0);
  const thisWeekRuns = valid.filter(a => new Date(a.start_date) >= weekStart);
  const thisWeekKm = thisWeekRuns.reduce((s, a) => s + (a.distance || 0), 0) / 1000;

  // Last 5 avg pace
  const last5 = valid.slice(0, 5);
  const l5Time = last5.reduce((s, a) => s + (a.moving_time || 0), 0);
  const l5Dist = last5.reduce((s, a) => s + (a.distance || 0), 0);

  // Fastest run
  const fastestRun = valid.reduce((b, a) => (a.average_speed > (b.average_speed || 0) ? a : b), valid[0]);

  return {
    streak,
    thisWeekKm: thisWeekKm.toFixed(1),
    thisWeekRuns: thisWeekRuns.length,
    fastestRunPace: formatPaceFromSpeed(fastestRun.average_speed),
    fastestRunId: fastestRun.id,
    last5AvgPace: l5Dist > 0 ? formatPace(l5Time, l5Dist) : '-',
  };
}

function getDefaultDates() {
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - 7);
  return { start: start.toISOString().split('T')[0], end: end.toISOString().split('T')[0] };
}

const PER_PAGE = 10;

function Dashboard() {
  const [activities, setActivities] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const defaults = getDefaultDates();
  const [startDate, setStartDate] = useState(defaults.start);
  const [endDate, setEndDate] = useState(defaults.end);
  const [pulling, setPulling] = useState(false);
  const [pullResult, setPullResult] = useState(null);
  const [showDatePicker, setShowDatePicker] = useState(false);

  const loadActivities = (p = page) => {
    setLoading(true);
    api.get(`/activities?page=${p}&per_page=${PER_PAGE}`)
      .then((res) => {
        setActivities(res.data.activities || []);
        setTotal(res.data.total || 0);
        setPage(p);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to load activities');
        setLoading(false);
      });
  };

  useEffect(() => { loadActivities(1); }, []); // eslint-disable-line

  const totalPages = Math.ceil(total / PER_PAGE);

  const handlePullByDate = async (start, end) => {
    setPulling(true);
    setPullResult(null);
    try {
      const res = await api.post('/import/by-date', { start_date: start || startDate, end_date: end || endDate });
      setPullResult(res.data);
      loadActivities(1);
    } catch (err) {
      setPullResult({ error: err.response?.data?.detail || 'Failed to pull' });
    }
    setPulling(false);
  };

  const handlePullToday = () => {
    const today = new Date().toISOString().split('T')[0];
    handlePullByDate(today, today);
  };

  const stats = computeStats(activities);

  if (loading && activities.length === 0) {
    return <div style={{ textAlign: 'center', padding: '60px', color: '#a0a0b0' }}>Loading activities...</div>;
  }

  if (error) {
    return (
      <div>
        <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff', marginBottom: '16px' }}>Dashboard</h1>
        <div style={{ padding: '16px', backgroundColor: '#3d1515', border: '1px solid #6b2020', borderRadius: '8px', color: '#ff6b6b', marginBottom: '16px' }}>{error}</div>
        <div style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
          <p>No activities loaded yet.</p>
          <p style={{ marginTop: '8px' }}><Link to="/import">Import your Strava activities</Link> to get started.</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px', flexWrap: 'wrap', gap: '8px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#fff' }}>Activities</h1>
        <button onClick={() => { setPullResult(null); loadActivities(1); }} disabled={loading}
          style={{ padding: '8px 14px', borderRadius: '6px', border: '1px solid #333', fontSize: '13px', fontWeight: 600, cursor: 'pointer', backgroundColor: '#16213e', color: '#e0e0e0' }}>
          &#x21bb; Refresh
        </button>
      </div>

      {/* Pull from Strava */}
      <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '14px 16px', marginBottom: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <button onClick={handlePullToday} disabled={pulling}
            style={{ padding: '10px 20px', borderRadius: '6px', border: 'none', fontSize: '14px', fontWeight: 600, cursor: 'pointer', backgroundColor: '#fc5200', color: '#fff', opacity: pulling ? 0.6 : 1, whiteSpace: 'nowrap', flex: '1 1 auto' }}>
            {pulling ? 'Pulling...' : "Pull Today's Runs"}
          </button>
          <button onClick={() => setShowDatePicker(!showDatePicker)}
            style={{ padding: '10px 12px', borderRadius: '6px', border: '1px solid #333', backgroundColor: '#16213e', color: '#a0a0b0', fontSize: '13px', cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: 0 }}>
            {showDatePicker ? 'Hide' : 'By Date'} {showDatePicker ? '\u25B2' : '\u25BC'}
          </button>
        </div>
        {showDatePicker && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'center', marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #252540' }}>
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
              style={{ padding: '8px 10px', borderRadius: '6px', border: '1px solid #333', backgroundColor: '#16213e', color: '#e0e0e0', fontSize: '14px', flex: '1 1 130px', minWidth: '130px' }} />
            <span style={{ color: '#666', fontSize: '13px' }}>to</span>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}
              style={{ padding: '8px 10px', borderRadius: '6px', border: '1px solid #333', backgroundColor: '#16213e', color: '#e0e0e0', fontSize: '14px', flex: '1 1 130px', minWidth: '130px' }} />
            <button onClick={() => handlePullByDate()} disabled={pulling}
              style={{ padding: '8px 16px', borderRadius: '6px', border: 'none', fontSize: '13px', fontWeight: 600, cursor: 'pointer', backgroundColor: '#fc5200', color: '#fff', opacity: pulling ? 0.6 : 1, whiteSpace: 'nowrap' }}>
              Pull
            </button>
          </div>
        )}
        {pullResult && !pullResult.error && (
          <div style={{ color: '#4ade80', fontSize: '13px', marginTop: '10px' }}>
            Imported {pullResult.imported} new runs
            {pullResult.already_existed ? `, ${pullResult.already_existed} already in DB` : ''}
            {pullResult.skipped_non_running ? `, ${pullResult.skipped_non_running} non-running skipped` : ''}
          </div>
        )}
        {pullResult && pullResult.error && (
          <div style={{ color: '#ff6b6b', fontSize: '13px', marginTop: '10px' }}>{pullResult.error}</div>
        )}
      </div>

      {/* Stats */}
      {stats && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', marginBottom: '20px' }}>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '14px 10px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Streak</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: stats.streak > 0 ? '#4ade80' : '#666' }}>{stats.streak}</div>
            <div style={{ fontSize: '11px', color: '#a0a0b0' }}>days</div>
          </div>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '14px 10px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>This Week</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{stats.thisWeekKm}</div>
            <div style={{ fontSize: '11px', color: '#a0a0b0' }}>km ({stats.thisWeekRuns} runs)</div>
          </div>
          <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '14px 10px', textAlign: 'center' }}>
            <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Last 5 Pace</div>
            <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{stats.last5AvgPace}</div>
            <div style={{ fontSize: '11px', color: '#a0a0b0' }}>min/km</div>
          </div>
          <Link to={`/activity/${stats.fastestRunId}`} style={{ textDecoration: 'none' }}>
            <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '14px 10px', textAlign: 'center', cursor: 'pointer' }}>
              <div style={{ fontSize: '10px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px', color: '#a0a0b0', marginBottom: '4px' }}>Fastest</div>
              <div style={{ fontSize: '22px', fontWeight: 700, color: '#fc5200' }}>{stats.fastestRunPace}</div>
              <div style={{ fontSize: '11px', color: '#a0a0b0' }}>min/km</div>
            </div>
          </Link>
        </div>
      )}

      {/* Activity List */}
      {activities.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
          <p>No activities found.</p>
          <p style={{ marginTop: '8px' }}><Link to="/import">Import your Strava activities</Link> to get started.</p>
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="table-scroll" style={{ display: 'block' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', backgroundColor: '#1a1a2e', borderRadius: '8px', overflow: 'hidden', minWidth: '500px' }}>
              <thead>
                <tr>
                  {['Name', 'Date', 'Distance', 'Pace', 'Elev.'].map((h) => (
                    <th key={h} style={{ textAlign: 'left', padding: '12px 14px', backgroundColor: '#16213e', color: '#a0a0b0', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '1px' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {activities.map((a) => (
                  <tr key={a.id} style={{ cursor: 'pointer' }}>
                    <td style={{ padding: '12px 14px', borderBottom: '1px solid #252540', fontSize: '14px' }}>
                      <Link to={`/activity/${a.id}`} style={{ color: '#fc5200', fontWeight: 500 }}>
                        {a.name || 'Untitled'}
                      </Link>
                    </td>
                    <td style={{ padding: '12px 14px', borderBottom: '1px solid #252540', fontSize: '13px', whiteSpace: 'nowrap' }}>{formatDate(a.start_date)}</td>
                    <td style={{ padding: '12px 14px', borderBottom: '1px solid #252540', fontSize: '13px', whiteSpace: 'nowrap' }}>{metersToKm(a.distance)} km</td>
                    <td style={{ padding: '12px 14px', borderBottom: '1px solid #252540', fontSize: '13px', whiteSpace: 'nowrap' }}>{formatPace(a.moving_time, a.distance)}</td>
                    <td style={{ padding: '12px 14px', borderBottom: '1px solid #252540', fontSize: '13px', whiteSpace: 'nowrap' }}>{formatElevation(a.total_elevation_gain)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '12px', marginTop: '20px', flexWrap: 'wrap' }}>
              <button onClick={() => loadActivities(page - 1)} disabled={page <= 1}
                style={{ padding: '8px 14px', borderRadius: '6px', border: '1px solid #333', backgroundColor: page <= 1 ? '#1a1a2e' : '#16213e', color: page <= 1 ? '#444' : '#e0e0e0', fontSize: '13px', cursor: page <= 1 ? 'default' : 'pointer' }}>
                Prev
              </button>
              <span style={{ color: '#a0a0b0', fontSize: '13px' }}>
                Page {page} of {totalPages} ({total} runs)
              </span>
              <button onClick={() => loadActivities(page + 1)} disabled={page >= totalPages}
                style={{ padding: '8px 14px', borderRadius: '6px', border: '1px solid #333', backgroundColor: page >= totalPages ? '#1a1a2e' : '#16213e', color: page >= totalPages ? '#444' : '#e0e0e0', fontSize: '13px', cursor: page >= totalPages ? 'default' : 'pointer' }}>
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default Dashboard;
