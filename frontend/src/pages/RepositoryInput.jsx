/**
 * RepositoryInput.jsx
 * - Left: GitHub URL, ZIP upload, language context, analysis mode, START button
 * - Right: Analysis threshold slider, severity filters (critical + naming only), info box
 * - Bottom: Recent Analysis table (real data from localStorage)
 *
 * Hardcoded RECENT data replaced with localStorage-persisted history.
 * Analysis threshold and severity filters are now passed to the backend
 * and stored alongside each analysis entry.
 */

import { useState, useRef, useEffect } from 'react';

const API = 'http://localhost:8080';
const HISTORY_KEY = 'cuqa_analysis_history';
const MAX_HISTORY  = 20;

// ── Severity filter definitions (Optimisation Suggestions removed) ──────────
const SEVERITY_FILTERS = [
  {
    key:   'critical',
    label: 'Critical Structural Issues',
    sub:   'Memory leaks, circular dependencies',
  },
  {
    key:   'naming',
    label: 'Naming & Style Violations',
    sub:   'PEP8, Java Conventions, C Coding Practices',
  },
];

// ── localStorage helpers ────────────────────────────────────────────────────
function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveHistory(entries) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, MAX_HISTORY)));
}

/**
 * Derive a quality score (0-100) from API response data.
 * When the full quality report is not yet available we use heuristics
 * based on file count: more files → larger codebase → score stays moderate.
 */
function deriveScore(data) {
  if (data.quality_score !== undefined) return Math.round(data.quality_score);
  // Heuristic fallback: 70 base, penalise for large repos
  const files = data.files_found || 0;
  const penalty = Math.min(30, Math.floor(files / 40));
  return Math.max(40, 70 - penalty);
}

/** Pick a colour band based on a 0-100 score. */
function scoreColor(score) {
  if (score >= 80) return '#22c55e';
  if (score >= 60) return '#f59e0b';
  if (score >= 40) return '#ef4444';
  return '#dc2626';
}

/** Format a Date object as a readable string, e.g. "Jul 28, 2026". */
function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ── Language badge colour map (for CSS classes) ──────────────────────────────
const LANG_LABEL = {
  All:    'All',
  Java:   'Java',
  Python: 'Python',
  C:      'C',
};

