/**
 * Dashboard.jsx — RefactorIQ System Dashboard
 *
 * Reads real data from:
 *   - localStorage (analysis history from RepositoryInput)
 *   - CUQA backend health API (/api/health)
 *
 * Shows:
 *   - Top metric cards (total analyses, avg score, repos analysed, files scanned)
 *   - Score history bar chart
 *   - Per-repository score table with progress bars
 *   - Pipeline agent status
 *   - Quick actions
 */

import { useState, useEffect } from 'react';
import { getEnv } from '../config/env';

const API          = getEnv('VITE_CUQA_AGENT_API_URL', getEnv('VITE_CUQA_API_URL', 'http://localhost:8080')).replace(/\/+$/, '');
const HISTORY_KEY  = 'cuqa_analysis_history';

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); }
  catch { return []; }
}

function scoreColor(s) {
  if (s >= 80) return '#22c55e';
  if (s >= 60) return '#f59e0b';
  if (s >= 40) return '#ef4444';
  return '#dc2626';
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ── Mini metric card ─────────────────────────────────────────────────────────
function MetricCard({ icon, label, value, sub, color, trend }) {
  return (
    <div className="card card-body" style={{ padding: 20 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ fontSize: 10, letterSpacing: '1px', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600 }}>
          {label}
        </div>
        <span style={{ fontSize: 18 }}>{icon}</span>
      </div>
      <div style={{ fontSize: 28, fontWeight: 800, color: color || 'var(--text-primary)', lineHeight: 1 }}>
        {value}
      </div>
      {sub && (
        <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 6 }}>{sub}</div>
      )}
      {trend !== undefined && (
        <div style={{
          marginTop: 8, fontSize: 10, color: trend >= 0 ? '#22c55e' : '#ef4444',
          display: 'flex', alignItems: 'center', gap: 3,
        }}>
          {trend >= 0 ? '▲' : '▼'} {Math.abs(trend)}% vs previous
        </div>
      )}
    </div>
  );
}

// ── Agent status row ─────────────────────────────────────────────────────────
const AGENTS = [
  { name: 'CUQA Agent',           icon: '🔍', color: '#00d4e8', role: 'Code Quality Assessment' },
  { name: 'RDP Agent',            icon: '🧠', color: '#8b5cf6', role: 'Refactoring Decision Planning' },
  { name: 'Transformation Agent', icon: '⚡', color: '#f59e0b', role: 'Safe Code Transformation' },
  { name: 'DIWO Agent',           icon: '🎛️', color: '#10b981', role: 'Developer-in-the-Loop Workflow' },
];

