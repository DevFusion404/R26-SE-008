/**
 * Overview.jsx — RefactorIQ home page
 * - Hero with live status of the four agents
 * - Getting-started steps that tick off as the user progresses
 * - The pipeline as clickable agent cards (locked until a repository is loaded)
 * - Recent analyses with CUQA's measured quality score
 */

import { useState, useEffect, useCallback } from 'react';
import { SERVICES, checkAllServices } from '../utils/agentHealth';
import { loadHistory, measuredScore, scoreColor } from '../utils/analysisHistory';
import { loadSessionReports } from '../utils/sessionReports';
import './pages.css';

const AGENTS = SERVICES.filter((s) => s.page);

const PIPELINE = [
  { id: 'cuqa',  step: 1, name: 'CUQA',  role: 'Code Understanding & Quality Assessment', out: 'Smells, quality scores, JSON report', color: 'var(--color-cuqa)',        page: 'cuqa' },
  { id: 'rdp',   step: 2, name: 'RDP',   role: 'Refactoring Decision & Planning',         out: 'Ranked refactoring plan with risk and impact', color: 'var(--color-rdp)', page: 'rdp' },
  { id: 'sctva', step: 3, name: 'SCTVA', role: 'Safe Code Transformation & Validation',   out: 'Refactored code, validation result, rollback', color: 'var(--color-transform)', page: 'transform' },
  { id: 'diwo',  step: 4, name: 'DIWO',  role: 'Developer Interaction & Workflow',        out: 'Developer decisions, audit trail, session report', color: 'var(--color-orchestrate)', page: 'orchestrate' },
];

const fmtDate = (iso) => new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });

