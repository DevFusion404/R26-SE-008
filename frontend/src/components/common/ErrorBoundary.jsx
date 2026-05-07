/**
 * ErrorBoundary.jsx — React Error Boundary
 * Catches errors in components and displays fallback UI
 */

import { Component } from 'react';

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Error caught by boundary:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            padding: '20px',
            margin: '16px 0',
            background: 'rgba(239,68,68,0.08)',
            border: '1px solid rgba(239,68,68,0.2)',
            borderRadius: '8px',
          }}
        >
          <div style={{ fontSize: '14px', fontWeight: '600', color: 'rgba(220,38,38,1)', marginBottom: '8px' }}>
            ⚠️ Component Error
          </div>
          <div style={{ fontSize: '12px', color: 'rgba(220,38,38,0.8)', fontFamily: 'monospace' }}>
            {this.state.error?.message || 'An unexpected error occurred'}
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
