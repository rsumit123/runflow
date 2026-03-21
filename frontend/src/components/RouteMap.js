import React, { useMemo } from 'react';
import { MapContainer, TileLayer, Polyline, Marker, useMap } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Fix default marker icons
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
});

const startIcon = new L.DivIcon({
  html: '<div style="background:#4ade80;width:12px;height:12px;border-radius:50%;border:2px solid #fff;"></div>',
  className: '',
  iconSize: [12, 12],
  iconAnchor: [6, 6],
});

const endIcon = new L.DivIcon({
  html: '<div style="background:#ff6b6b;width:12px;height:12px;border-radius:50%;border:2px solid #fff;"></div>',
  className: '',
  iconSize: [12, 12],
  iconAnchor: [6, 6],
});

function FitBounds({ bounds }) {
  const map = useMap();
  React.useEffect(() => {
    if (bounds && bounds.length > 0) {
      map.fitBounds(bounds, { padding: [30, 30] });
    }
  }, [map, bounds]);
  return null;
}

/**
 * RouteMap component
 * @param {Array} latlng - Full GPS track [[lat,lng], ...]
 * @param {string} polyline - Encoded polyline (fallback if no latlng)
 * @param {number} height - Map height in px
 * @param {Object} highlight - { startIndex, endIndex } to highlight a segment
 */
function RouteMap({ latlng, polyline, height = 300, highlight }) {
  const positions = useMemo(() => {
    if (latlng && latlng.length > 0) return latlng;
    if (polyline) return decodePolyline(polyline);
    return [];
  }, [latlng, polyline]);

  const highlightPositions = useMemo(() => {
    if (!highlight || !positions || positions.length === 0) return null;
    const { startIndex, endIndex } = highlight;
    if (startIndex == null || endIndex == null) return null;
    if (startIndex >= positions.length || endIndex >= positions.length) return null;
    return positions.slice(startIndex, endIndex + 1);
  }, [highlight, positions]);

  if (!positions || positions.length < 2) {
    return (
      <div style={{
        backgroundColor: '#1a1a2e',
        borderRadius: '8px',
        height: `${height}px`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#555',
        fontSize: '14px',
        border: '1px dashed #333',
      }}>
        No GPS data available for this run
      </div>
    );
  }

  const startPos = positions[0];
  const endPos = positions[positions.length - 1];

  return (
    <div style={{ borderRadius: '8px', overflow: 'hidden', height: `${height}px` }}>
      <MapContainer
        center={startPos}
        zoom={15}
        style={{ height: '100%', width: '100%', background: '#0f0f1a' }}
        zoomControl={true}
        scrollWheelZoom={true}
      >
        <TileLayer
          attribution='&copy; <a href="https://carto.com/">CARTO</a>'
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        />
        {/* Base route - dimmed when highlight is active */}
        <Polyline
          positions={positions}
          pathOptions={{
            color: highlightPositions ? '#666' : '#fc5200',
            weight: highlightPositions ? 2 : 3,
            opacity: highlightPositions ? 0.4 : 0.9,
          }}
        />
        {/* Highlighted segment */}
        {highlightPositions && highlightPositions.length >= 2 && (
          <Polyline
            positions={highlightPositions}
            pathOptions={{ color: '#4ade80', weight: 5, opacity: 1 }}
          />
        )}
        <Marker position={startPos} icon={startIcon} />
        <Marker position={endPos} icon={endIcon} />
        <FitBounds bounds={highlightPositions || positions} />
      </MapContainer>
    </div>
  );
}

function decodePolyline(encoded) {
  const points = [];
  let index = 0;
  let lat = 0;
  let lng = 0;

  while (index < encoded.length) {
    let b, shift, result;

    shift = 0;
    result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lat += (result & 1) ? ~(result >> 1) : (result >> 1);

    shift = 0;
    result = 0;
    do {
      b = encoded.charCodeAt(index++) - 63;
      result |= (b & 0x1f) << shift;
      shift += 5;
    } while (b >= 0x20);
    lng += (result & 1) ? ~(result >> 1) : (result >> 1);

    points.push([lat / 1e5, lng / 1e5]);
  }

  return points;
}

export default RouteMap;
