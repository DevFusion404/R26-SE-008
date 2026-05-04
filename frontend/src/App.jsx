/**
 * App.jsx — R26-SE-008 Main Application Shell
 * Full sidebar navigation matching the design screenshots.
 */

import { useState, useEffect } from 'react';
import './index.css';

import Overview        from './pages/Overview.jsx';
import Dashboard       from './pages/Dashboard.jsx';
import RepositoryInput from './pages/RepositoryInput.jsx';
import CUQAAgentPage   from './pages/CUQAAgentPage.jsx';
import Reports         from './pages/Reports.jsx';
import Evaluation      from './pages/Evaluation.jsx';
import Settings        from './pages/Settings.jsx';

const API = 'http://localhost:8001';

// ── Sidebar navigation definition ─────────────────────────────────────────
const NAV_MAIN = [
  { id: 'overview',    icon: '⌂',  label: 'Overview' },
  { id: 'dashboard',   icon: '⊞',  label: 'Dashboard' },
  { id: 'repository',  icon: '⛓',  label: 'Repository Input' },
  { id: 'cuqa',        icon: '🔍', label: 'CUQA Agent' },
  { id: 'rdp',         icon: '🧠', label: 'RDP Agent' },
  { id: 'transform',   icon: '⚡', label: 'Transformation Agent' },
  { id: 'orchestrate', icon: '🎛', label: 'Orchestration Agent' },
  { id: 'reports',     icon: '📋', label: 'Reports' },
  { id: 'evaluation',  icon: '🧪', label: 'Evaluation' },
  { id: 'settings',    icon: '⚙',  label: 'Settings' },
];

const NAV_BOTTOM = [
  { id: 'docs',    icon: '📖', label: 'Documentation' },
  { id: 'support', icon: '💬', label: 'Support' },
];

// Agents that aren't built yet — show a "coming soon" state
const PLACEHOLDER_PAGES = {
  rdp: { icon: '🧠', title: 'RDP Agent', sub: 'Refactoring Decision & Planning', color: 'var(--color-rdp)' },
  transform: { icon: '⚡', title: 'Transformation Agent', sub: 'Safe Code Transformation & Validation', color: 'var(--color-transform)' },
  orchestrate: { icon: '🎛️', title: 'Orchestration Agent', sub: 'Developer Interaction & Workflow', color: 'var(--color-orchestrate)' },
  docs:    { icon: '📖', title: 'Documentation', sub: 'Setup guides and API reference', color: 'var(--accent)' },
  support: { icon: '💬', title: 'Support', sub: 'Research team contact', color: 'var(--color-ok)' },
};

function PlaceholderPage({ page }) {
  const info = PLACEHOLDER_PAGES[page] || { icon: '🚧', title: 'Coming Soon', sub: '' };
  return (
    <div className="page-container">
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon" style={{ background: `${info.color}18`, borderColor: `${info.color}40` }}>
            {info.icon}
          </div>
          <div>
            <div className="page-title">{info.title}</div>
            <div className="page-subtitle">{info.sub}</div>
          </div>
        </div>
      </div>
      <div className="empty-state" style={{ flex: 1 }}>
        <span className="empty-icon" style={{ fontSize: 52 }}>{info.icon}</span>
        <p style={{ fontSize: 14 }}>
          <strong style={{ color: info.color }}>{info.title}</strong> is part of the pipeline
          but its interface is managed by the Orchestration Agent in the full system.
        </p>
        <p style={{ fontSize: 12, marginTop: 8 }}>This module will be enabled in the next development phase.</p>
      </div>
    </div>
  );
}

