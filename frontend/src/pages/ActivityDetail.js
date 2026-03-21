import React, { useState, useEffect, useMemo } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import api from '../api';
import SplitsTable from '../components/SplitsTable';
import RouteMap from '../components/RouteMap';

const backLink = {
  display: 'inline-block',
  marginBottom: '20px',
  color: '#a0a0b0',
  fontSize: '14px',
};

const titleStyle = {
  fontSize: '22px',
  fontWeight: 700,
  color: '#ffffff',
  marginBottom: '8px',
  wordBreak: 'break-word',
};

const dateStyle = {
  fontSize: '14px',
  color: '#a0a0b0',
  marginBottom: '24px',
};

const statsGrid = {
  display: 'grid',
  gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))',
  gap: '12px',
  marginBottom: '24px',
};

const statCard = {
  backgroundColor: '#1a1a2e',
  borderRadius: '8px',
  padding: '20px',
  textAlign: 'center',
};

const statLabel = {
  fontSize: '11px',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '1px',
  color: '#a0a0b0',
  marginBottom: '6px',
};

const statValue = {
  fontSize: '24px',
  fontWeight: 700,
  color: '#fc5200',
};

const statUnit = {
  fontSize: '13px',
  color: '#a0a0b0',
  marginTop: '2px',
};

const sectionTitle = {
  fontSize: '20px',
  fontWeight: 600,
  color: '#ffffff',
  marginBottom: '16px',
  marginTop: '8px',
};

const mapPlaceholder = {
  backgroundColor: '#1a1a2e',
  borderRadius: '8px',
  height: '300px',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  color: '#555',
  fontSize: '15px',
  border: '1px dashed #333',
  marginBottom: '32px',
};

const loadingStyle = {
  textAlign: 'center',
  padding: '60px',
  color: '#a0a0b0',
  fontSize: '16px',
};

const errorStyle = {
  padding: '16px',
  backgroundColor: '#3d1515',
  border: '1px solid #6b2020',
  borderRadius: '8px',
  color: '#ff6b6b',
};

const bestSplitCard = {
  backgroundColor: '#1a1a2e',
  borderRadius: '8px',
  padding: '20px',
  textAlign: 'center',
  border: '1px solid #fc5200',
  marginBottom: '24px',
};