// ────────────────────────────────────────────────────────────────────────────
export default function RepositoryInput({ onLoaded }) {
  const [githubUrl,     setGithubUrl]     = useState('');
  const [language,      setLanguage]      = useState('All');
  const [mode,          setMode]          = useState('Comprehensive Refactoring');
  const [threshold,     setThreshold]     = useState(75);
  const [filters,       setFilters]       = useState({ critical: true, naming: true });
  const [dragging,      setDragging]      = useState(false);
  const [loading,       setLoading]       = useState(false);
  const [error,         setError]         = useState(null);
  const [success,       setSuccess]       = useState(null);
  // detectedLangs: { breakdown:{Python:12,Java:3}, detected_languages:['Python','Java'], primary_language:'Python', is_polyglot:true }
  const [detectedLangs, setDetectedLangs] = useState(null);
  const [history,       setHistory]       = useState([]);
  const fileRef = useRef(null);

  // Load persisted history on mount
  useEffect(() => {
    setHistory(loadHistory());
  }, []);

  function toggleFilter(key) {
    setFilters(f => ({ ...f, [key]: !f[key] }));
  }

  /** Build the analysis config payload that travels with every request. */
  function buildConfig() {
    return {
      language_context: language,
      analysis_mode:    mode,
      threshold,
      severity_filters: {
        critical: filters.critical,
        naming:   filters.naming,
      },
    };
  }

  /** Append a new entry to the localStorage history and update React state. */
  function appendHistory(data, source) {
    const score = deriveScore(data);

    // Determine the display language label:
    // 1. If user explicitly chose a single language, use it.
    // 2. Otherwise use what the backend detected.
    const breakdown   = data.language_breakdown   || {};  // {Python:12, Java:3}
    const detectedArr = data.detected_languages   || [];  // ['Python', 'Java']
    const primary     = data.primary_language     || null;
    const isPolyglot  = data.is_polyglot          || false;

    let displayLanguage;
    if (language !== 'All') {
      // User forced a specific context
      displayLanguage = language;
    } else if (isPolyglot) {
      displayLanguage = 'Mixed';
    } else if (primary) {
      displayLanguage = primary;
    } else {
      displayLanguage = 'Unknown';
    }

    const entry = {
      id:                  Date.now(),
      name:                data.repo_name,
      source,                               // "zip" | "github"
      language:            displayLanguage,
      language_breakdown:  breakdown,
      detected_languages:  detectedArr,
      primary_language:    primary,
      is_polyglot:         isPolyglot,
      date:                new Date().toISOString(),
      files_found:         data.files_found,
      score,
      color:               scoreColor(score),
      threshold,
      filters:             { ...filters },
      mode,
    };
    const updated = [entry, ...loadHistory()].slice(0, MAX_HISTORY);
    saveHistory(updated);
    setHistory(updated);
    return entry;
  }

  // ── ZIP upload ─────────────────────────────────────────────────────────────
  async function handleZip(file) {
    if (!file?.name.endsWith('.zip')) { setError('Only .zip files supported.'); return; }
    setError(null); setSuccess(null); setDetectedLangs(null); setLoading(true);

    const form = new FormData();
    form.append('file', file);
    form.append('config', JSON.stringify(buildConfig()));

    try {
      const res  = await fetch(`${API}/api/upload-zip`, { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Upload failed.');

      // Store detected language info in state so we can display it
      const langInfo = {
        breakdown:          data.language_breakdown  || {},
        detected_languages: data.detected_languages || [],
        primary_language:   data.primary_language   || null,
        is_polyglot:        data.is_polyglot         || false,
      };
      setDetectedLangs(langInfo);

      const entry = appendHistory(data, 'zip');
      setSuccess(`✔ "${data.repo_name}" loaded — ${data.files_found} source files detected.`);
      onLoaded?.({ ...data, config: buildConfig(), historyEntry: entry });
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  // ── GitHub load ────────────────────────────────────────────────────────────
  async function handleGitHub() {
    if (!githubUrl.trim()) { setError('Enter a GitHub URL.'); return; }
    setError(null); setSuccess(null); setDetectedLangs(null); setLoading(true);

    try {
      const res  = await fetch(`${API}/api/github-repo`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          url: githubUrl.trim(),
          config: buildConfig(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load repo.');

      // Store detected language info in state so we can display it
      const langInfo = {
        breakdown:          data.language_breakdown  || {},
        detected_languages: data.detected_languages || [],
        primary_language:   data.primary_language   || null,
        is_polyglot:        data.is_polyglot         || false,
      };
      setDetectedLangs(langInfo);

      const entry = appendHistory(data, 'github');
      setSuccess(`✔ "${data.repo_name}" loaded — ${data.files_found} source files detected.`);
      onLoaded?.({ ...data, config: buildConfig(), historyEntry: entry });
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  // ── Export history as CSV ──────────────────────────────────────────────────
  function exportHistory() {
    if (!history.length) return;
    const header = 'Repository,Language,Date,Files,Score,Mode,Threshold';
    const rows   = history.map(r =>
      `"${r.name}","${r.language}","${fmtDate(r.date)}","${r.files_found}","${r.score}%","${r.mode}","${r.threshold}%"`
    );
    const csv  = [header, ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = 'cuqa_analysis_history.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="page-container">

      {/* ── Header ──────────────────────────────────────────────── */}
      <div>
        <h1 className="page-title">Repository Input</h1>
        <p className="page-subtitle">
          Connect your codebase for agentic refactoring. <strong style={{ color: 'var(--accent)' }}>RefactorIQ</strong> analyses architectural
          patterns and applies deep-tech transformations across Java, Python, and C.
        </p>
      </div>

      {/* ── Main grid ───────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 300px', gap: 20, alignItems: 'start' }}>

        {/* LEFT PANEL */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* GitHub URL */}
          <div className="card card-body" style={{ padding: 20 }}>
            <label className="field-label">Repository Source</label>
            <div className="input-with-icon" style={{ marginBottom: 16 }}>
              <span className="input-icon">🔗</span>
              <input
                id="github-url"
                className="input"
                placeholder="https://github.com/organization/repository"
                value={githubUrl}
                onChange={e => setGithubUrl(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleGitHub()}
              />
            </div>

            {/* ZIP Upload */}
            <div
              className={`upload-zone ${dragging ? 'dragging' : ''}`}
              onDragOver={e => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={e => { e.preventDefault(); setDragging(false); handleZip(e.dataTransfer.files?.[0]); }}
              onClick={() => fileRef.current?.click()}
            >
              <span className="upload-zone-icon">📁</span>
              <div className="upload-zone-title">Drag &amp; Drop or Upload ZIP</div>
              <div className="upload-zone-sub">Local projects up to 250MB</div>
              <input ref={fileRef} type="file" accept=".zip" style={{ display: 'none' }}
                onChange={e => handleZip(e.target.files?.[0])} />
            </div>
          </div>

          {/* Language + Mode */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div className="card card-body" style={{ padding: 20 }}>
              <label className="field-label">Language Context</label>
              <div className="toggle-group">
                {['Java', 'Python', 'C', 'All'].map(l => (
                  <button
                    key={l}
                    className={`toggle-item ${language === l ? 'active' : ''}`}
                    onClick={() => setLanguage(l)}
                  >{l}</button>
                ))}
              </div>
            </div>

            <div className="card card-body" style={{ padding: 20 }}>
              <label className="field-label">Analysis Mode</label>
              <select
                className="input"
                value={mode}
                onChange={e => setMode(e.target.value)}
                style={{ cursor: 'pointer' }}
              >
                <option>Comprehensive Refactoring</option>
                <option>Quick Scan</option>
                <option>Security Focus</option>
                <option>Performance Focus</option>
              </select>
            </div>
          </div>

          {/* Error alert */}
          {error && <div className="alert alert-error">⚠ {error}</div>}

          {/* Language Detection Result Panel */}
          {success && detectedLangs && (
            <div style={{
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border-accent)',
              borderRadius: 'var(--r-sm)',
              padding: 14,
            }}>
              {/* Header row */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ color: '#22c55e', fontSize: 14 }}>✔</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
                    Repository loaded — {success.split('—')[1]?.trim()}
                  </span>
                </div>
                {detectedLangs.is_polyglot && (
                  <span style={{
                    fontSize: 9, fontWeight: 700, letterSpacing: '0.6px',
                    padding: '2px 7px', borderRadius: 'var(--r-full)',
                    background: 'rgba(139,92,246,0.2)', color: '#a78bfa',
                    border: '1px solid rgba(139,92,246,0.4)',
                  }}>
                    POLYGLOT
                  </span>
                )}
              </div>

              {/* Language breakdown */}
              {detectedLangs.detected_languages.length === 0 ? (
                <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                  ⚠ No supported source files detected (.py, .java, .c, .h)
                </div>
              ) : (
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-muted)', letterSpacing: '0.5px', marginBottom: 6, textTransform: 'uppercase' }}>
                    Detected Languages
                  </div>
                  {/* Bar chart for each language */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
                    {(() => {
                      const breakdown = detectedLangs.breakdown;
                      const total = Object.values(breakdown).reduce((a, b) => a + b, 0);
                      const LANG_COLORS = { Python: '#3b82f6', Java: '#f59e0b', C: '#8b5cf6' };
                      return detectedLangs.detected_languages.map(lang => {
                        const count = breakdown[lang] || 0;
                        const pct   = total > 0 ? Math.round((count / total) * 100) : 0;
                        const color = LANG_COLORS[lang] || 'var(--accent)';
                        return (
                          <div key={lang}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                <span style={{ fontSize: 11, fontWeight: 600, color }}>
                                  {lang === 'Python' ? '🐍' : lang === 'Java' ? '☕' : '⚙️'} {lang}
                                </span>
                                {lang === detectedLangs.primary_language && (
                                  <span style={{ fontSize: 9, color: '#22c55e', fontWeight: 600 }}>PRIMARY</span>
                                )}
                              </div>
                              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                                {count} file{count !== 1 ? 's' : ''} ({pct}%)
                              </span>
                            </div>
                            <div style={{ height: 5, background: 'var(--bg-base)', borderRadius: 3, overflow: 'hidden' }}>
                              <div style={{
                                height: '100%',
                                width: `${pct}%`,
                                background: color,
                                borderRadius: 3,
                                transition: 'width 0.5s ease',
                              }} />
                            </div>
                          </div>
                        );
                      });
                    })()}
                  </div>

                  {/* Inline language chips */}
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
                    {detectedLangs.detected_languages.map(lang => {
                      const LANG_COLORS = { Python: '#3b82f6', Java: '#f59e0b', C: '#8b5cf6' };
                      const c = LANG_COLORS[lang] || 'var(--accent)';
                      return (
                        <span key={lang} style={{
                          fontSize: 10, fontWeight: 600, padding: '2px 8px',
                          borderRadius: 'var(--r-full)', background: `${c}18`,
                          color: c, border: `1px solid ${c}40`,
                        }}>
                          {lang}
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Simple success without lang data */}
          {success && !detectedLangs && (
            <div className="alert alert-success">{success}</div>
          )}

          {/* CTA Button */}
          <button
            className="btn btn-primary-full"
            onClick={handleGitHub}
            disabled={loading}
          >
            {loading
              ? <><div className="spinner" style={{ width: 18, height: 18, borderWidth: 2 }} /> Analysing…</>
              : <>🚀 START SYSTEM ANALYSIS</>
            }
          </button>
        </div>


        {/* RIGHT PANEL */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Analysis Threshold */}
          <div className="card card-body" style={{ padding: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <label className="field-label" style={{ margin: 0 }}>Analysis Threshold</label>
              <span className="badge badge-accent">{threshold}%</span>
            </div>
            <input
              type="range" min={0} max={100}
              value={threshold}
              onChange={e => setThreshold(+e.target.value)}
              style={{ width: '100%', marginBottom: 4 }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>LENIENT</span>
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>STRICT</span>
            </div>
            <p style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 }}>
              Files with a quality score below this threshold will be
              flagged for refactoring. Applied per analysis run.
            </p>
          </div>

          {/* Severity Filter — Optimisation Suggestions removed */}
          <div className="card card-body" style={{ padding: 20 }}>
            <label className="field-label">Severity Filter</label>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {SEVERITY_FILTERS.map(({ key, label, sub }) => (
                <div
                  key={key}
                  className={`check-row ${filters[key] ? 'checked' : ''}`}
                  onClick={() => toggleFilter(key)}
                >
                  <div className="check-box">{filters[key] ? '✓' : ''}</div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>{label}</div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>{sub}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Info box */}
          <div style={{
            background: 'var(--accent-muted)', border: '1px solid var(--border-accent)',
            borderRadius: 'var(--r-sm)', padding: '12px 14px',
            display: 'flex', gap: 10, alignItems: 'flex-start',
          }}>
            <span style={{ fontSize: 16, flexShrink: 0, marginTop: 1 }}>ℹ</span>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.6 }}>
              Settings applied here will calibrate the{' '}
              <strong style={{ color: 'var(--accent)' }}>CUQA Agent's</strong>{' '}
              reasoning engine during the pre-processing phase.
            </p>
          </div>
        </div>
      </div>

      {/* ── Recent Analysis ─────────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">⏱ Recent Analysis</span>
          <button
            className="btn btn-outline btn-sm"
            onClick={exportHistory}
            disabled={!history.length}
            title={history.length ? 'Export history as CSV' : 'No history to export'}
          >
            Export All
          </button>
        </div>

        {history.length === 0 ? (
          /* Empty state — shown until the first repo is analysed */
          <div style={{ padding: '32px 20px', textAlign: 'center' }}>
            <div style={{ fontSize: 36, marginBottom: 10 }}>📂</div>
            <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 4 }}>
              No analyses yet
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Load a repository above to see results here.
            </div>
          </div>
        ) : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Repository Name</th>
                    <th>Language</th>
                    <th>Date</th>
                    <th>Files</th>
                    <th>Threshold</th>
                    <th>Last Score</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ color: 'var(--text-muted)' }}>
                            {r.source === 'github' ? '🔗' : '📁'}
                          </span>
                          <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{r.name}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`badge badge-${r.language.toLowerCase()}`}>{r.language}</span>
                      </td>
                      <td>{fmtDate(r.date)}</td>
                      <td>{r.files_found.toLocaleString()}</td>
                      <td>
                        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{r.threshold}%</span>
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <div className="progress-bar" style={{ width: 80, flex: 'none' }}>
                            <div
                              className="progress-fill"
                              style={{ width: `${r.score}%`, background: r.color }}
                            />
                          </div>
                          <span style={{ fontSize: 12, fontWeight: 700, color: r.color }}>{r.score}%</span>
                        </div>
                      </td>
                      <td>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => {
                            // Re-populate URL input so the user can quickly reload
                            if (r.source === 'github') setGithubUrl(`https://github.com/${r.name}`);
                          }}
                          title="Re-load this repository"
                        >
                          View →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ padding: '14px 20px', textAlign: 'center', borderTop: '1px solid var(--border)' }}>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                {history.length} {history.length === 1 ? 'analysis' : 'analyses'} stored locally
                {history.length >= MAX_HISTORY && ' (limit reached — oldest entries are removed)'}
              </span>
            </div>
          </>
        )}
      </div>

    </div>
  );
}