export default function Overview({ onNavigate, repoLoaded = false, repoMeta = null }) {
  const [history]  = useState(() => loadHistory());
  const [sessions] = useState(() => loadSessionReports());
  const [health, setHealth]     = useState(null);
  const [checking, setChecking] = useState(false);

  const runCheck = useCallback(async () => {
    setChecking(true);
    setHealth(await checkAllServices(AGENTS));
    setChecking(false);
  }, []);

  useEffect(() => {
    let alive = true;
    checkAllServices(AGENTS).then((h) => { if (alive) setHealth(h); });
    return () => { alive = false; };
  }, []);

  const scored   = history.map(measuredScore).filter((s) => s != null);
  const avgScore = scored.length ? Math.round(scored.reduce((a, b) => a + b, 0) / scored.length) : null;
  const liveCount = health ? AGENTS.filter((a) => health[a.id]?.ok).length : null;

  const steps = [
    {
      title: 'Load a repository',
      text: repoLoaded ? `${repoMeta?.repo_name} is loaded (${repoMeta?.files_found} source files).` : 'Upload a ZIP or paste a public GitHub link.',
      done: repoLoaded, page: 'repository',
    },
    {
      title: 'Review the CUQA analysis',
      text: 'See the structure, code smells and quality score of every file.',
      done: scored.length > 0 || sessions.length > 0, page: 'cuqa', locked: !repoLoaded,
    },
    {
      title: 'Run the refactoring workflow',
      text: sessions.length ? `${sessions.length} session report(s) saved — open Reports.` : 'Select smells, approve the plan, check the changes in DIWO.',
      done: sessions.length > 0, page: sessions.length ? 'reports' : 'orchestrate', locked: !repoLoaded && !sessions.length,
    },
  ];
  const nextIdx = steps.findIndex((s) => !s.done);

  return (
    <div className="page-container">

      {/* ── Hero ───────────────────────────────────────────── */}
      <section className="ui-hero">
        <div className="ui-hero-row">
          <div className="ui-hero-text">
            <span className="ui-kicker">⚡ Agentic refactoring for legacy code</span>
            <h1 className="ui-title"><span className="hl">RefactorIQ</span> — find, plan and safely fix code smells</h1>
            <p className="ui-lead">
              Four agents work one after the other on a Java, Python or C repository. CUQA finds the problems,
              RDP plans the refactorings, SCTVA changes and validates the code, and DIWO keeps the developer
              in control of every decision.
            </p>
            <div className="ui-actions">
              <button className="btn btn-primary" onClick={() => onNavigate('repository')}>▶ Start an analysis</button>
              {repoLoaded && (
                <button className="btn btn-outline" onClick={() => onNavigate('cuqa')}>Continue with {repoMeta?.repo_name} →</button>
              )}
              <button className="btn btn-outline" onClick={() => onNavigate('reports')}>🧾 Reports</button>
            </div>
          </div>

          {/* Live agent status */}
          <div className="card" style={{ flex: '0 1 300px', minWidth: 240 }}>
            <div className="card-header">
              <span className="card-title">Agent status</span>
              <button className="btn btn-ghost btn-sm" onClick={runCheck} disabled={checking}>
                {checking ? 'Checking…' : '⟳ Check'}
              </button>
            </div>
            <div style={{ padding: '6px 0' }}>
              {AGENTS.map((a) => {
                const h = health?.[a.id];
                return (
                  <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 16px' }}>
                    <span className={`ui-dot ${!h ? 'wait' : h.ok ? 'ok' : 'down'}`} />
                    <span style={{ flex: 1, fontSize: 13.5, fontWeight: 600, color: 'var(--text-primary)' }}>{a.name}</span>
                    <span style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
                      {!h ? '…' : h.ok ? `${h.ms} ms` : h.detail}
                    </span>
                  </div>
                );
              })}
            </div>
            <div style={{ padding: '8px 16px 12px', fontSize: 12.5, color: 'var(--text-secondary)', borderTop: '1px solid var(--border)' }}>
              {liveCount == null ? 'Checking the agents…' : `${liveCount} of ${AGENTS.length} agents answering`}
            </div>
          </div>
        </div>
      </section>

      {/* ── Getting started ─────────────────────────────────── */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="ui-section-head">
          <h2>Getting started</h2>
          <p>Three steps from a repository to a refactoring report</p>
        </div>
        <div className="ui-steps">
          {steps.map((s, i) => (
            <button
              key={s.title}
              type="button"
              className={`ui-step ui-click ${s.done ? 'done' : i === nextIdx ? 'next' : ''} ${s.locked ? 'is-locked' : ''}`}
              onClick={() => !s.locked && onNavigate(s.page)}
              title={s.locked ? 'Load a repository first' : undefined}
            >
              <span className="n">{s.done ? '✓' : i + 1}</span>
              <span><b>{s.title}</b><small>{s.text}</small></span>
            </button>
          ))}
        </div>
      </section>

      {/* ── Pipeline ────────────────────────────────────────── */}
      <section style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div className="ui-section-head">
          <h2>The agent pipeline</h2>
          <p>{repoLoaded ? 'Click an agent to open it' : 'Agents unlock after a repository is loaded'}</p>
        </div>
        <div className="ov-pipeline" style={{ display: 'flex', alignItems: 'stretch', gap: 6 }}>
          {PIPELINE.map((a, i) => {
            const h = health?.[a.id];
            return (
              <div key={a.id} className="ov-pipeline-step" style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1, minWidth: 0 }}>
                <button
                  type="button"
                  className={`card ui-click ${repoLoaded ? '' : 'is-locked'}`}
                  onClick={() => repoLoaded && onNavigate(a.page)}
                  title={repoLoaded ? `Open the ${a.name} agent` : 'Load a repository first'}
                  style={{
                    flex: 1, minWidth: 0, textAlign: 'left', padding: '14px 14px 12px',
                    borderTop: `3px solid ${a.color}`, fontFamily: 'var(--font-body)', color: 'inherit',
                    display: 'flex', flexDirection: 'column', gap: 6, height: '100%',
                  }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{
                      width: 26, height: 26, borderRadius: 7, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      fontWeight: 800, fontSize: 13, color: a.color,
                      border: `1px solid color-mix(in srgb, ${a.color} 50%, transparent)`,
                      background: `color-mix(in srgb, ${a.color} 12%, transparent)`,
                    }}>{a.step}</span>
                    <b style={{ fontSize: 15, color: 'var(--text-primary)' }}>{a.name}</b>
                    <span className={`ui-dot ${!h ? 'wait' : h.ok ? 'ok' : 'down'}`} style={{ marginLeft: 'auto' }} title={h ? (h.ok ? 'Answering' : h.detail) : 'Checking'} />
                  </span>
                  <span style={{ fontSize: 12.5, color: 'var(--text-secondary)', lineHeight: 1.4 }}>{a.role}</span>
                  <span style={{ fontSize: 12.5, color: a.color, lineHeight: 1.4, marginTop: 'auto' }}>→ {a.out}</span>
                </button>
                {i < PIPELINE.length - 1 && (
                  <span className="ov-pipeline-arrow" style={{ fontSize: 16, color: 'var(--text-muted)', flexShrink: 0 }}>→</span>
                )}
              </div>
            );
          })}
        </div>
      </section>

      {/* ── At a glance + recent analyses ───────────────────── */}
      <div className="rg-side-340" style={{ gap: 16, alignItems: 'start' }}>
        <div className="card" style={{ overflow: 'hidden' }}>
          <div className="card-header">
            <span className="card-title">⏱ Recent analyses</span>
            {history.length > 0 && (
              <button className="btn btn-ghost btn-sm" onClick={() => onNavigate('dashboard')}>Dashboard →</button>
            )}
          </div>
          {history.length === 0 ? (
            <div className="empty-state" style={{ padding: 32 }}>
              <span className="empty-icon">📂</span>
              <p>No analyses yet. Load a repository to begin.</p>
              <button className="btn btn-primary btn-sm" style={{ marginTop: 10 }} onClick={() => onNavigate('repository')}>▶ Start first analysis</button>
            </div>
          ) : (
            <div>
              {history.slice(0, 6).map((h) => {
                const score = measuredScore(h);
                return (
                  <div key={h.id} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
                    <span style={{ fontSize: 16 }}>{h.source === 'github' ? '🔗' : '📁'}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.name}</div>
                      <div style={{ fontSize: 12.5, color: 'var(--text-muted)' }}>
                        {h.language} · {h.files_found} files{h.smells != null ? ` · ${h.smells} smells` : ''} · {fmtDate(h.date)}
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }} title={score == null ? 'Not scored by CUQA yet' : 'Average quality from CUQA'}>
                      <div style={{ fontSize: 16, fontWeight: 800, color: scoreColor(score) }}>{score == null ? '—' : score}</div>
                      <div style={{ fontSize: 11.5, color: 'var(--text-muted)' }}>quality</div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div className="rg-2" style={{ gap: 12 }}>
            <div className="ui-stat" style={{ '--c': 'var(--accent)' }}>
              <span className="k">Analyses</span>
              <span className="v">{history.length}</span>
              <span className="s">in this browser</span>
            </div>
            <div className="ui-stat" style={{ '--c': scoreColor(avgScore) }}>
              <span className="k">Avg. quality</span>
              <span className="v">{avgScore ?? '—'}</span>
              <span className="s">{scored.length ? `from ${scored.length} CUQA run(s)` : 'no CUQA score yet'}</span>
            </div>
          </div>
          <button type="button" className="ui-stat ui-click" style={{ '--c': 'var(--color-orchestrate)', textAlign: 'left', fontFamily: 'var(--font-body)' }} onClick={() => onNavigate('reports')}>
            <span className="k">Completed sessions <span>→</span></span>
            <span className="v">{sessions.length}</span>
            <span className="s">{sessions[0] ? `Last: ${sessions[0].repository?.name || sessions[0].target || 'session'}` : 'Finish a DIWO workflow to get a report'}</span>
          </button>
          <div className="card card-body" style={{ padding: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 8 }}>Languages</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <span className="pill pill-java">☕ Java — full pipeline</span>
              <span className="pill pill-python">🐍 Python — full pipeline</span>
              <span className="pill pill-c">⚙️ C — CUQA analysis</span>
            </div>
            <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 10, lineHeight: 1.5 }}>
              Parsers: javalang (Java), Python <code>ast</code>, tree-sitter (C). The ML smell model is for Java.
            </p>
          </div>
        </div>
      </div>

    </div>
  );
}