function formatDate(dateString) {
  if (!dateString) return '-';
  const d = new Date(dateString);
  return d.toLocaleDateString('en-US', {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
}

function formatTime(seconds) {
  if (!seconds) return '-';
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = Math.round(seconds % 60);
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins}:${secs.toString().padStart(2, '0')}`;
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

function formatPaceSeconds(totalSeconds) {
  if (!totalSeconds || totalSeconds === Infinity) return '-';
  const mins = Math.floor(totalSeconds / 60);
  const secs = Math.round(totalSeconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function formatDistanceLabel(meters) {
  if (meters >= 1000) return `${meters / 1000}km`;
  return `${meters}m`;
}

const deleteButtonStyle = {
  padding: '8px 20px',
  borderRadius: '6px',
  border: '1px solid #6b2020',
  backgroundColor: 'transparent',
  color: '#ff6b6b',
  fontSize: '13px',
  fontWeight: 600,
  cursor: 'pointer',
  marginLeft: '16px',
};

function formatDistLabel(m) {
  return m >= 1000 ? `${m / 1000}km` : `${m}m`;
}

function ActivityDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [activity, setActivity] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [analysis, setAnalysis] = useState(null);
  const [highlightDist, setHighlightDist] = useState(null);

  const handleDelete = async () => {
    if (!window.confirm('Delete this activity from your local database? (This does NOT delete from Strava)')) return;
    setDeleting(true);
    try {
      await api.delete(`/activities/${id}`);
      navigate('/');
    } catch (err) {
      alert('Failed to delete: ' + (err.response?.data?.detail || err.message));
      setDeleting(false);
    }
  };

  useEffect(() => {
    api
      .get(`/activities/${id}`)
      .then((res) => {
        setActivity(res.data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to load activity');
        setLoading(false);
      });

    api
      .get(`/activities/${id}/analysis`)
      .then((res) => {
        setAnalysis(res.data);
      })
      .catch(() => {
        // No analysis available (no GPS streams) - that's fine
      });
  }, [id]);

  const best1kmSplit = useMemo(() => {
    if (!activity || !activity.splits || activity.splits.length === 0) return null;
    let bestPace = Infinity;
    let bestSplit = null;
    activity.splits.forEach((split) => {
      const dist = split.distance || 0;
      const time = split.moving_time || split.elapsed_time || 0;
      if (dist > 0 && time > 0) {
        const km = dist / 1000;
        const paceSeconds = time / km;
        if (paceSeconds < bestPace) {
          bestPace = paceSeconds;
          bestSplit = { ...split, paceSeconds: paceSeconds };
        }
      }
    });
    return bestSplit;
  }, [activity]);

  if (loading) {
    return <div style={loadingStyle}>Loading activity...</div>;
  }

  if (error) {
    return (
      <div>
        <Link to="/" style={backLink}>
          &larr; Back to Dashboard
        </Link>
        <div style={errorStyle}>{error}</div>
      </div>
    );
  }

  if (!activity) return null;

  const elevationM = activity.total_elevation_gain
    ? Math.round(activity.total_elevation_gain)
    : 0;

  const hasBestEfforts = analysis && analysis.best_efforts && analysis.best_efforts.length > 0;

  return (
    <div>
      <Link to="/" style={backLink}>
        &larr; Back to Dashboard
      </Link>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
        <h1 style={{ ...titleStyle, marginBottom: 0, flex: '1 1 auto', minWidth: 0 }}>{activity.name || 'Untitled Activity'}</h1>
        <button style={{ ...deleteButtonStyle, marginLeft: 0, whiteSpace: 'nowrap', flexShrink: 0 }} onClick={handleDelete} disabled={deleting}>
          {deleting ? 'Deleting...' : 'Delete'}
        </button>
      </div>
      <div style={dateStyle}>
        {formatDate(activity.start_date)}
        {activity.route_name && (
          <span style={{ marginLeft: '12px', color: '#fc5200', backgroundColor: '#fc520015', padding: '2px 10px', borderRadius: '10px', fontSize: '12px', fontWeight: 600 }}>
            {activity.route_name}
          </span>
        )}
      </div>

      <div style={statsGrid}>
        <div style={statCard}>
          <div style={statLabel}>Distance</div>
          <div style={statValue}>{metersToKm(activity.distance)}</div>
          <div style={statUnit}>km</div>
        </div>
        <div style={statCard}>
          <div style={statLabel}>Pace</div>
          <div style={statValue}>{formatPace(activity.moving_time, activity.distance)}</div>
          <div style={statUnit}>min/km</div>
        </div>
        <div style={statCard}>
          <div style={statLabel}>Moving Time</div>
          <div style={statValue}>{formatTime(activity.moving_time)}</div>
          <div style={statUnit}>h:m:s</div>
        </div>
        <div style={statCard}>
          <div style={statLabel}>Elapsed Time</div>
          <div style={statValue}>{formatTime(activity.elapsed_time)}</div>
          <div style={statUnit}>h:m:s</div>
        </div>
        <div style={statCard}>
          <div style={statLabel}>Elevation Gain</div>
          <div style={statValue}>{elevationM}</div>
          <div style={statUnit}>m</div>
        </div>
        {activity.average_heartrate && (
          <div style={statCard}>
            <div style={statLabel}>Avg Heart Rate</div>
            <div style={statValue}>{Math.round(activity.average_heartrate)}</div>
            <div style={statUnit}>bpm</div>
          </div>
        )}
        {activity.max_heartrate && (
          <div style={statCard}>
            <div style={statLabel}>Max Heart Rate</div>
            <div style={statValue}>{Math.round(activity.max_heartrate)}</div>
            <div style={statUnit}>bpm</div>
          </div>
        )}
        {activity.average_cadence && (
          <div style={statCard}>
            <div style={statLabel}>Avg Cadence</div>
            <div style={statValue}>{Math.round(activity.average_cadence * 2)}</div>
            <div style={statUnit}>spm</div>
          </div>
        )}
      </div>

      {best1kmSplit && (
        <div style={bestSplitCard}>
          <div style={statLabel}>Best 1km Split</div>
          <div style={statValue}>{formatPaceSeconds(best1kmSplit.paceSeconds)}</div>
          <div style={statUnit}>
            min/km (Split #{best1kmSplit.split_number || best1kmSplit.split || '?'})
          </div>
        </div>
      )}

      {/* Run Analysis - Best Efforts */}
      {hasBestEfforts && (
        <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '20px', marginBottom: '24px' }}>
          <h2 style={{ ...sectionTitle, marginTop: 0 }}>Run Analysis</h2>

          {analysis.pace_percentile != null && (
            <div style={{
              backgroundColor: '#16213e',
              borderRadius: '8px',
              padding: '14px 18px',
              marginBottom: '18px',
              border: '1px solid #fc520033',
              fontSize: '15px',
              color: '#e0e0e0',
              textAlign: 'center',
            }}>
              This run was faster than <span style={{ color: '#fc5200', fontWeight: 700 }}>{Math.round(analysis.pace_percentile)}%</span> of all your runs
            </div>
          )}

          <div style={{ fontSize: '14px', fontWeight: 600, color: '#a0a0b0', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            Best Efforts
          </div>

          {/* Table */}
          <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
            <div style={{
              display: 'grid',
              gridTemplateColumns: '60px 1fr 1fr 1fr 80px 80px',
              gap: '6px',
              padding: '8px 10px',
              fontSize: '10px',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
              color: '#666',
              borderBottom: '1px solid #252540',
              minWidth: '480px',
            }}>
              <div>Dist</div>
              <div>Time</div>
              <div>Pace</div>
              <div>Percentile</div>
              <div>vs Best</div>
              <div>vs Phase</div>
            </div>

            {analysis.best_efforts.map((effort) => (
              <div key={effort.distance} style={{
                display: 'grid',
                gridTemplateColumns: '60px 1fr 1fr 1fr 80px 80px',
                gap: '6px',
                padding: '8px 10px',
                alignItems: 'center',
                borderBottom: '1px solid #1e1e35',
                fontSize: '13px',
                minWidth: '480px',
              }}>
                <div style={{ color: '#e0e0e0', fontWeight: 600 }}>
                  {formatDistanceLabel(effort.distance)}
                </div>
                <div style={{ color: '#e0e0e0' }}>
                  {formatTime(effort.time_seconds)}
                </div>
                <div style={{ color: '#a0a0b0', fontSize: '12px' }}>
                  {formatPaceSeconds(effort.pace_sec_per_km)}/km
                </div>
                <div style={{ color: '#a0a0b0', fontSize: '11px' }}>
                  Top {100 - Math.round(effort.percentile)}%
                </div>
                <div>
                  {effort.is_pr ? (
                    <span style={{ backgroundColor: '#4ade80', color: '#0a2a0a', fontSize: '10px', fontWeight: 700, padding: '2px 6px', borderRadius: '4px' }}>PR!</span>
                  ) : effort.diff_from_best != null ? (
                    <span style={{ color: '#ff6b6b', fontSize: '12px' }}>+{Math.round(effort.diff_from_best)}s</span>
                  ) : '-'}
                </div>
                <div>
                  {effort.diff_from_phase != null ? (
                    effort.diff_from_phase <= 0 ? (
                      <span style={{ color: '#4ade80', fontSize: '10px', fontWeight: 700 }}>Phase best!</span>
                    ) : (
                      <span style={{ color: effort.diff_from_phase <= 5 ? '#fbbf24' : '#ff6b6b', fontSize: '12px' }}>+{Math.round(effort.diff_from_phase)}s</span>
                    )
                  ) : <span style={{ color: '#555', fontSize: '11px' }}>1st in phase</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px', marginBottom: '12px' }}>
        <h2 style={{ ...sectionTitle, marginBottom: 0 }}>Route Map</h2>
        {activity.best_efforts && activity.best_efforts.length > 0 && (
          <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', alignItems: 'center' }}>
            <span style={{ fontSize: '11px', color: '#666', marginRight: '4px' }}>Fastest:</span>
            <button
              onClick={() => setHighlightDist(null)}
              style={{
                padding: '4px 10px', borderRadius: '4px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', border: 'none',
                backgroundColor: highlightDist === null ? '#fc5200' : '#16213e',
                color: highlightDist === null ? '#fff' : '#a0a0b0',
              }}>
              Full
            </button>
            {activity.best_efforts.map(be => (
              <button
                key={be.distance_target}
                onClick={() => setHighlightDist(highlightDist === be.distance_target ? null : be.distance_target)}
                style={{
                  padding: '4px 10px', borderRadius: '4px', fontSize: '11px', fontWeight: 600, cursor: 'pointer', border: 'none',
                  backgroundColor: highlightDist === be.distance_target ? '#4ade80' : '#16213e',
                  color: highlightDist === be.distance_target ? '#0a2a0a' : '#a0a0b0',
                }}>
                {formatDistLabel(be.distance_target)}
              </button>
            ))}
          </div>
        )}
      </div>
      <div style={{ marginBottom: '32px' }}>
        <RouteMap
          latlng={activity.streams?.find(s => s.stream_type === 'latlng')?.data}
          polyline={activity.map_summary_polyline}
          height={350}
          highlight={highlightDist && activity.best_efforts ? (() => {
            const be = activity.best_efforts.find(e => e.distance_target === highlightDist);
            return be && be.start_index != null ? { startIndex: be.start_index, endIndex: be.end_index } : null;
          })() : null}
        />
        {highlightDist && activity.best_efforts && (() => {
          const be = activity.best_efforts.find(e => e.distance_target === highlightDist);
          if (!be) return null;
          return (
            <div style={{ marginTop: '8px', fontSize: '13px', color: '#a0a0b0', textAlign: 'center' }}>
              Fastest {formatDistLabel(highlightDist)}: <span style={{ color: '#4ade80', fontWeight: 600 }}>{formatTime(be.time_seconds)}</span>
              <span style={{ marginLeft: '8px', color: '#666' }}>({formatPaceSeconds(be.pace_sec_per_km)}/km)</span>
            </div>
          );
        })()}
      </div>

      {activity.splits && activity.splits.length > 0 && (
        <>
          <h2 style={sectionTitle}>Splits</h2>
          <SplitsTable splits={activity.splits} />
        </>
      )}
    </div>
  );
}

export default ActivityDetail;