export default function Dashboard() {
  const [history,   setHistory]   = useState([]);
  const [backendOk, setBackendOk] = useState(null);
  const [wsLoaded,  setWsLoaded]  = useState(false);
  const [repoName,  setRepoName]  = useState(null);
  const [refresh,   setRefresh]   = useState(0);

  useEffect(() => {
    setHistory(loadHistory());

    fetch(`${API}/api/health`)
      .then(r => r.json())
      .then(d => {
        setBackendOk(d.status === 'ok');
        setWsLoaded(!!d.workspace_loaded);
      })
      .catch(() => setBackendOk(false));

    // Try to get workspace repo name
    fetch(`${API}/api/project-structure`)
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setRepoName(d.repo_name))
      .catch(() => {});
  }, [refresh]);

  // ── Derived stats ────────────────────────────────────────────────────────
  const totalRuns       = history.length;
  const avgScore        = totalRuns > 0
    ? Math.round(history.reduce((s, h) => s + h.score, 0) / totalRuns)
    : null;
  const totalFiles      = history.reduce((s, h) => s + (h.files_found || 0), 0);
  const uniqueRepos     = new Set(history.map(h => h.name)).size;

  // Score trend: compare last vs second-to-last
  const scoreTrend = history.length >= 2
    ? Math.round(history[0].score - history[1].score)
    : undefined;

  // Group scores by language for language distribution
  const byLang = history.reduce((acc, h) => {
    const l = h.language || 'Unknown';
    acc[l] = (acc[l] || 0) + 1;
    return acc;
  }, {});

  const langColors = {
    Python: '#3b82f6', Java: '#f59e0b', C: '#8b5cf6',
    Mixed: '#10b981', Unknown: '#374151',
  };

  // Most recent 8 for bar chart
  const chartData = history.slice(0, 8).reverse();

  return (
    <div className="page-container">

      {/* ── Header ───────────────────────────────────────────── */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">📊</div>
          <div>
            <div className="page-title">Dashboard</div>
            <div className="page-subtitle">
              RefactorIQ system metrics · real-time analysis history from this session
            </div>
          </div>
        </div>
        <div className="page-header-actions">
          <button
            className="btn btn-outline btn-sm"
            onClick={() => setRefresh(r => r + 1)}
            title="Refresh stats"
          >
            ⟳ Refresh
          </button>
        </div>
      </div>

      {/* ── Backend banner ───────────────────────────────────── */}
      {backendOk === false && (
        <div className="alert alert-error">
          ⚠ CUQA backend is offline — metrics may be incomplete.
          Run: <code style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>python agents/cuqa_agent/src/main.py</code>
        </div>
      )}

      {/* ── Metric cards ─────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
        <MetricCard
          icon="🔬"
          label="Total Analyses"
          value={totalRuns}
          sub={totalRuns === 0 ? 'Run an analysis to begin' : `${uniqueRepos} unique repo${uniqueRepos !== 1 ? 's' : ''}`}
          color="var(--accent)"
        />
        <MetricCard
          icon="⭐"
          label="Avg Quality Score"
          value={avgScore != null ? `${avgScore}%` : '—'}
          sub={avgScore != null ? (avgScore >= 70 ? 'Good overall health' : avgScore >= 50 ? 'Needs attention' : 'Critical issues present') : 'No data yet'}
          color={avgScore != null ? scoreColor(avgScore) : 'var(--text-muted)'}
          trend={scoreTrend}
        />
        <MetricCard
          icon="📁"
          label="Files Scanned"
          value={totalFiles > 0 ? totalFiles.toLocaleString() : '—'}
          sub={totalFiles > 0 ? 'across all repositories' : 'No files scanned yet'}
          color="#8b5cf6"
        />
        <MetricCard
          icon="🔗"
          label="Active Workspace"
          value={wsLoaded ? '●' : '○'}
          sub={wsLoaded ? (repoName || 'Workspace loaded') : 'No repo loaded'}
          color={wsLoaded ? '#22c55e' : 'var(--text-muted)'}
        />
      </div>

      {/* ── Score chart + Language split ──────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 220px', gap: 14 }}>

        {/* Score history bar chart */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">📈 Quality Score History</span>
            {chartData.length > 0 && (
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                last {chartData.length} analyses
              </span>
            )}
          </div>
          {chartData.length === 0 ? (
            <div className="empty-state" style={{ padding: 40 }}>
              <span className="empty-icon">📈</span>
              <p>Quality score history will appear here after your first analysis.</p>
            </div>
          ) : (
            <div style={{ padding: '16px 20px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'flex-end', gap: 8, height: 120 }}>
                {chartData.map((h, i) => (
                  <div key={h.id} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, height: '100%', justifyContent: 'flex-end' }}>
                    <div style={{ fontSize: 9, color: scoreColor(h.score), fontWeight: 700 }}>{h.score}%</div>
                    <div
                      title={`${h.name} — ${h.score}%`}
                      style={{
                        width: '100%',
                        height: `${h.score}%`,
                        background: h.color,
                        borderRadius: '4px 4px 0 0',
                        opacity: i === chartData.length - 1 ? 1 : 0.65,
                        cursor: 'default',
                        transition: 'opacity 0.2s',
                        minHeight: 4,
                      }}
                    />
                  </div>
                ))}
              </div>
              {/* X-axis labels */}
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                {chartData.map(h => (
                  <div key={h.id} style={{
                    flex: 1, fontSize: 9, color: 'var(--text-muted)',
                    textAlign: 'center', overflow: 'hidden',
                    textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {h.name.length > 8 ? h.name.slice(0, 8) + '…' : h.name}
                  </div>
                ))}
              </div>
              {/* Threshold guide line note */}
              <div style={{ marginTop: 12, fontSize: 10, color: 'var(--text-muted)' }}>
                Score ≥ 80% = healthy · 60–79% = review needed · &lt; 60% = critical
              </div>
            </div>
          )}
        </div>

        {/* Language distribution */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">🌐 Language Split</span>
          </div>
          {Object.keys(byLang).length === 0 ? (
            <div style={{ padding: '32px 16px', textAlign: 'center' }}>
              <div style={{ fontSize: 28, marginBottom: 8 }}>🌐</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>No data yet</div>
            </div>
          ) : (
            <div style={{ padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {Object.entries(byLang).map(([lang, count]) => {
                const pct = Math.round((count / totalRuns) * 100);
                const c   = langColors[lang] || '#374151';
                return (
                  <div key={lang}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                      <span style={{ fontSize: 11, color: 'var(--text-primary)', fontWeight: 600 }}>{lang}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{count} ({pct}%)</span>
                    </div>
                    <div className="progress-bar">
                      <div className="progress-fill" style={{ width: `${pct}%`, background: c }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Repository history table ──────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">📋 Analysis History</span>
          <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
            {totalRuns} {totalRuns === 1 ? 'entry' : 'entries'} · stored in browser
          </span>
        </div>
        {history.length === 0 ? (
          <div className="empty-state" style={{ padding: 40 }}>
            <span className="empty-icon">📂</span>
            <p>
              No analysis history yet. Go to{' '}
              <strong style={{ color: 'var(--accent)' }}>Repository Input</strong>{' '}
              to load your first codebase.
            </p>
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Language</th>
                  <th>Mode</th>
                  <th>Date</th>
                  <th>Files</th>
                  <th>Threshold</th>
                  <th>Quality Score</th>
                  <th>Filters</th>
                </tr>
              </thead>
              <tbody>
                {history.map(h => (
                  <tr key={h.id}>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ color: 'var(--text-muted)' }}>
                          {h.source === 'github' ? '🔗' : '📁'}
                        </span>
                        <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: 12 }}>
                          {h.name}
                        </span>
                      </div>
                    </td>
                    <td>
                      <span
                        className={`badge badge-${(h.language || 'unknown').toLowerCase()}`}
                        style={{ textTransform: 'capitalize' }}
                      >
                        {h.language || 'Unknown'}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                        {h.mode ? h.mode.replace(' Refactoring', '') : '—'}
                      </span>
                    </td>
                    <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                      {fmtDate(h.date)}
                    </td>
                    <td style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                      {(h.files_found || 0).toLocaleString()}
                    </td>
                    <td>
                      <span style={{
                        fontSize: 11, fontWeight: 600,
                        color: h.threshold >= 75 ? '#22c55e' : h.threshold >= 50 ? '#f59e0b' : '#ef4444',
                      }}>
                        {h.threshold ?? '—'}%
                      </span>
                    </td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <div className="progress-bar" style={{ width: 70, flex: 'none' }}>
                          <div
                            className="progress-fill"
                            style={{ width: `${h.score}%`, background: h.color }}
                          />
                        </div>
                        <span style={{ fontSize: 12, fontWeight: 700, color: h.color }}>
                          {h.score}%
                        </span>
                      </div>
                    </td>
                    <td>
                      <div style={{ display: 'flex', gap: 4 }}>
                        {h.filters?.critical && (
                          <span style={{ fontSize: 9, padding: '1px 5px', background: '#ef444420', color: '#ef4444', borderRadius: 3, fontWeight: 600 }}>
                            CRIT
                          </span>
                        )}
                        {h.filters?.naming && (
                          <span style={{ fontSize: 9, padding: '1px 5px', background: '#f59e0b20', color: '#f59e0b', borderRadius: 3, fontWeight: 600 }}>
                            NAME
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Agent pipeline status ─────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">🤖 Agent Pipeline Status</span>
        </div>
        <div style={{ padding: '12px 20px 20px', display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 14 }}>
          {AGENTS.map((agent, i) => {
            // CUQA is live if backend is up, rest are pipeline stages
            const isLive   = i === 0 && backendOk === true;
            const isPipeline = i > 0;
            return (
              <div key={agent.name} style={{
                padding: 14,
                background: 'var(--bg-elevated)',
                border: `1px solid ${isLive ? agent.color + '60' : 'var(--border)'}`,
                borderRadius: 'var(--r-md)',
                boxShadow: isLive ? `0 0 12px ${agent.color}18` : 'none',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: 20 }}>{agent.icon}</span>
                  <div style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: isLive ? '#22c55e' : isPipeline ? agent.color + '60' : '#374151',
                    boxShadow: isLive ? '0 0 6px #22c55e' : 'none',
                    flexShrink: 0,
                  }} />
                </div>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 3 }}>
                  {agent.name}
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.5 }}>
                  {agent.role}
                </div>
                <div style={{ marginTop: 8, fontSize: 9, fontWeight: 600, letterSpacing: '0.5px',
                  color: isLive ? '#22c55e' : isPipeline ? agent.color : '#3d5166',
                }}>
                  {isLive ? '● LIVE' : isPipeline ? '○ PIPELINE STAGE' : '○ OFFLINE'}
                </div>
              </div>
            );
          })}
        </div>
      </div>

    </div>
  );
}
