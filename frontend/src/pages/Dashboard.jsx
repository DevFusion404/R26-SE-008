/**
 * Dashboard.jsx — RefactorIQ system dashboard
 *
 * Reads real data only:
 *   - analysis history (localStorage) with CUQA's measured quality score
 *   - session reports saved when a DIWO workflow is completed
 *   - live health of every agent service
 */

import { useState, useEffect, useCallback } from 'react';
import { SERVICES, checkAllServices, healthUrl } from '../utils/agentHealth';
import { loadHistory, measuredScore, scoreColor } from '../utils/analysisHistory';
import { loadSessionReports } from '../utils/sessionReports';
import './pages.css';

const LANG_COLORS = { Python: '#3b82f6', Java: '#f59e0b', C: '#8b5cf6', Mixed: '#10b981' };

const fmtDate = (iso) => new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });

export default function Dashboard({ onNavigate, repoLoaded = false }) {
  const [history,  setHistory]  = useState(() => loadHistory());
  const [sessions, setSessions] = useState(() => loadSessionReports());
  const [health,   setHealth]   = useState(null);
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState(null);
  const [query,    setQuery]    = useState('');
  const [hoverBar, setHoverBar] = useState(null);

  const refresh = useCallback(async () => {
    setHistory(loadHistory());
    setSessions(loadSessionReports());
    setChecking(true);
    setHealth(await checkAllServices());
    setCheckedAt(new Date());
    setChecking(false);
  }, []);

  useEffect(() => {
    let alive = true;
    checkAllServices().then((h) => {
      if (!alive) return;
      setHealth(h);
      setCheckedAt(new Date());
    });
    return () => { alive = false; };
  }, []);

  // ── Derived stats (measured CUQA scores only) ─────────────────────────
  const measured   = history.filter((h) => measuredScore(h) != null);
  const avgScore   = measured.length ? Math.round(measured.reduce((s, h) => s + h.score, 0) / measured.length) : null;
  const totalSmells = measured.reduce((s, h) => s + (h.smells || 0), 0);
  const totalFiles = history.reduce((s, h) => s + (h.files_found || 0), 0);
  const liveCount  = health ? SERVICES.filter((s) => health[s.id]?.ok).length : null;

  const byLang = history.reduce((acc, h) => {
    const l = h.language || 'Unknown';
    acc[l] = (acc[l] || 0) + 1;
    return acc;
  }, {});

  const chartData = history.slice(0, 10).reverse();
  const shown = history.filter((h) => (h.name || '').toLowerCase().includes(query.trim().toLowerCase()));

  return (
    <div className="page-container">

      {/* ── Header ─────────────────────────────────────────── */}
      <div className="page-header">
        <div className="page-header-left">
          <div className="page-header-icon">📊</div>
          <div>
            <div className="page-title">Dashboard</div>
            <div className="page-subtitle">Live agent status and the history of every repository analysed in this browser.</div>
          </div>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-outline btn-sm" onClick={refresh} disabled={checking}>
            {checking ? 'Refreshing…' : '⟳ Refresh'}
          </button>
          <button className="btn btn-primary btn-sm" onClick={() => onNavigate?.('repository')}>＋ New analysis</button>
        </div>
      </div>

      {/* ── Stats ──────────────────────────────────────────── */}
      <div className="rg-4" style={{ gap: 14 }}>
        <div className="ui-stat" style={{ '--c': 'var(--accent)' }}>
          <span className="k">Analyses <span>🔬</span></span>
          <span className="v">{history.length}</span>
          <span className="s">{totalFiles ? `${totalFiles.toLocaleString()} source files loaded` : 'Run an analysis to begin'}</span>
        </div>
        <div className="ui-stat" style={{ '--c': scoreColor(avgScore) }}>
          <span className="k">Avg. quality <span>⭐</span></span>
          <span className="v">{avgScore ?? '—'}</span>
          <span className="s">{measured.length ? `CUQA score over ${measured.length} run(s)` : 'No CUQA score yet'}</span>
        </div>
        <div className="ui-stat" style={{ '--c': 'var(--color-high)' }}>
          <span className="k">Smells found <span>🦨</span></span>
          <span className="v">{measured.length ? totalSmells.toLocaleString() : '—'}</span>
          <span className="s">across the scored repositories</span>
        </div>
        <button type="button" className="ui-stat ui-click" style={{ '--c': 'var(--color-orchestrate)', textAlign: 'left', fontFamily: 'var(--font-body)' }} onClick={() => onNavigate?.('reports')}>
          <span className="k">Sessions done <span>→</span></span>
          <span className="v">{sessions.length}</span>
          <span className="s">{sessions.length ? 'Open the session reports' : 'Complete a DIWO workflow'}</span>
        </button>
      </div>

      {/* ── Agent status ───────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">🤖 Agent services</span>
          <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
            {liveCount == null ? 'Checking…' : `${liveCount} of ${SERVICES.length} answering`}
            {checkedAt ? ` · checked ${checkedAt.toLocaleTimeString()}` : ''}
          </span>
        </div>
        <div className="rg-auto-220" style={{ gap: 12, padding: 16 }}>
          {SERVICES.map((s) => {
            const h = health?.[s.id];
            const canOpen = s.page && repoLoaded;
            return (
              <div
                key={s.id}
                className={s.page ? `ui-click ${canOpen ? '' : 'is-locked'}` : ''}
                role={s.page ? 'button' : undefined}
                tabIndex={s.page ? 0 : undefined}
                onClick={() => canOpen && onNavigate?.(s.page)}
                onKeyDown={(e) => { if (e.key === 'Enter' && canOpen) onNavigate?.(s.page); }}
                title={healthUrl(s)}
                style={{
                  padding: 14, borderRadius: 'var(--r-md)', background: 'var(--bg-elevated)',
                  border: '1px solid var(--border)', borderLeft: `3px solid ${s.color}`,
                  display: 'flex', flexDirection: 'column', gap: 6,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span className={`ui-dot ${!h ? 'wait' : h.ok ? 'ok' : 'down'}`} />
                  <b style={{ fontSize: 14, color: 'var(--text-primary)' }}>{s.name}</b>
                </div>
                <span style={{ fontSize: 12.5, color: 'var(--text-secondary)' }}>{s.role}</span>
                <span className={`ui-chip ${!h ? '' : h.ok ? 'ok' : 'bad'}`} style={{ alignSelf: 'flex-start' }}>
                  {!h ? 'checking…' : h.ok ? `online · ${h.ms} ms` : h.detail}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Score chart + language split ───────────────────── */}
      <div className="rg-side-300" style={{ gap: 14 }}>
        <div className="card">
          <div className="card-header">
            <span className="card-title">📈 Quality score per analysis</span>
            <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>{chartData.length ? `last ${chartData.length}, oldest on the left` : ''}</span>
          </div>
          {chartData.length === 0 ? (
            <div className="empty-state" style={{ padding: 40 }}>
              <span className="empty-icon">📈</span>
              <p>Scores appear here after your first analysis.</p>
            </div>
          ) : (
            <div style={{ padding: '18px 20px 16px' }}>
              <div style={{ position: 'relative', display: 'flex', alignItems: 'flex-end', gap: 10, height: 150, borderBottom: '1px solid var(--border)' }}>
                {[80, 60].map((line) => (
                  <div key={line} style={{ position: 'absolute', left: 0, right: 0, bottom: `${line}%`, borderTop: '1px dashed var(--border-light)' }}>
                    <span style={{ position: 'absolute', right: 0, top: -16, fontSize: 11, color: 'var(--text-muted)' }}>{line}</span>
                  </div>
                ))}
                {chartData.map((h) => {
                  const score = measuredScore(h);
                  const on = hoverBar === h.id;
                  return (
                    <div
                      key={h.id}
                      onMouseEnter={() => setHoverBar(h.id)}
                      onMouseLeave={() => setHoverBar(null)}
                      style={{ flex: 1, height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', alignItems: 'center', position: 'relative', zIndex: 1 }}
                    >
                      {on && (
                        <div style={{
                          position: 'absolute', bottom: `calc(${score ?? 6}% + 8px)`, whiteSpace: 'nowrap',
                          background: 'var(--bg-elevated)', border: '1px solid var(--border-light)', borderRadius: 6,
                          padding: '4px 8px', fontSize: 12, color: 'var(--text-primary)', boxShadow: 'var(--shadow-sm)', zIndex: 2,
                        }}>
                          <b>{h.name}</b> · {score == null ? 'not scored' : `${score}/100`}{h.smells != null ? ` · ${h.smells} smells` : ''}
                        </div>
                      )}
                      <div style={{
                        width: '100%', maxWidth: 46,
                        height: score == null ? '6%' : `${Math.max(score, 3)}%`,
                        background: score == null ? 'var(--bg-hover)' : scoreColor(score),
                        borderRadius: '5px 5px 0 0',
                        opacity: on ? 1 : 0.8,
                        transition: 'opacity 0.15s ease, height 0.5s ease',
                      }} />
                    </div>
                  );
                })}
              </div>
              <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
                {chartData.map((h) => (
                  <div key={h.id} style={{ flex: 1, fontSize: 11.5, color: 'var(--text-muted)', textAlign: 'center', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {h.name}
                  </div>
                ))}
              </div>
              <div className="ui-legend" style={{ marginTop: 12 }}>
                <span><i style={{ background: 'var(--color-ok)' }} />80+ healthy</span>
                <span><i style={{ background: 'var(--color-medium)' }} />60–79 review</span>
                <span><i style={{ background: 'var(--color-critical)' }} />below 60</span>
                <span><i style={{ background: 'var(--bg-hover)' }} />not scored by CUQA</span>
              </div>
            </div>
          )}
        </div>

        <div className="card">
          <div className="card-header"><span className="card-title">🌐 Languages</span></div>
          {Object.keys(byLang).length === 0 ? (
            <div className="empty-state" style={{ padding: 32 }}><p>No data yet</p></div>
          ) : (
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
              {Object.entries(byLang).sort((a, b) => b[1] - a[1]).map(([lang, count]) => {
                const pct = Math.round((count / history.length) * 100);
                return (
                  <div key={lang}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5, fontSize: 13 }}>
                      <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{lang}</span>
                      <span style={{ color: 'var(--text-muted)' }}>{count} · {pct}%</span>
                    </div>
                    <div className="progress-bar">
                      <div className="progress-fill" style={{ width: `${pct}%`, background: LANG_COLORS[lang] || 'var(--text-muted)' }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── History table ──────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">📋 Analysis history</span>
          <input
            className="input"
            style={{ maxWidth: 240, padding: '6px 12px', fontSize: 13 }}
            placeholder="Search repositories…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        {history.length === 0 ? (
          <div className="empty-state" style={{ padding: 40 }}>
            <span className="empty-icon">📂</span>
            <p>No analysis history yet. Load a repository in <strong>Repository Input</strong>.</p>
          </div>
        ) : shown.length === 0 ? (
          <div className="empty-state" style={{ padding: 28 }}><p>No repository matches “{query}”.</p></div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Language</th>
                  <th>Date</th>
                  <th>Files</th>
                  <th>Smells</th>
                  <th>Threshold</th>
                  <th>Quality (CUQA)</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((h) => {
                  const score = measuredScore(h);
                  return (
                    <tr key={h.id}>
                      <td>
                        <span style={{ marginRight: 8 }}>{h.source === 'github' ? '🔗' : '📁'}</span>
                        <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{h.name}</span>
                      </td>
                      <td><span className={`badge badge-${(h.language || 'unknown').toLowerCase()}`}>{h.language || 'Unknown'}</span></td>
                      <td>{fmtDate(h.date)}</td>
                      <td>{(h.files_found || 0).toLocaleString()}</td>
                      <td>{h.smells ?? '—'}</td>
                      <td>{h.threshold ?? '—'}%</td>
                      <td>
                        {score == null ? (
                          <span style={{ color: 'var(--text-muted)' }} title="Open the CUQA page after loading to score it">not scored</span>
                        ) : (
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <div className="progress-bar" style={{ width: 70, flex: 'none' }}>
                              <div className="progress-fill" style={{ width: `${score}%`, background: scoreColor(score) }} />
                            </div>
                            <b style={{ color: scoreColor(score) }}>{score}</b>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  );
}
