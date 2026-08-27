/**
 * Overview.jsx — RefactorIQ Landing / Home page
 * - Full-width (no maxWidth constraint) so it fills the screen
 * - Pipeline steps match the real agent architecture
 * - "Telemetry" reads from localStorage history (real data)
 * - Hardcoded bar chart replaced with real analysis score history
 */

import { useState, useEffect } from 'react';
import { getEnv } from '../config/env';

const API = getEnv('VITE_CUQA_AGENT_API_URL', getEnv('VITE_CUQA_API_URL', 'http://localhost:8080')).replace(/\/+$/, '');
const HISTORY_KEY = 'cuqa_analysis_history';

const PIPELINE_STEPS = [
  { id: 'repo',    icon: '🔗', label: 'Repository\nInput',         color: 'var(--text-secondary)',   sub: 'ZIP / GITHUB' },
  { id: 'cuqa',   icon: '🔍', label: 'CUQA\nAgent',               color: 'var(--color-cuqa)',        sub: 'CODE QUALITY\nANALYST' },
  { id: 'rdp',    icon: '🧠', label: 'RDP\nAgent',                 color: 'var(--color-rdp)',         sub: 'REFACTORING\nPLANNER' },
  { id: 'trans',  icon: '⚡', label: 'Transformation\nAgent',      color: 'var(--color-transform)',   sub: 'SCTVA\nSAFE EDITS' },
  { id: 'orch',   icon: '🎛️', label: 'DIWO\nAgent',               color: 'var(--color-orchestrate)', sub: 'ORCHESTRATION\n& FEEDBACK' },
  { id: 'out',    icon: '✅', label: 'Refactored\nOutput',         color: 'var(--color-ok)',          sub: 'VALIDATED\nCODE' },
];

function PipelineCard({ step, isActive, onClick }) {
  return (
    <div
      onClick={onClick}
      style={{
        background: 'var(--bg-card)',
        border: `1px solid ${isActive ? step.color : 'var(--border)'}`,
        borderRadius: 'var(--r-md)',
        padding: '16px 12px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 6,
        flex: 1,
        minWidth: 90,
        textAlign: 'center',
        boxShadow: isActive ? `0 0 18px ${step.color}28` : 'none',
        transition: 'all 0.2s',
        cursor: onClick ? 'pointer' : 'default',
      }}
    >
      <div style={{
        width: 40, height: 40,
        borderRadius: 'var(--r-md)',
        background: `${step.color}18`,
        border: `1px solid ${step.color}40`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 18,
      }}>
        {step.icon}
      </div>
      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)', whiteSpace: 'pre-line', lineHeight: 1.3 }}>
        {step.label}
      </div>
      {step.sub && (
        <div style={{ fontSize: 9, letterSpacing: '0.7px', color: step.color, fontWeight: 600, whiteSpace: 'pre-line', lineHeight: 1.3 }}>
          {step.sub}
        </div>
      )}
    </div>
  );
}

/** Read analysis history from localStorage */
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]'); }
  catch { return []; }
}

