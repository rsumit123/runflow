import React, { useState, useEffect } from 'react';
import { Routes, Route, Link, useLocation } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import ActivityDetail from './pages/ActivityDetail';
import Import from './pages/Import';
import Phases from './pages/Phases';
import Stats from './pages/Stats';
import RoutesPage from './pages/Routes';

const navLinks = [
  { path: '/', label: 'Dashboard' },
  { path: '/stats', label: 'Stats' },
  { path: '/routes', label: 'Routes' },
  { path: '/phases', label: 'Phases' },
  { path: '/import', label: 'Import' },
];

function App() {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);

  // Close menu on navigation
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

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
        position: 'relative',
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

        {/* Desktop nav */}
        <div style={{ display: 'flex', gap: '16px' }} className="desktop-nav">
          {navLinks.map(({ path, label }) => (
            <Link key={path} to={path} style={{
              color: isActive(path) ? '#fc5200' : '#a0a0b0',
              fontWeight: isActive(path) ? 600 : 400,
              fontSize: '14px',
            }}>
              {label}
            </Link>
          ))}
        </div>

        {/* Mobile hamburger */}
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="mobile-menu-btn"
          style={{
            display: 'none',
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            padding: '4px',
          }}
        >
          <div style={{ width: '22px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{
              display: 'block', height: '2px', backgroundColor: '#e0e0e0', borderRadius: '1px',
              transition: 'transform 0.2s',
              transform: menuOpen ? 'rotate(45deg) translate(4px, 4px)' : 'none',
            }} />
            <span style={{
              display: 'block', height: '2px', backgroundColor: '#e0e0e0', borderRadius: '1px',
              transition: 'opacity 0.2s',
              opacity: menuOpen ? 0 : 1,
            }} />
            <span style={{
              display: 'block', height: '2px', backgroundColor: '#e0e0e0', borderRadius: '1px',
              transition: 'transform 0.2s',
              transform: menuOpen ? 'rotate(-45deg) translate(4px, -4px)' : 'none',
            }} />
          </div>
        </button>

        {/* Mobile dropdown */}
        {menuOpen && (
          <div className="mobile-dropdown" style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            backgroundColor: '#1a1a2e',
            borderBottom: '2px solid #fc5200',
            zIndex: 100,
            display: 'none',
          }}>
            {navLinks.map(({ path, label }) => (
              <Link key={path} to={path} style={{
                display: 'block',
                padding: '14px 20px',
                color: isActive(path) ? '#fc5200' : '#e0e0e0',
                fontWeight: isActive(path) ? 600 : 400,
                fontSize: '15px',
                borderBottom: '1px solid #252540',
              }}>
                {label}
              </Link>
            ))}
          </div>
        )}
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
