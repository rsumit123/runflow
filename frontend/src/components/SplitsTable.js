import React, { useMemo } from 'react';

const tableWrapper = {
  borderRadius: '8px',
  overflow: 'hidden',
  marginBottom: '32px',
  overflowX: 'auto',
  WebkitOverflowScrolling: 'touch',
};

const tableStyle = {
  width: '100%',
  borderCollapse: 'collapse',
  backgroundColor: '#1a1a2e',
  minWidth: '420px',
};

const thStyle = {
  textAlign: 'left',
  padding: '14px 16px',
  backgroundColor: '#16213e',
  color: '#a0a0b0',
  fontSize: '12px',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '1px',
};

const tdBase = {
  padding: '12px 16px',
  borderBottom: '1px solid #252540',
  fontSize: '14px',
};

function formatPace(movingTimeSeconds, distanceMeters) {
  if (!movingTimeSeconds || !distanceMeters) return '-';
  const km = distanceMeters / 1000;
  if (km === 0) return '-';
  const paceSeconds = movingTimeSeconds / km;
  const mins = Math.floor(paceSeconds / 60);
  const secs = Math.round(paceSeconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function getPaceSeconds(split) {
  const distance = split.distance || 0;
  const time = split.moving_time || split.elapsed_time || 0;
  if (!distance || !time) return Infinity;
  const km = distance / 1000;
  return time / km;
}

function formatDistance(meters) {
  if (!meters) return '-';
  const km = meters / 1000;
  return `${km.toFixed(2)} km`;
}

function formatElevation(elevDiff) {
  if (elevDiff == null) return '-';
  const m = Math.round(elevDiff);
  const sign = m >= 0 ? '+' : '';
  return `${sign}${m} m`;
}

function SplitsTable({ splits }) {
  const { fastestIdx, slowestIdx } = useMemo(() => {
    if (!splits || splits.length === 0) return { fastestIdx: -1, slowestIdx: -1 };

    let fastest = Infinity;
    let slowest = -Infinity;
    let fastestI = -1;
    let slowestI = -1;

    splits.forEach((split, i) => {
      const pace = getPaceSeconds(split);
      if (pace !== Infinity && pace < fastest) {
        fastest = pace;
        fastestI = i;
      }
      if (pace !== Infinity && pace > slowest) {
        slowest = pace;
        slowestI = i;
      }
    });

    return { fastestIdx: fastestI, slowestIdx: slowestI };
  }, [splits]);

  if (!splits || splits.length === 0) {
    return (
      <div style={{ color: '#666', padding: '20px', textAlign: 'center' }}>
        No splits data available.
      </div>
    );
  }

  const getRowStyle = (index) => {
    let bg = 'transparent';
    if (index === fastestIdx) {
      bg = 'rgba(74, 222, 128, 0.08)';
    } else if (index === slowestIdx) {
      bg = 'rgba(255, 107, 107, 0.08)';
    }
    return { backgroundColor: bg };
  };

  const getPaceStyle = (index) => {
    if (index === fastestIdx) {
      return { color: '#4ade80', fontWeight: 600 };
    }
    if (index === slowestIdx) {
      return { color: '#ff6b6b', fontWeight: 600 };
    }
    return {};
  };

  return (
    <div style={tableWrapper}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Split #</th>
            <th style={thStyle}>Distance</th>
            <th style={thStyle}>Pace (min/km)</th>
            <th style={thStyle}>Elevation Change</th>
          </tr>
        </thead>
        <tbody>
          {splits.map((split, index) => (
            <tr key={index} style={getRowStyle(index)}>
              <td style={tdBase}>{split.split_number || split.split || index + 1}</td>
              <td style={tdBase}>{formatDistance(split.distance)}</td>
              <td style={{ ...tdBase, ...getPaceStyle(index) }}>
                {formatPace(split.moving_time || split.elapsed_time, split.distance)} /km
                {index === fastestIdx && (
                  <span style={{ fontSize: '11px', marginLeft: '8px', opacity: 0.7 }}>
                    FASTEST
                  </span>
                )}
                {index === slowestIdx && (
                  <span style={{ fontSize: '11px', marginLeft: '8px', opacity: 0.7 }}>
                    SLOWEST
                  </span>
                )}
              </td>
              <td style={tdBase}>
                {formatElevation(split.elevation_difference ?? split.total_elevation_gain)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default SplitsTable;