function scoreColor(s) {
  if (s >= 80) return '#22c55e';
  if (s >= 60) return '#f59e0b';
  return '#ef4444';
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

// ── Capability cards ─────────────────────────────────────────────────────────
const CAPABILITIES = [
  {
    icon: '🔍',
    color: 'var(--color-cuqa)',
    title: 'CUQA Agent',
    sub: 'Code Understanding & Quality Assessment',
    desc: 'Parses Python, Java, and C source files via AST analysis. Detects code smells (memory leaks, circular deps, naming violations) and computes per-file quality scores.',
    badges: ['Python 3.9+', 'Java SE 8+', 'C/C++'],
  },
  {
    icon: '🧠',
    color: 'var(--color-rdp)',
    title: 'RDP Agent',
    sub: 'Refactoring Decision & Planning',
    desc: 'Consumes CUQA quality reports and generates a prioritised refactoring plan. Uses rule-based heuristics aligned to the SCTVA transformation catalogue.',
    badges: ['AST-driven', 'Rule-based', 'Priority ranking'],
  },
  {
    icon: '⚡',
    color: 'var(--color-transform)',
    title: 'Transformation Agent',
    sub: 'Safe Code Transformation & Validation',
    desc: 'Applies surgical code transformations derived from the RDP plan. Each edit is validated against the original AST signature to prevent regressions.',
    badges: ['SCTVA', 'Regression-safe', 'AST diff'],
  },
  {
    icon: '🎛️',
    color: 'var(--color-orchestrate)',
    title: 'DIWO Agent',
    sub: 'Developer-in-the-Loop Workflow Orchestration',
    desc: 'Manages the end-to-end pipeline with human-in-the-loop checkpoints. Collects developer feedback to improve future refactoring decisions.',
    badges: ['Human-in-loop', 'Audit trail', 'Feedback DB'],
  },
];

export default function Overview({ onNavigate }) {
  const [history,   setHistory]   = useState([]);
  const [backendOk, setBackendOk] = useState(null);
  const [wsLoaded,  setWsLoaded]  = useState(false);

  useEffect(() => {
    setHistory(loadHistory());

    // Check backend health & workspace status
    fetch(`${API}/api/health`)
      .then(r => r.json())
      .then(d => {
        setBackendOk(d.status === 'ok');
        setWsLoaded(!!d.workspace_loaded);
      })
      .catch(() => setBackendOk(false));
  }, []);

  const totalRuns    = history.length;
  const avgScore     = totalRuns > 0
    ? Math.round(history.reduce((s, h) => s + h.score, 0) / totalRuns)
    : null;
  const lastEntry    = history[0] ?? null;
  const recentFive   = history.slice(0, 6);

  return (
    <div className="page-container">

      {/* ── Hero ─────────────────────────────────────────────── */}
      <div style={{ padding: '4px 0 0' }}>
        <span className="badge badge-accent" style={{ marginBottom: 16, display: 'inline-flex', gap: 6 }}>
          ⚡ AUTOMATED REFACTORING PLATFORM
        </span>

        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 24, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 280 }}>
            <h1 style={{ fontSize: 'clamp(22px, 3vw, 36px)', fontWeight: 800, lineHeight: 1.2, color: 'var(--text-primary)' }}>
              <span style={{ color: 'var(--accent)' }}>RefactorIQ</span>
              {' — '}Agentic Code Modernisation Platform
            </h1>
            <p style={{ marginTop: 12, fontSize: 13, color: 'var(--text-secondary)', maxWidth: 520, lineHeight: 1.7 }}>
              A multi-agent AI system for detecting, planning, and applying refactoring
              transformations across legacy Java, Python, and C codebases — with developer-in-the-loop
              validation at every stage.
            </p>
            <div style={{ display: 'flex', gap: 10, marginTop: 20, flexWrap: 'wrap' }}>
              <button className="btn btn-primary" onClick={() => onNavigate('repository')}>
                ▶ Start Analysis
              </button>
              <button className="btn btn-outline" onClick={() => onNavigate('cuqa')}>
                View CUQA Agent →
              </button>
              <button className="btn btn-outline" onClick={() => onNavigate('dashboard')}>
                📊 Dashboard
              </button>
            </div>
          </div>

          {/* ── Live status strip ── */}
          <div style={{
            display: 'flex', flexDirection: 'column', gap: 10,
            minWidth: 200, flexShrink: 0,
          }}>
            {/* Backend */}
            <div className="card card-body" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                background: backendOk === null ? '#f59e0b' : backendOk ? '#22c55e' : '#ef4444',
                boxShadow: backendOk ? '0 0 6px #22c55e' : 'none',
              }} />
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)' }}>
                  {backendOk === null ? 'Connecting…' : backendOk ? 'CUQA Backend Live' : 'Backend Offline'}
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                  {wsLoaded ? 'Workspace loaded' : 'No workspace active'}
                </div>
              </div>
            </div>
            {/* Analyses run */}
            <div className="card card-body" style={{ padding: '12px 16px' }}>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>ANALYSES RUN</div>
              <div style={{ fontSize: 22, fontWeight: 800, color: 'var(--accent)' }}>{totalRuns}</div>
              {lastEntry && (
                <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginTop: 2 }}>
                  Last: {lastEntry.name} · {fmtDate(lastEntry.date)}
                </div>
              )}
            </div>
            {/* Avg score */}
            <div className="card card-body" style={{ padding: '12px 16px' }}>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 2 }}>AVG QUALITY SCORE</div>
              <div style={{ fontSize: 22, fontWeight: 800, color: avgScore != null ? scoreColor(avgScore) : 'var(--text-muted)' }}>
                {avgScore != null ? `${avgScore}%` : '—'}
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
                {totalRuns > 0 ? `across ${totalRuns} repo${totalRuns > 1 ? 's' : ''}` : 'run an analysis first'}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* ── Pipeline ──────────────────────────────────────────── */}
      <div>
        <h2 style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 6 }}>
          Autonomous Multi-Agent Pipeline
        </h2>
        <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 14 }}>
          Five specialised agents work in sequence — each phase feeds structured JSON into the next.
        </p>
        <div style={{ display: 'flex', alignItems: 'stretch', gap: 6 }}>
          {PIPELINE_STEPS.map((step, i) => (
            <div key={step.id} style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 1 }}>
              <PipelineCard
                step={step}
                isActive={step.id === 'cuqa'}
                onClick={() => {
                  const map = { repo: 'repository', cuqa: 'cuqa', rdp: 'rdp', trans: 'transform', orch: 'orchestrate' };
                  if (map[step.id]) onNavigate(map[step.id]);
                }}
              />
              {i < PIPELINE_STEPS.length - 1 && (
                <span style={{ fontSize: 14, color: 'var(--text-muted)', flexShrink: 0 }}>→</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── Two-col: Capabilities + Recent Analyses ───────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 16, alignItems: 'start' }}>

        {/* Capability cards grid */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
          {CAPABILITIES.map(cap => (
            <div key={cap.title} className="card" style={{ padding: 18 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
                <div style={{
                  width: 36, height: 36, borderRadius: 'var(--r-md)',
                  background: `${cap.color}18`, border: `1px solid ${cap.color}40`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16, flexShrink: 0,
                }}>
                  {cap.icon}
                </div>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>{cap.title}</div>
                  <div style={{ fontSize: 10, color: cap.color, fontWeight: 600, letterSpacing: '0.5px' }}>{cap.sub}</div>
                </div>
              </div>
              <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.65, marginBottom: 10 }}>{cap.desc}</p>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {cap.badges.map(b => (
                  <span key={b} style={{
                    fontSize: 9, fontWeight: 600, letterSpacing: '0.5px',
                    padding: '2px 7px', borderRadius: 'var(--r-full)',
                    background: `${cap.color}18`, color: cap.color, border: `1px solid ${cap.color}30`,
                  }}>
                    {b}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Recent analyses from localStorage */}
        <div className="card" style={{ overflow: 'hidden' }}>
          <div className="card-header">
            <span className="card-title">⏱ Recent Analyses</span>
            {totalRuns > 0 && (
              <button className="btn btn-ghost btn-sm" onClick={() => onNavigate('repository')}>
                View All →
              </button>
            )}
          </div>

          {totalRuns === 0 ? (
            <div style={{ padding: '28px 20px', textAlign: 'center' }}>
              <div style={{ fontSize: 32, marginBottom: 10 }}>📂</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>No analyses yet</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 16 }}>
                Load a repository to begin
              </div>
              <button className="btn btn-primary" style={{ fontSize: 11 }} onClick={() => onNavigate('repository')}>
                ▶ Start First Analysis
              </button>
            </div>
          ) : (
            <div style={{ padding: '8px 0' }}>
              {/* Mini bar chart of recent scores */}
              <div style={{ padding: '8px 16px 4px', borderBottom: '1px solid var(--border)', marginBottom: 4 }}>
                <div style={{ fontSize: 10, color: 'var(--text-muted)', marginBottom: 8, letterSpacing: '0.5px' }}>
                  SCORE HISTORY
                </div>
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 5, height: 48 }}>
                  {recentFive.slice().reverse().map((h, i) => (
                    <div key={h.id} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
                      <div style={{
                        width: '100%',
                        height: `${h.score}%`,
                        background: h.color,
                        borderRadius: '3px 3px 0 0',
                        opacity: i === recentFive.length - 1 ? 1 : 0.6,
                        transition: 'height 0.4s ease',
                        minHeight: 3,
                      }} />
                    </div>
                  ))}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
                  <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>oldest</span>
                  <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>latest</span>
                </div>
              </div>

              {/* List of recent entries */}
              {recentFive.map(h => (
                <div key={h.id} style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  padding: '8px 16px',
                  borderBottom: '1px solid var(--border)',
                }}>
                  <span style={{ fontSize: 14, flexShrink: 0 }}>
                    {h.source === 'github' ? '🔗' : '📁'}
                  </span>
                  <div style={{ flex: 1, overflow: 'hidden' }}>
                    <div style={{
                      fontSize: 11, fontWeight: 600, color: 'var(--text-primary)',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {h.name}
                    </div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                      {h.language} · {h.files_found} files · {fmtDate(h.date)}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right', flexShrink: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 700, color: h.color }}>{h.score}%</div>
                    <div style={{ fontSize: 9, color: 'var(--text-muted)' }}>score</div>
                  </div>
                </div>
              ))}

              <div style={{ padding: '10px 16px', textAlign: 'center' }}>
                <button
                  className="btn btn-outline btn-sm"
                  style={{ width: '100%', fontSize: 11 }}
                  onClick={() => onNavigate('cuqa')}
                >
                  Open CUQA Agent →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Bottom info row ───────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
        <div className="card" style={{ padding: 16, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <span style={{ fontSize: 20, flexShrink: 0 }}>🔬</span>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>Research Context</div>
            <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              RefactorIQ is the implementation artefact for research project R26-SE-008, targeting
              fully-automated legacy code modernisation with academic-grade evaluation metrics.
            </p>
          </div>
        </div>
        <div className="card" style={{ padding: 16, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <span style={{ fontSize: 20, flexShrink: 0 }}>🌐</span>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>Supported Languages</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
              {[
                { label: 'Python', color: '#3b82f6' },
                { label: 'Java', color: '#f59e0b' },
                { label: 'C / Header', color: '#8b5cf6' },
              ].map(l => (
                <span key={l.label} style={{
                  fontSize: 10, fontWeight: 600, padding: '3px 8px',
                  borderRadius: 'var(--r-full)', background: `${l.color}18`,
                  color: l.color, border: `1px solid ${l.color}30`,
                }}>
                  {l.label}
                </span>
              ))}
            </div>
            <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6, marginTop: 6 }}>
              AST parsing via tree-sitter (C), javalang (Java), and Python's built-in ast module.
            </p>
          </div>
        </div>
        <div className="card" style={{ padding: 16, display: 'flex', alignItems: 'flex-start', gap: 12 }}>
          <span style={{ fontSize: 20, flexShrink: 0 }}>💻</span>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>Local Deployment</div>
            <p style={{ fontSize: 11, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Runs fully on-premise. CUQA backend served by FastAPI on port 8080.
              No data leaves your machine — ideal for sensitive enterprise codebases.
            </p>
            <div style={{ marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--accent)', background: 'var(--bg-elevated)', borderRadius: 4, padding: '4px 8px' }}>
              python agents/cuqa_agent/src/main.py
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}
