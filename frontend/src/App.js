import React from 'react';
import { Routes, Route, Link, useLocation } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import ActivityDetail from './pages/ActivityDetail';
import Import from './pages/Import';
import Phases from './pages/Phases';
import Stats from './pages/Stats';
import RoutesPage from './pages/Routes';

function App() {
  const location = useLocation();

  const isActive = (path) => location.pathname === path;

  return (
    <div>
      <nav style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 16px',
        backgroundColor: '#1a1a2e',
        borderBottom: '2px solid #fc5200',
      }}>
        <Link to="/" style={{
          fontSize: '18px',
          fontWeight: 700,
          color: '#fc5200',
          letterSpacing: '1px',
          whiteSpace: 'nowrap',
        }}>
          RunFlow
        </Link>
        <div style={{ display: 'flex', gap: '16px' }}>
          <Link to="/" style={{
            color: isActive('/') ? '#fc5200' : '#a0a0b0',
            fontWeight: isActive('/') ? 600 : 400,
            fontSize: '14px',
          }}>
            Dashboard
          </Link>
          <Link to="/stats" style={{
            color: isActive('/stats') ? '#fc5200' : '#a0a0b0',
            fontWeight: isActive('/stats') ? 600 : 400,
            fontSize: '14px',
          }}>
            Stats
          </Link>
          <Link to="/routes" style={{
            color: isActive('/routes') ? '#fc5200' : '#a0a0b0',
            fontWeight: isActive('/routes') ? 600 : 400,
            fontSize: '14px',
          }}>
            Routes
          </Link>
          <Link to="/phases" style={{
            color: isActive('/phases') ? '#fc5200' : '#a0a0b0',
            fontWeight: isActive('/phases') ? 600 : 400,
            fontSize: '14px',
          }}>
            Phases
          </Link>
          <Link to="/import" style={{
            color: isActive('/import') ? '#fc5200' : '#a0a0b0',
            fontWeight: isActive('/import') ? 600 : 400,
            fontSize: '14px',
          }}>
            Import
          </Link>
        </div>
      </nav>
      <div style={{
        maxWidth: '1100px',
        margin: '0 auto',
        padding: '20px 12px',
      }}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/activity/:id" element={<ActivityDetail />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/routes" element={<RoutesPage />} />
          <Route path="/phases" element={<Phases />} />
          <Route path="/import" element={<Import />} />
        </Routes>
      </div>
    </div>
  );
}

export default App;
