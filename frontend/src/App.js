import React, { useState, useEffect } from 'react';
import { Routes, Route, Link, useLocation } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import ActivityDetail from './pages/ActivityDetail';
import Import from './pages/Import';
import Phases from './pages/Phases';
import Stats from './pages/Stats';
import Training from './pages/Training';
import PlanWorkoutDetail from './pages/PlanWorkoutDetail';
import RoutesPage from './pages/Routes';
import Goals from './pages/Goals';
import Login from './pages/Login';
import Signup from './pages/Signup';
import Settings from './pages/Settings';
import { RequireAuth, useAuth } from './auth/AuthContext';
import { GarminWarningBanner } from './garminWarning';
import { errorText } from './errors';
import ErrorBoundary from './ErrorBoundary';

const navLinks = [
  { path: '/', label: 'Dashboard' },
  { path: '/stats', label: 'Stats' },
  { path: '/training', label: 'Training' },
  { path: '/goals', label: 'Goals' },
  { path: '/routes', label: 'Routes' },
  { path: '/phases', label: 'Phases' },
  { path: '/import', label: 'Import' },
  { path: '/settings', label: 'Settings' },
];

function App() {
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const { user, authError, logout } = useAuth();
  const onAuthPage = ['/login', '/signup'].includes(location.pathname);

  const [signOutError, setSignOutError] = useState('');
  const signOut = async () => {
    setMenuOpen(false);
    setSignOutError('');
    try {
      await logout();   // navigates to a plain /login on success
    } catch (err) {
      setSignOutError(errorText(err, "Couldn't sign out. You are still signed in. Try again."));
    }
  };

  // Close menu on navigation
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  const isActive = (path) => location.pathname === path;

  return (
    <div>
      {!onAuthPage && <nav style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 16px',
        backgroundColor: '#0b0f14',
        borderBottom: '1px solid #1e2936',
        position: 'relative',
      }}>
        <Link to="/" style={{
          fontSize: '18px',
          fontWeight: 700,
          color: '#ff5a1f',
          letterSpacing: '1px',
          whiteSpace: 'nowrap',
        }}>
          RunFlow
        </Link>

        {/* Desktop nav */}
        <div style={{ display: 'flex', gap: '16px' }} className="desktop-nav">
          {navLinks.map(({ path, label }) => (
            <Link key={path} to={path} style={{
              color: isActive(path) ? '#ff5a1f' : '#93a1b1',
              fontWeight: isActive(path) ? 600 : 400,
              fontSize: '14px',
            }}>
              {label}
            </Link>
          ))}
          {user && (
            <span style={{ display: 'flex', gap: '12px', alignItems: 'center', marginLeft: '8px' }}>
              <span style={{ color: '#93a1b1', fontSize: '13px' }}>{user.email}</span>
              <button onClick={signOut} style={{
                background: 'none', border: 'none', cursor: 'pointer', padding: 0,
                color: '#93a1b1', fontSize: '14px', textDecoration: 'underline',
              }}>Sign out</button>
            </span>
          )}
        </div>

        {/* Mobile hamburger */}
        <button
          onClick={() => setMenuOpen(!menuOpen)}
          className="mobile-menu-btn"
          aria-label="Menu"
          aria-expanded={menuOpen}
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
              display: 'block', height: '2px', backgroundColor: '#e8edf2', borderRadius: '1px',
              transition: 'transform 0.2s',
              transform: menuOpen ? 'rotate(45deg) translate(4px, 4px)' : 'none',
            }} />
            <span style={{
              display: 'block', height: '2px', backgroundColor: '#e8edf2', borderRadius: '1px',
              transition: 'opacity 0.2s',
              opacity: menuOpen ? 0 : 1,
            }} />
            <span style={{
              display: 'block', height: '2px', backgroundColor: '#e8edf2', borderRadius: '1px',
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
            backgroundColor: '#0b0f14',
            borderBottom: '1px solid #1e2936',
            zIndex: 100,
            display: 'none',
          }}>
            {navLinks.map(({ path, label }) => (
              <Link key={path} to={path} style={{
                display: 'block',
                padding: '14px 20px',
                color: isActive(path) ? '#ff5a1f' : '#e8edf2',
                fontWeight: isActive(path) ? 600 : 400,
                fontSize: '15px',
                borderBottom: '1px solid #1e2936',
              }}>
                {label}
              </Link>
            ))}
            {user && (
              <button onClick={signOut} style={{
                display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none',
                padding: '14px 20px', color: '#e8edf2', fontSize: '15px', cursor: 'pointer',
              }}>
                Sign out <span style={{ color: '#93a1b1', fontSize: '13px', wordBreak: 'break-all' }}>({user.email})</span>
              </button>
            )}
          </div>
        )}
      </nav>}

      {!onAuthPage && (authError || signOutError) && user && (
        <div role="alert" style={{ padding: '8px 16px', backgroundColor: '#2b2110', color: '#f5a623', fontSize: 13 }}>
          {signOutError || "Can't reach RunFlow right now."}
        </div>
      )}
      <GarminWarningBanner />
      <div style={{
        maxWidth: '1100px',
        margin: '0 auto',
        padding: '20px 12px',
      }}>
        <ErrorBoundary key={location.pathname}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/signup" element={<Signup />} />
          <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
          <Route path="/" element={<RequireAuth><Dashboard /></RequireAuth>} />
          <Route path="/activity/:id" element={<RequireAuth><ActivityDetail /></RequireAuth>} />
          <Route path="/stats" element={<RequireAuth><Stats /></RequireAuth>} />
          <Route path="/training" element={<RequireAuth><Training /></RequireAuth>} />
          <Route path="/plan/workout/:id" element={<RequireAuth><PlanWorkoutDetail /></RequireAuth>} />
          <Route path="/goals" element={<RequireAuth><Goals /></RequireAuth>} />
          <Route path="/routes" element={<RequireAuth><RoutesPage /></RequireAuth>} />
          <Route path="/phases" element={<RequireAuth><Phases /></RequireAuth>} />
          <Route path="/import" element={<RequireAuth><Import /></RequireAuth>} />
        </Routes>
        </ErrorBoundary>
      </div>
    </div>
  );
}

export default App;
