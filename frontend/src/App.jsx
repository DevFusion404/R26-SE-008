/**
 * App.jsx — R26-SE-008 Main Application Shell
 * Full sidebar navigation matching the design screenshots.
 */

import { useState, useEffect } from 'react';
import './index.css';
import { useTheme } from './context/ThemeContext.jsx';

import Overview        from './pages/Overview.jsx';
import Dashboard       from './pages/Dashboard.jsx';
import RepositoryInput from './pages/RepositoryInput.jsx';
import CUQAAgentPage   from './pages/CUQAAgentPage.jsx';
import RDPAgentPage    from './pages/RDPAgentPage.jsx';
import SCTVAAgentPage  from './pages/transform/SCTVAAgentPage.jsx';
import DIWOAgentPage   from './pages/diwo/DIWOAgentPage.jsx';
import Reports         from './pages/Reports.jsx';
import Evaluation      from './pages/Evaluation.jsx';
import Settings        from './pages/Settings.jsx';
import Documentation   from './pages/Documentation.jsx';
import UserService     from './services/userService.js';
import AuthPage        from './pages/AuthPage.jsx';
import ProfilePage     from './pages/ProfilePage.jsx';

import { getEnv } from './config/env';

const API = getEnv('VITE_CUQA_AGENT_API_URL', getEnv('VITE_CUQA_API_URL', 'http://localhost:8080')).replace(/\/+$/, '');

// ── Sidebar navigation definition ─────────────────────────────────────────
const NAV_MAIN = [
  { id: 'overview',    icon: '⌂',  label: 'Overview' },
  { id: 'dashboard',   icon: '⊞',  label: 'Dashboard' },
  { id: 'repository',  icon: '⛓',  label: 'Repository Input' },
  { id: 'cuqa',        icon: '🔍', label: 'CUQA Agent' },
  { id: 'rdp',         icon: '🧠', label: 'RDP Agent' },
  { id: 'transform',   icon: '⚡', label: 'Transformation Agent' },
  { id: 'orchestrate', icon: '🎛', label: 'DIWO Agent' },
  { id: 'reports',     icon: '📋', label: 'Reports' },
  { id: 'evaluation',  icon: '🧪', label: 'Evaluation' },
  { id: 'settings',    icon: '⚙',  label: 'Settings' },
];

const NAV_BOTTOM = [
  { id: 'profile', icon: '👤', label: 'Profile' },
];