// ── Main App ───────────────────────────────────────────────────────────────
export default function App() {
  const [page,       setPage]       = useState('overview');
  const [repoLoaded, setRepoLoaded] = useState(false);
  const [repoMeta,   setRepoMeta]   = useState(null);
  const [backendOk,  setBackendOk]  = useState(null);
  const [search,     setSearch]     = useState('');

  // Check backend health on mount
  useEffect(() => {
    fetch(`${API}/api/health`)
      .then(r => r.json())
      .then(d => setBackendOk(d.status === 'ok'))
      .catch(() => setBackendOk(false));
  }, []);

  function handleRepoLoaded(data) {
    setRepoLoaded(true);
    setRepoMeta(data);
    // Auto-navigate to CUQA agent after load
    setTimeout(() => setPage('cuqa'), 400);
  }

  function navigate(id) {
    setPage(id);
  }

  // ── Render active page ───────────────────────────────────
  function renderPage() {
    switch (page) {
      case 'overview':    return <Overview onNavigate={navigate} />;
      case 'dashboard':   return <Dashboard />;
      case 'repository':  return <RepositoryInput onLoaded={handleRepoLoaded} />;
      case 'cuqa':        return <CUQAAgentPage repoLoaded={repoLoaded} repoMeta={repoMeta} />;
      case 'reports':     return <Reports />;
      case 'evaluation':  return <Evaluation />;
      case 'settings':    return <Settings />;
      default:            return <PlaceholderPage page={page} />;
    }
  }

  return (
    <div className="app-shell">

      {/* ── Top Bar ─────────────────────────────────────────── */}
      <header className="topbar">
        <span className="topbar-brand">
          Agentic Intelligent Code Refactoring Assistant (R26-SE-008)
        </span>

        <div className="topbar-search">
          <span className="topbar-search-icon">🔍</span>
          <input
            placeholder="Search codebase…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>

        <div className="topbar-spacer" />

        <div className="topbar-actions">
          {/* Backend status */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 11, color: backendOk ? 'var(--color-ok)' : 'var(--color-critical)',
            background: backendOk ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)',
            border: `1px solid ${backendOk ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)'}`,
            borderRadius: 'var(--r-full)', padding: '3px 10px',
          }}>
            <span style={{ fontSize: 8 }}>●</span>
            {backendOk === null ? 'Connecting…' : backendOk ? 'Backend Live' : 'Backend Offline'}
          </div>

          <button className="topbar-icon-btn" title="Notifications">🔔</button>
          <button className="topbar-icon-btn" title="Help">❓</button>
          <div className="topbar-avatar" title="Researcher">R</div>
        </div>
      </header>

      {/* ── Sidebar ─────────────────────────────────────────── */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-brand-name">R26-SE-008</div>
          <div className="sidebar-brand-sub">Deep-Tech Research</div>
        </div>

        <nav className="sidebar-nav">
          {/* Repo chip if loaded */}
          {repoMeta && (
            <div style={{
              margin: '8px 12px',
              background: 'var(--accent-muted)',
              border: '1px solid var(--border-accent)',
              borderRadius: 'var(--r-sm)',
              padding: '8px 10px',
            }}>
              <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--accent)', letterSpacing: '0.5px', marginBottom: 2 }}>
                LOADED
              </div>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                📦 {repoMeta.repo_name}
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>
                {repoMeta.files_found} source files
              </div>
            </div>
          )}

          {/* Main nav items */}
          {NAV_MAIN.map(item => {
            const isActive = page === item.id;
            // highlight agent items differently when repo is loaded
            const isAgentReady = repoLoaded && ['cuqa'].includes(item.id);

            return (
              <div
                key={item.id}
                id={`nav-${item.id}`}
                className={`sidebar-item ${isActive ? 'active' : ''}`}
                onClick={() => navigate(item.id)}
                style={isAgentReady && !isActive ? { color: 'var(--accent)', opacity: 0.9 } : {}}
                title={item.label}
              >
                <span className="s-icon">{item.icon}</span>
                <span>{item.label}</span>
                {item.id === 'cuqa' && repoLoaded && !isActive && (
                  <span style={{
                    marginLeft: 'auto', width: 6, height: 6,
                    borderRadius: '50%', background: 'var(--accent)',
                    flexShrink: 0,
                  }} />
                )}
              </div>
            );
          })}
        </nav>

        {/* Bottom nav */}
        <div className="sidebar-bottom">
          {NAV_BOTTOM.map(item => (
            <div
              key={item.id}
              id={`nav-${item.id}`}
              className={`sidebar-item ${page === item.id ? 'active' : ''}`}
              onClick={() => navigate(item.id)}
            >
              <span className="s-icon">{item.icon}</span>
              <span>{item.label}</span>
            </div>
          ))}
        </div>
      </aside>

      {/* ── Main content ─────────────────────────────────────── */}
      <main className="main-content">
        {/* Backend offline banner */}
        {backendOk === false && (
          <div className="alert alert-error" style={{ margin: '16px 24px 0', borderRadius: 'var(--r-sm)' }}>
            ⚠ CUQA backend is offline. Run:{' '}
            <code style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>
              python agents/cuqa_agent/src/main.py
            </code>
          </div>
        )}
        {renderPage()}
      </main>

    </div>
  );
}
