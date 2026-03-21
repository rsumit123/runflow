import React, { useState, useEffect, useRef } from 'react';
import api from '../api';

const pageTitle = {
  fontSize: '28px',
  fontWeight: 700,
  color: '#ffffff',
  marginBottom: '8px',
};

const subtitle = {
  fontSize: '14px',
  color: '#a0a0b0',
  marginBottom: '32px',
};

const cardStyle = {
  backgroundColor: '#1a1a2e',
  borderRadius: '8px',
  padding: '28px',
  marginBottom: '20px',
};

const cardTitle = {
  fontSize: '18px',
  fontWeight: 600,
  color: '#ffffff',
  marginBottom: '8px',
};

const cardDesc = {
  fontSize: '14px',
  color: '#a0a0b0',
  marginBottom: '20px',
  lineHeight: '1.5',
};

const buttonBase = {
  padding: '12px 28px',
  borderRadius: '6px',
  border: 'none',
  fontSize: '14px',
  fontWeight: 600,
  cursor: 'pointer',
  transition: 'opacity 0.2s',
};

const primaryButton = {
  ...buttonBase,
  backgroundColor: '#fc5200',
  color: '#ffffff',
};

const secondaryButton = {
  ...buttonBase,
  backgroundColor: '#16213e',
  color: '#e0e0e0',
  border: '1px solid #333',
};

const connectButton = {
  ...buttonBase,
  backgroundColor: '#fc5200',
  color: '#ffffff',
  fontSize: '15px',
  padding: '14px 32px',
};

const disabledButton = {
  ...buttonBase,
  backgroundColor: '#333',
  color: '#666',
  cursor: 'not-allowed',
};

const statusBox = {
  marginTop: '20px',
  padding: '16px',
  borderRadius: '6px',
  fontSize: '14px',
};

const successStatus = {
  ...statusBox,
  backgroundColor: '#0d3320',
  border: '1px solid #1a6b40',
  color: '#4ade80',
};

const errorStatus = {
  ...statusBox,
  backgroundColor: '#3d1515',
  border: '1px solid #6b2020',
  color: '#ff6b6b',
};

const loadingStatus = {
  ...statusBox,
  backgroundColor: '#1a1a2e',
  border: '1px solid #333',
  color: '#a0a0b0',
};

const spinnerStyle = {
  display: 'inline-block',
  marginRight: '8px',
};

const inputStyle = {
  padding: '10px 14px',
  borderRadius: '6px',
  border: '1px solid #333',
  backgroundColor: '#16213e',
  color: '#e0e0e0',
  fontSize: '14px',
  width: '100%',
  maxWidth: '500px',
  marginBottom: '12px',
  boxSizing: 'border-box',
};

const progressBarContainer = {
  width: '100%',
  backgroundColor: '#16213e',
  borderRadius: '4px',
  height: '8px',
  marginTop: '12px',
  overflow: 'hidden',
};

const progressBarFill = (pct) => ({
  width: `${pct}%`,
  backgroundColor: '#fc5200',
  height: '100%',
  borderRadius: '4px',
  transition: 'width 0.3s ease',
});

