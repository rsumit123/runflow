import React from 'react';

// Without this, one render error blanks the whole app.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error) {
    console.error(error); // eslint-disable-line no-console
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" style={{ padding: 32, textAlign: 'center', color: '#e8edf2' }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, marginBottom: 12 }}>Something went wrong</h1>
        <button onClick={() => window.location.reload()} style={{
          minHeight: 44, padding: '0 18px', borderRadius: 6, border: 'none',
          backgroundColor: '#ff5a1f', color: '#0b0f14', fontSize: 14, fontWeight: 700, cursor: 'pointer',
        }}>Reload</button>
      </div>
    );
  }
}