// Agents that aren't built yet — show a "coming soon" state
const PLACEHOLDER_PAGES = {
  rdp: { icon: '🧠', title: 'RDP Agent', sub: 'Refactoring Decision & Planning', color: 'var(--color-rdp)' },
  transform: { icon: '⚡', title: 'Transformation Agent', sub: 'Safe Code Transformation & Validation', color: 'var(--color-transform)' },
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
  const [currentUser, setCurrentUser] = useState(() => UserService.getUser());
  const [isGuest, setIsGuest] = useState(
    () => !UserService.isAuthenticated() && localStorage.getItem('is_guest') === 'true'
  );

  // Persist active page across refreshes
  const [page, setPage] = useState(
    () => localStorage.getItem('rfiq_active_page') || 'overview'
  );
  const [repoLoaded,    setRepoLoaded]    = useState(false);
  const [repoMeta,      setRepoMeta]      = useState(null);
  const [repoConfig,    setRepoConfig]    = useState(null);  // threshold, filters, mode from RepositoryInput
  const [backendOk,     setBackendOk]     = useState(null);
  const [search,        setSearch]        = useState('');
  const [cuqaReport,    setCuqaReport]    = useState(null); // quality report from CUQA → RDP bridge
  // 'idle' | 'cuqa_done' | 'rdp_running' | 'rdp_done'
  const [pipelineState, setPipelineState] = useState('idle');
  const { theme, toggleTheme, isDark } = useTheme();

  async function handleLogout() {
    await UserService.logout();
    setCurrentUser(null);
    setIsGuest(false);
    localStorage.removeItem('is_guest');
    localStorage.setItem('rfiq_active_page', 'overview');
    setPage('overview');
  }

  function handleProfileUpdate(updatedUser) {
    setCurrentUser(updatedUser);
  }

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
    // Persist the analysis config (threshold, filters, mode, language) from RepositoryInput
    if (data.config) setRepoConfig(data.config);
    // Auto-navigate to CUQA agent after load
    setTimeout(() => {
      localStorage.setItem('rfiq_active_page', 'cuqa');
      setPage('cuqa');
    }, 400);
  }

  // Called from CUQAAgentPage when user clicks "Continue → RDP Agent"
  function handleSendToRdp(report) {
    setCuqaReport(report);
    setPipelineState('rdp_running');
    localStorage.setItem('rfiq_active_page', 'rdp');
    setPage('rdp');
  }

  function handleClearPreloaded() {
    setCuqaReport(null);
  }

  function handleRdpDone() {
    setPipelineState('rdp_done');
  }

  const GATED_DOWNSTREAM_AGENTS = ['rdp', 'transform', 'orchestrate'];
  const isDetectionDone = repoLoaded && !!cuqaReport;

  function navigate(id) {
    if (id === 'cuqa' && !repoLoaded) {
      return; // Lock CUQA Agent until repository is uploaded/linked
    }
    if (GATED_DOWNSTREAM_AGENTS.includes(id) && !isDetectionDone) {
      return; // Lock downstream agents until code smell analysis completes
    }
    localStorage.setItem('rfiq_active_page', id);
    setPage(id);
  }

  // ── Render active page ───────────────────────────────────
  function renderPage() {
    switch (page) {
      case 'overview':    return <Overview onNavigate={navigate} />;
      case 'dashboard':   return <Dashboard />;
      case 'repository':  return <RepositoryInput onLoaded={handleRepoLoaded} />;
      case 'cuqa':        return (
        <CUQAAgentPage
          repoLoaded={repoLoaded}
          repoMeta={repoMeta}
          analysisConfig={repoConfig}
          onSendToRdp={handleSendToRdp}
          onReportReady={(rep) => setCuqaReport(rep)}
          onNavigate={navigate}
          pipelineState={pipelineState}
          onPipelineStateChange={setPipelineState}
        />
      );
      case 'rdp':         return (
        <RDPAgentPage
          repoLoaded={repoLoaded}
          repoMeta={repoMeta}
          preloadedReport={cuqaReport}
          onClearPreloaded={handleClearPreloaded}
          onPlanGenerated={handleRdpDone}
        />
      );
      case 'transform':   return <SCTVAAgentPage />;
      case 'orchestrate': return <DIWOAgentPage />;
      case 'reports':     return <Reports />;
      case 'evaluation':  return <Evaluation />;
      case 'settings':    return <Settings />;
      case 'profile':     return (
        <ProfilePage
          currentUser={currentUser}
          isGuest={isGuest}
          onLogout={handleLogout}
          onProfileUpdate={handleProfileUpdate}
        />
      );
      case 'docs':        return <Documentation />;
      default:            return <PlaceholderPage page={page} />;
    }
  }

  if (!currentUser && !isGuest) {
    return (
      <AuthPage
        onAuthSuccess={(user) => {
          setCurrentUser(user);
          setIsGuest(false);
          localStorage.removeItem('is_guest');
        }}
        onGuestLogin={() => {
          setIsGuest(true);
          setCurrentUser(null);
        }}
      />
    );
  }

  return (
    <div className="app-shell">

      {/* ── Top Bar ─────────────────────────────────────────── */}
      <header className="topbar">
        <span className="topbar-brand">
          <span style={{ color: 'var(--accent)', fontWeight: 800 }}>RefactorIQ</span>
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
          {/* ── Theme Toggle Button ─────────────────────────── */}
          <button
            id="theme-toggle-btn"
            onClick={toggleTheme}
            title={isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
            aria-label={isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: '5px 12px',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--r-full)',
              background: isDark ? 'var(--bg-elevated)' : '#fff8e1',
              color: isDark ? '#a0aec0' : '#b45309',
              cursor: 'pointer',
              fontSize: 13,
              fontWeight: 600,
              fontFamily: 'var(--font-body)',
              lineHeight: 1,
              userSelect: 'none',
              boxShadow: isDark ? 'none' : '0 1px 4px rgba(0,0,0,0.10)',
              transition: 'background 0.25s ease, color 0.25s ease, border-color 0.25s ease',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-light)'; }}
          >
            <span
              style={{
                fontSize: 15,
                display: 'inline-block',
                transition: 'transform 0.4s ease',
                transform: isDark ? 'rotate(0deg)' : 'rotate(20deg)',
              }}
            >
              {isDark ? '🌙' : '☀️'}
            </span>
            <span style={{ fontSize: 11 }}>
              {isDark ? 'Dark' : 'Light'}
            </span>
          </button>
          <div
            className="topbar-avatar"
            title={isGuest ? 'Guest User' : currentUser?.full_name}
            onClick={() => navigate('profile')}
            style={{
              background: isGuest ? 'var(--text-muted)' : 'var(--gradient-accent)',
              color: isGuest ? 'var(--text-secondary)' : '#000',
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer'
            }}
          >
            {isGuest ? 'G' : (currentUser?.full_name?.[0]?.toUpperCase() || 'U')}
          </div>
        </div>
      </header>

      {/* ── Sidebar ─────────────────────────────────────────── */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="sidebar-brand-name" style={{ color: 'var(--accent)' }}>RefactorIQ</div>
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
            const isActive          = page === item.id;
            const isCuqaGated       = item.id === 'cuqa' && !repoLoaded;
            const isDownstreamGated = GATED_DOWNSTREAM_AGENTS.includes(item.id) && !isDetectionDone;
            const isDisabled        = isCuqaGated || isDownstreamGated;
            const isAgentReady      = repoLoaded && ['cuqa'].includes(item.id);
            // Pipeline status dot colours
            const rdpPulsing        = item.id === 'rdp' && pipelineState === 'rdp_running';
            const rdpDone           = item.id === 'rdp' && pipelineState === 'rdp_done';

            const tooltipText = isCuqaGated
              ? 'CUQA Agent — Please upload or link a repository in Repository Input first'
              : isDownstreamGated
              ? `${item.label} — Upload repository & run CUQA code smell analysis first`
              : item.label;

            return (
              <div
                key={item.id}
                id={`nav-${item.id}`}
                className={`sidebar-item ${isActive ? 'active' : ''} ${isDisabled ? 'disabled' : ''}`}
                onClick={() => !isDisabled && navigate(item.id)}
                style={isDisabled
                  ? { opacity: 0.35, cursor: 'not-allowed', filter: 'grayscale(0.6)' }
                  : isAgentReady && !isActive ? { color: 'var(--accent)', opacity: 0.9 } : {}
                }
                title={tooltipText}
              >
                <span className="s-icon">{item.icon}</span>
                <span>{item.label}</span>
                {/* Lock icon for gated agents before unlock condition */}
                {isDisabled && (
                  <span style={{ marginLeft: 'auto', fontSize: 10, opacity: 0.6 }} title={tooltipText}>🔒</span>
                )}
                {/* CUQA ready dot */}
                {item.id === 'cuqa' && repoLoaded && !isActive && (
                  <span style={{
                    marginLeft: 'auto', width: 6, height: 6,
                    borderRadius: '50%', background: 'var(--accent)',
                    flexShrink: 0,
                  }} />
                )}
                {/* RDP pipeline running pulse */}
                {rdpPulsing && (
                  <span style={{
                    marginLeft: 'auto', width: 8, height: 8,
                    borderRadius: '50%', background: '#a855f7',
                    flexShrink: 0, animation: 'pulse 1.2s infinite',
                  }} />
                )}
                {/* RDP done check */}
                {rdpDone && !isActive && (
                  <span style={{
                    marginLeft: 'auto', fontSize: 11, color: '#22c55e', flexShrink: 0,
                  }}>✓</span>
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
