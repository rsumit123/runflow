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
  const [intervals, setIntervals] = useState(null);
  const [intervalsLoading, setIntervalsLoading] = useState(false);
  const [showIntervalForm, setShowIntervalForm] = useState(false);
  const [repCount, setRepCount] = useState(3);
  const [repDistance, setRepDistance] = useState(400);
  const [laps, setLaps] = useState(null);
  const [insight, setInsight] = useState(null);
  const [isInterval, setIsInterval] = useState(false);

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
        setIsInterval(res.data.is_interval || false);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.response?.data?.detail || 'Failed to load activity');
        setLoading(false);
      });

    api.get(`/activities/${id}/insights`)
      .then((res) => { if (res.data.narratives?.length) setInsight(res.data); })
      .catch(() => {});

    api.get(`/activities/${id}/laps`)
      .then((res) => { if (res.data.lap_count >= 2) setLaps(res.data); })
      .catch(() => {});

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
        {isInterval && (
          <span style={{ marginLeft: '8px', color: '#fbbf24', backgroundColor: '#fbbf2415', padding: '2px 10px', borderRadius: '10px', fontSize: '12px', fontWeight: 600 }}>
            Interval
          </span>
        )}
      </div>
      {activity.gps_glitch_count > 0 && (
        <div style={{
          backgroundColor: '#3d2515', border: '1px solid #6b4020', borderRadius: '8px',
          padding: '10px 14px', marginBottom: '16px', fontSize: '12px', color: '#fbbf24',
          display: 'flex', alignItems: 'center', gap: '8px',
        }}>
          <span style={{ fontSize: '16px' }}>&#9888;</span>
          <span>GPS signal issues detected ({activity.gps_glitch_count} glitch{activity.gps_glitch_count > 1 ? 'es' : ''}). Some best effort times may have been filtered to ensure accuracy.</span>
        </div>
      )}

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

      {/* Run Insight */}
      {insight && insight.narratives.length > 0 && (
        <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', marginBottom: '24px' }}>
          <h2 style={{ ...sectionTitle, marginBottom: '12px', fontSize: '16px' }}>Run Insight</h2>
          {insight.narratives.map((n, i) => (
            <p key={i} style={{ color: '#e0e0e0', fontSize: '13px', lineHeight: 1.6, marginBottom: '6px' }}>{n}</p>
          ))}
          {/* Pacing flow with history comparison */}
          {insight.pace_segments && insight.pace_segments.length > 0 && (
            <div>
              <div style={{ display: 'flex', gap: '4px', margin: '14px 0 6px' }}>
                {insight.pace_segments.map((seg, i) => {
                  const allPaces = insight.pace_segments.map(s => s.pace);
                  const allHistPaces = insight.pace_segments.filter(s => s.history_avg).map(s => s.history_avg);
                  const allValues = [...allPaces, ...allHistPaces];
                  const minPace = Math.min(...allValues);
                  const maxPace = Math.max(...allValues);
                  const range = maxPace - minPace || 1;
                  const height = 24 + ((maxPace - seg.pace) / range) * 30;
                  const histHeight = seg.history_avg ? 24 + ((maxPace - seg.history_avg) / range) * 30 : 0;
                  const isFaster = seg.history_avg && seg.pace < seg.history_avg - 3;
                  const isSlower = seg.history_avg && seg.pace > seg.history_avg + 3;
                  return (
                    <div key={i} style={{ flex: 1, textAlign: 'center' }}>
                      <div style={{ fontSize: '11px', color: isFaster ? '#4ade80' : isSlower ? '#ff6b6b' : '#a0a0b0', fontWeight: 600, marginBottom: '3px' }}>
                        {seg.pace_formatted}
                      </div>
                      <div style={{ display: 'flex', gap: '2px', justifyContent: 'center', alignItems: 'flex-end', height: '56px' }}>
                        <div style={{ width: '40%', height: `${height}px`, borderRadius: '2px 2px 0 0', backgroundColor: isFaster ? '#4ade80' : isSlower ? '#ff6b6b55' : '#fc520077' }} />
                        {seg.history_avg && (
                          <div style={{ width: '40%', height: `${histHeight}px`, borderRadius: '2px 2px 0 0', backgroundColor: '#ffffff15', border: '1px solid #ffffff22' }} />
                        )}
                      </div>
                      <div style={{ fontSize: '9px', color: '#555', marginTop: '3px' }}>{seg.label}</div>
                    </div>
                  );
                })}
              </div>
              {insight.pace_segments.some(s => s.history_avg) && (
                <div style={{ display: 'flex', gap: '10px', justifyContent: 'center', fontSize: '10px', color: '#555', marginBottom: '4px' }}>
                  <span><span style={{ display: 'inline-block', width: '8px', height: '8px', backgroundColor: '#fc520077', borderRadius: '2px', marginRight: '4px' }}/>Today</span>
                  <span><span style={{ display: 'inline-block', width: '8px', height: '8px', backgroundColor: '#ffffff15', border: '1px solid #ffffff22', borderRadius: '2px', marginRight: '4px' }}/>Recent avg</span>
                </div>
              )}
            </div>
          )}
          {insight.tips.length > 0 && (
            <div style={{ marginTop: '12px', borderTop: '1px solid #252540', paddingTop: '10px' }}>
              {insight.tips.map((t, i) => (
                <div key={i} style={{ display: 'flex', gap: '6px', marginBottom: '6px', fontSize: '12px' }}>
                  <span style={{ color: '#fbbf24', flexShrink: 0 }}>Tip:</span>
                  <span style={{ color: '#a0a0b0' }}>{t}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

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
                <div>
                  <span style={{ color: '#e0e0e0', fontWeight: 600 }}>{formatDistanceLabel(effort.distance)}</span>
                  <span style={{
                    marginLeft: '4px', fontSize: '8px', fontWeight: 600, padding: '1px 5px', borderRadius: '3px', verticalAlign: 'middle',
                    backgroundColor: effort.is_dedicated ? '#4ade8015' : '#fc520015',
                    color: effort.is_dedicated ? '#4ade80' : '#fc5200',
                  }}>
                    {effort.is_dedicated ? 'Sprint' : 'Seg'}
                  </span>
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

      {/* Laps */}
      {laps && laps.lap_count >= 2 && (
        <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', marginBottom: '24px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <h2 style={{ ...sectionTitle, marginBottom: 0 }}>
              Laps <span style={{ fontSize: '13px', color: '#666', fontWeight: 400, marginLeft: '8px' }}>~{laps.avg_lap_distance_m}m loop</span>
            </h2>
            <div style={{ fontSize: '12px', color: '#a0a0b0' }}>
              Avg: <span style={{ color: '#fc5200', fontWeight: 600 }}>{formatTime(laps.stats.avg_lap_time)}</span>
              <span style={{ color: '#555', margin: '0 4px' }}>|</span>
              {formatPaceSeconds(laps.stats.avg_pace)}/km
            </div>
          </div>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {laps.laps.map((lap) => {
              const isFastest = lap.lap_number === laps.stats.fastest_lap;
              const isSlowest = lap.lap_number === laps.stats.slowest_lap;
              const diff = lap.duration_s - laps.stats.fastest_time;
              return (
                <div key={lap.lap_number} style={{
                  backgroundColor: isFastest ? '#4ade8012' : isSlowest ? '#ff6b6b08' : '#16213e',
                  border: isFastest ? '1px solid #4ade8033' : isSlowest ? '1px solid #ff6b6b22' : '1px solid #252540',
                  borderRadius: '6px', padding: '8px 12px', textAlign: 'center',
                  minWidth: '70px', flex: '0 0 auto',
                }}>
                  <div style={{ fontSize: '10px', color: '#666' }}>Lap {lap.lap_number}</div>
                  <div style={{ fontSize: '16px', fontWeight: 700, color: isFastest ? '#4ade80' : isSlowest ? '#ff6b6b' : '#e0e0e0' }}>
                    {formatTime(lap.duration_s)}
                  </div>
                  <div style={{ fontSize: '10px', color: '#555' }}>
                    {diff === 0 ? 'Best' : `+${diff}s`}
                  </div>
                </div>
              );
            })}
            {laps.partial_lap && (
              <div style={{ backgroundColor: '#16213e', border: '1px solid #252540', borderRadius: '6px', padding: '8px 12px', textAlign: 'center', minWidth: '70px', opacity: 0.5 }}>
                <div style={{ fontSize: '10px', color: '#666' }}>Partial</div>
                <div style={{ fontSize: '16px', fontWeight: 700, color: '#666' }}>{formatTime(laps.partial_lap.duration_s)}</div>
                <div style={{ fontSize: '10px', color: '#555' }}>{laps.partial_lap.distance_m}m</div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Interval Analysis */}
      {activity.has_detailed_data && !intervals && (
        <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', marginBottom: '24px' }}>
          {!showIntervalForm ? (
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
              {isInterval ? (
                <>
                  <span style={{ color: '#fbbf24', fontSize: '14px' }}>Tagged as interval run — excluded from pace averages</span>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button onClick={() => setShowIntervalForm(true)}
                      style={{ padding: '8px 14px', borderRadius: '6px', border: 'none', backgroundColor: '#fc5200', color: '#fff', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}>
                      Analyze reps
                    </button>
                    <button onClick={async () => {
                      await api.post(`/activities/${id}/toggle-interval`);
                      setIsInterval(false);
                    }}
                      style={{ padding: '8px 14px', borderRadius: '6px', border: '1px solid #333', backgroundColor: 'transparent', color: '#666', fontSize: '12px', cursor: 'pointer' }}>
                      Untag
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <span style={{ color: '#a0a0b0', fontSize: '14px' }}>Was this an interval run?</span>
                  <button onClick={() => setShowIntervalForm(true)}
                    style={{ padding: '8px 16px', borderRadius: '6px', border: 'none', backgroundColor: '#fc5200', color: '#fff', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>
                    Yes, analyze
                  </button>
                </>
              )}
            </div>
          ) : (
            <div>
              <div style={{ fontSize: '14px', color: '#fff', fontWeight: 600, marginBottom: '12px' }}>Describe your workout</div>
              <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '12px' }}>
                <div>
                  <div style={{ fontSize: '11px', color: '#666', marginBottom: '4px' }}>Reps</div>
                  <div style={{ display: 'flex', gap: '4px' }}>
                    {[2, 3, 4, 5, 6, 8, 10].map(n => (
                      <button key={n} onClick={() => setRepCount(n)}
                        style={{
                          width: '36px', height: '36px', borderRadius: '6px', border: 'none', fontSize: '14px', fontWeight: 600, cursor: 'pointer',
                          backgroundColor: repCount === n ? '#fc5200' : '#16213e',
                          color: repCount === n ? '#fff' : '#a0a0b0',
                        }}>
                        {n}
                      </button>
                    ))}
                  </div>
                </div>
                <div style={{ color: '#333', fontSize: '20px', alignSelf: 'flex-end', paddingBottom: '4px' }}>x</div>
                <div>
                  <div style={{ fontSize: '11px', color: '#666', marginBottom: '4px' }}>Distance</div>
                  <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                    {[100, 200, 250, 400, 500, 800, 1000].map(d => (
                      <button key={d} onClick={() => setRepDistance(d)}
                        style={{
                          padding: '8px 12px', borderRadius: '6px', border: 'none', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                          backgroundColor: repDistance === d ? '#fc5200' : '#16213e',
                          color: repDistance === d ? '#fff' : '#a0a0b0',
                        }}>
                        {d >= 1000 ? `${d/1000}km` : `${d}m`}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  onClick={async () => {
                    setIntervalsLoading(true);
                    try {
                      const res = await api.get(`/activities/${id}/intervals?reps=${repCount}&distance=${repDistance}`);
                      setIntervals(res.data);
                      // Auto-tag as interval if analysis succeeded
                      if (res.data.is_interval && !isInterval) {
                        await api.post(`/activities/${id}/toggle-interval`);
                        setIsInterval(true);
                      }
                    } catch {
                      setIntervals({ is_interval: false, message: 'Failed to analyze intervals' });
                    }
                    setIntervalsLoading(false);
                  }}
                  disabled={intervalsLoading}
                  style={{ padding: '10px 20px', borderRadius: '6px', border: 'none', backgroundColor: '#fc5200', color: '#fff', fontSize: '13px', fontWeight: 600, cursor: 'pointer', opacity: intervalsLoading ? 0.6 : 1 }}>
                  {intervalsLoading ? 'Analyzing...' : `Analyze ${repCount} x ${repDistance >= 1000 ? `${repDistance/1000}km` : `${repDistance}m`}`}
                </button>
                <button onClick={() => setShowIntervalForm(false)}
                  style={{ padding: '10px 14px', borderRadius: '6px', border: '1px solid #333', backgroundColor: 'transparent', color: '#666', fontSize: '13px', cursor: 'pointer' }}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Interval Results */}
      {intervals && intervals.is_interval && (
        <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', marginBottom: '24px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
            <h2 style={{ ...sectionTitle, marginBottom: 0 }}>Interval Breakdown</h2>
            <button onClick={() => { setIntervals(null); setShowIntervalForm(false); }}
              style={{ padding: '4px 10px', borderRadius: '4px', border: '1px solid #333', backgroundColor: 'transparent', color: '#666', fontSize: '11px', cursor: 'pointer' }}>
              Close
            </button>
          </div>
          {/* Summary */}
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '16px' }}>
            <div style={{ backgroundColor: '#16213e', borderRadius: '6px', padding: '10px 14px', textAlign: 'center', flex: '1 1 80px' }}>
              <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Workout</div>
              <div style={{ fontSize: '18px', fontWeight: 700, color: '#fc5200' }}>{intervals.summary.total_reps} x {intervals.summary.rep_distance_m}m</div>
            </div>
            <div style={{ backgroundColor: '#16213e', borderRadius: '6px', padding: '10px 14px', textAlign: 'center', flex: '1 1 80px' }}>
              <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Avg Rep Pace</div>
              <div style={{ fontSize: '18px', fontWeight: 700, color: '#fc5200' }}>{formatPaceSeconds(intervals.summary.avg_rep_pace)}/km</div>
            </div>
            <div style={{ backgroundColor: '#16213e', borderRadius: '6px', padding: '10px 14px', textAlign: 'center', flex: '1 1 80px' }}>
              <div style={{ fontSize: '10px', color: '#666', textTransform: 'uppercase' }}>Avg Rest</div>
              <div style={{ fontSize: '18px', fontWeight: 700, color: '#a0a0b0' }}>{formatTime(intervals.summary.avg_rest_duration_s)}</div>
            </div>
          </div>
          {/* Fastest/Slowest rep */}
          {intervals.summary.fastest_rep && intervals.summary.total_reps > 1 && (
            <div style={{ fontSize: '12px', color: '#a0a0b0', marginBottom: '14px' }}>
              Fastest: <span style={{ color: '#4ade80', fontWeight: 600 }}>Rep {intervals.summary.fastest_rep} ({formatPaceSeconds(intervals.summary.fastest_rep_pace)}/km)</span>
              {intervals.summary.slowest_rep !== intervals.summary.fastest_rep && (
                <span style={{ marginLeft: '12px' }}>
                  Slowest: <span style={{ color: '#ff6b6b', fontWeight: 600 }}>Rep {intervals.summary.slowest_rep} ({formatPaceSeconds(intervals.summary.slowest_rep_pace)}/km)</span>
                </span>
              )}
            </div>
          )}
          {/* Segments */}
          {intervals.segments.map((seg, i) => {
            const isRep = seg.type === 'rep';
            const isRest = seg.type === 'rest';
            const isFastest = isRep && seg.rep_number === intervals.summary.fastest_rep;
            const isSlowest = isRep && seg.rep_number === intervals.summary.slowest_rep && intervals.summary.total_reps > 1;
            const label = isRep ? `Rep ${seg.rep_number}`
              : isRest ? `Rest ${seg.rest_number}`
              : seg.type === 'warmup' ? 'Warmup' : 'Cooldown';
            return (
              <div key={i} style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '8px 12px', borderBottom: '1px solid #252540',
                backgroundColor: isFastest ? 'rgba(74,222,128,0.06)' : isSlowest ? 'rgba(255,107,107,0.06)' : 'transparent',
                fontSize: '13px',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: '80px' }}>
                  <div style={{
                    width: '8px', height: '8px', borderRadius: '50%',
                    backgroundColor: isRep ? '#fc5200' : isRest ? '#555' : '#333',
                  }} />
                  <span style={{ color: isRep ? '#fc5200' : '#666', fontWeight: isRep ? 600 : 400 }}>{label}</span>
                </div>
                <span style={{ color: '#e0e0e0' }}>{seg.distance_m}m</span>
                <span style={{ color: '#e0e0e0' }}>{formatTime(seg.duration_s)}</span>
                <span style={{ color: isFastest ? '#4ade80' : isSlowest ? '#ff6b6b' : isRep ? '#fc5200' : '#555', fontWeight: isRep ? 600 : 400 }}>
                  {seg.pace_sec_per_km ? `${formatPaceSeconds(seg.pace_sec_per_km)}/km` : '-'}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* Interval not detected */}
      {intervals && !intervals.is_interval && (
        <div style={{ backgroundColor: '#1a1a2e', borderRadius: '8px', padding: '16px', marginBottom: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ color: '#666', fontSize: '13px' }}>{intervals.message}</span>
          <button onClick={() => { setIntervals(null); setShowIntervalForm(true); }}
            style={{ padding: '4px 10px', borderRadius: '4px', border: '1px solid #333', backgroundColor: 'transparent', color: '#a0a0b0', fontSize: '11px', cursor: 'pointer' }}>
            Try again
          </button>
        </div>
      )}

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