function Import() {
  const [importStatus, setImportStatus] = useState(null);
  const [importLoading, setImportLoading] = useState(false);
  const [importJobId, setImportJobId] = useState(null);
  const [importProgress, setImportProgress] = useState(null);
  const [syncStatus, setSyncStatus] = useState(null);
  const [syncLoading, setSyncLoading] = useState(false);
  const [authUrl, setAuthUrl] = useState(null);
  const [authLoading, setAuthLoading] = useState(false);

  // Bulk import state
  const [bulkDir, setBulkDir] = useState('');
  const [bulkLoading, setBulkLoading] = useState(false);
  const [bulkStatus, setBulkStatus] = useState(null);
  const [bulkJobId, setBulkJobId] = useState(null);
  const [bulkProgress, setBulkProgress] = useState(null);

  const pollRef = useRef(null);
  const bulkPollRef = useRef(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (bulkPollRef.current) clearInterval(bulkPollRef.current);
    };
  }, []);

  // Poll for API import progress
  useEffect(() => {
    if (!importJobId) return;

    const poll = async () => {
      try {
        const res = await api.get(`/import/status?job_id=${importJobId}`);
        const job = res.data.job;
        if (job) {
          setImportProgress(job);
          if (job.status === 'completed') {
            setImportLoading(false);
            setImportStatus({
              type: 'success',
              message: `Successfully imported ${job.imported} activities (${job.skipped || 0} non-running skipped, ${job.errors || 0} errors).`,
            });
            setImportJobId(null);
            if (pollRef.current) clearInterval(pollRef.current);
          } else if (job.status === 'error') {
            setImportLoading(false);
            setImportStatus({
              type: 'error',
              message: job.error || 'Import failed.',
            });
            setImportJobId(null);
            if (pollRef.current) clearInterval(pollRef.current);
          }
        }
      } catch (err) {
        // Ignore poll errors
      }
    };

    pollRef.current = setInterval(poll, 2000);
    poll(); // immediate first poll

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [importJobId]);

  // Poll for bulk import progress
  useEffect(() => {
    if (!bulkJobId) return;

    const poll = async () => {
      try {
        const res = await api.get(`/import/status?job_id=${bulkJobId}`);
        const job = res.data.job;
        if (job) {
          setBulkProgress(job);
          if (job.status === 'completed') {
            setBulkLoading(false);
            setBulkStatus({
              type: 'success',
              message: `Imported ${job.imported} running activities from export (${job.already_exists || 0} already existed, ${job.total_in_csv || job.total || 0} total in CSV).`,
            });
            setBulkJobId(null);
            if (bulkPollRef.current) clearInterval(bulkPollRef.current);
          } else if (job.status === 'error') {
            setBulkLoading(false);
            setBulkStatus({
              type: 'error',
              message: job.error || 'Bulk import failed.',
            });
            setBulkJobId(null);
            if (bulkPollRef.current) clearInterval(bulkPollRef.current);
          }
        }
      } catch (err) {
        // Ignore poll errors
      }
    };

    bulkPollRef.current = setInterval(poll, 2000);
    poll();

    return () => {
      if (bulkPollRef.current) clearInterval(bulkPollRef.current);
    };
  }, [bulkJobId]);

  const handleConnect = async () => {
    setAuthLoading(true);
    try {
      const res = await api.get('/auth/url');
      const url = res.data.url || res.data;
      if (typeof url === 'string' && url.startsWith('http')) {
        window.location.href = url;
      } else {
        setAuthUrl(JSON.stringify(res.data));
      }
    } catch (err) {
      setAuthUrl('Error: ' + (err.response?.data?.detail || 'Failed to get auth URL'));
    }
    setAuthLoading(false);
  };

  const handleImportAll = async () => {
    setImportLoading(true);
    setImportStatus(null);
    setImportProgress(null);
    try {
      const res = await api.post('/import/all');
      if (res.data.job_id) {
        setImportJobId(res.data.job_id);
      } else {
        // Fallback for non-background response
        setImportStatus({
          type: 'success',
          message: `Successfully imported ${res.data.count ?? res.data.imported ?? 'all'} activities.`,
          data: res.data,
        });
        setImportLoading(false);
      }
    } catch (err) {
      setImportStatus({
        type: 'error',
        message: err.response?.data?.detail || 'Failed to import activities.',
      });
      setImportLoading(false);
    }
  };

  const handleSync = async () => {
    setSyncLoading(true);
    setSyncStatus(null);
    try {
      const res = await api.post('/import/sync');
      setSyncStatus({
        type: 'success',
        message: `Synced ${res.data.count ?? res.data.imported ?? res.data.synced ?? 0} new activities.`,
        data: res.data,
      });
    } catch (err) {
      setSyncStatus({
        type: 'error',
        message: err.response?.data?.detail || 'Failed to sync activities.',
      });
    }
    setSyncLoading(false);
  };

  const handleBulkImport = async () => {
    if (!bulkDir.trim()) return;
    setBulkLoading(true);
    setBulkStatus(null);
    setBulkProgress(null);
    try {
      const res = await api.post('/import/bulk', { directory: bulkDir.trim() });
      if (res.data.job_id) {
        setBulkJobId(res.data.job_id);
      } else {
        setBulkStatus({
          type: 'success',
          message: `Imported ${res.data.imported || 0} activities from export.`,
        });
        setBulkLoading(false);
      }
    } catch (err) {
      setBulkStatus({
        type: 'error',
        message: err.response?.data?.detail || 'Failed to import from export.',
      });
      setBulkLoading(false);
    }
  };

  const renderProgressBar = (progress) => {
    if (!progress) return null;
    const total = progress.total || 0;
    const current = progress.current || progress.imported || 0;
    const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;

    return (
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '8px', fontSize: '13px', color: '#a0a0b0' }}>
          <span>{current} / {total > 0 ? total : '?'} activities processed</span>
          <span>{total > 0 ? `${pct}%` : ''}</span>
        </div>
        {total > 0 && (
          <div style={progressBarContainer}>
            <div style={progressBarFill(pct)} />
          </div>
        )}
      </div>
    );
  };

  return (
    <div>
      <h1 style={pageTitle}>Import Activities</h1>
      <p style={subtitle}>Connect your Strava account and import your running activities.</p>

      <div style={cardStyle}>
        <h2 style={cardTitle}>1. Connect to Strava</h2>
        <p style={cardDesc}>
          Authorize this app to access your Strava data. You'll be redirected to Strava to grant
          permission.
        </p>
        <button
          style={authLoading ? disabledButton : connectButton}
          onClick={handleConnect}
          disabled={authLoading}
        >
          {authLoading ? 'Connecting...' : 'Connect with Strava'}
        </button>
        {authUrl && (
          <div style={{ ...statusBox, backgroundColor: '#16213e', color: '#a0a0b0', marginTop: '12px' }}>
            {authUrl}
          </div>
        )}
      </div>

      <div style={cardStyle}>
        <h2 style={cardTitle}>2. Import All Activities (API)</h2>
        <p style={cardDesc}>
          Fetch and store all your running activities from Strava via the API. This runs in the
          background and may take a while if you have many activities.
        </p>
        <button
          style={importLoading ? disabledButton : primaryButton}
          onClick={handleImportAll}
          disabled={importLoading}
        >
          {importLoading ? (
            <>
              <span style={spinnerStyle}>&#9696;</span>
              Importing...
            </>
          ) : (
            'Import All Activities'
          )}
        </button>
        {importLoading && (
          <div style={loadingStatus}>
            Importing activities from Strava in the background...
            {importProgress && renderProgressBar({
              current: importProgress.imported || 0,
              total: 0, // API import doesn't know total upfront
            })}
            {importProgress && (
              <div style={{ marginTop: '8px', fontSize: '13px' }}>
                Imported: {importProgress.imported || 0} | Skipped: {importProgress.skipped || 0} | Page: {importProgress.current_page || 0}
              </div>
            )}
          </div>
        )}
        {importStatus && (
          <div style={importStatus.type === 'success' ? successStatus : errorStatus}>
            {importStatus.message}
          </div>
        )}
      </div>

      <div style={cardStyle}>
        <h2 style={cardTitle}>3. Sync New Activities</h2>
        <p style={cardDesc}>
          Fetch only new activities since your last import. Faster than a full import.
        </p>
        <button
          style={syncLoading ? disabledButton : secondaryButton}
          onClick={handleSync}
          disabled={syncLoading}
        >
          {syncLoading ? (
            <>
              <span style={spinnerStyle}>&#9696;</span>
              Syncing...
            </>
          ) : (
            'Sync New Activities'
          )}
        </button>
        {syncLoading && (
          <div style={loadingStatus}>
            Checking for new activities...
          </div>
        )}
        {syncStatus && (
          <div style={syncStatus.type === 'success' ? successStatus : errorStatus}>
            {syncStatus.message}
          </div>
        )}
      </div>

      <div style={cardStyle}>
        <h2 style={cardTitle}>4. Bulk Import from Strava Export</h2>
        <p style={cardDesc}>
          Import activities from a Strava data export archive. Go to Strava Settings &rarr;
          My Account &rarr; "Download or Delete Your Account" &rarr; "Request Your Archive".
          Once downloaded, extract the ZIP and provide the path below, or provide the path to the ZIP file directly.
        </p>
        <div style={{ marginBottom: '12px' }}>
          <input
            type="text"
            placeholder="/path/to/strava-export or /path/to/export.zip"
            value={bulkDir}
            onChange={(e) => setBulkDir(e.target.value)}
            style={inputStyle}
          />
        </div>
        <button
          style={bulkLoading || !bulkDir.trim() ? disabledButton : secondaryButton}
          onClick={handleBulkImport}
          disabled={bulkLoading || !bulkDir.trim()}
        >
          {bulkLoading ? (
            <>
              <span style={spinnerStyle}>&#9696;</span>
              Importing from export...
            </>
          ) : (
            'Import from Export'
          )}
        </button>
        {bulkLoading && bulkProgress && (
          <div style={loadingStatus}>
            Importing activities from export...
            {renderProgressBar(bulkProgress)}
          </div>
        )}
        {bulkStatus && (
          <div style={bulkStatus.type === 'success' ? successStatus : errorStatus}>
            {bulkStatus.message}
          </div>
        )}
      </div>
    </div>
  );
}

export default Import;
