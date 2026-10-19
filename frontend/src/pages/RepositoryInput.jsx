/**
 * RepositoryInput.jsx
 * - Source: GitHub link or ZIP upload, as two tabs with their own action
 * - Analysis options: quality threshold, severity filters, language context, mode
 *   (defaults come from Settings)
 * - Recent analyses: localStorage history; the score is CUQA's own average
 *   quality, filled in once the CUQA page has analysed the repository
 */

import { useState, useRef } from 'react';
import CUQAAgentService from '../services/cuqaAgentService';
import { cacheSourceFiles, readSourceFilesFromZip, saveSourceContext } from '../utils/sourceCache';
import { loadHistory, saveHistory, MAX_HISTORY, measuredScore, scoreColor } from '../utils/analysisHistory';
import { loadSettings } from '../utils/appSettings';
import './pages.css';

// What each filter really controls on the CUQA page
const SEVERITY_FILTERS = [
  { key: 'critical', label: 'High-severity smells', sub: 'For example Long Method, Large Class, Deep Nesting' },
  { key: 'naming',   label: 'Medium and low smells', sub: 'For example Magic Number, Too Many Parameters, naming' },
];

const MODES = ['Comprehensive Refactoring', 'Quick Scan', 'Security Focus', 'Performance Focus'];
const GITHUB_RE = /^https?:\/\/(www\.)?github\.com\/[^/\s]+\/[^/\s]+/i;

const fmtDate = (iso) => new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
const LANG_COLORS = { Python: '#3b82f6', Java: '#f59e0b', C: '#8b5cf6' };
const LANG_ICON = { Python: '🐍', Java: '☕', C: '⚙️' };

export default function RepositoryInput({ onLoaded }) {
  const defaults = loadSettings();
  const [source,        setSource]        = useState('github'); // 'github' | 'zip'
  const [githubUrl,     setGithubUrl]     = useState('');
  const [language,      setLanguage]      = useState(defaults.defaultLanguage);
  const [mode,          setMode]          = useState(defaults.defaultMode);
  const [threshold,     setThreshold]     = useState(defaults.defaultThreshold);
  const [filters,       setFilters]       = useState({ critical: defaults.enableCriticalFilter, naming: defaults.enableNamingFilter });
  const [dragging,      setDragging]      = useState(false);
  const [loading,       setLoading]       = useState(false);
  const [loadingWhat,   setLoadingWhat]   = useState('');
  const [error,         setError]         = useState(null);
  const [loaded,        setLoaded]        = useState(null);  // last successful load
  const [history,       setHistory]       = useState(() => loadHistory());
  const fileRef = useRef(null);

  const urlLooksValid = GITHUB_RE.test(githubUrl.trim());

  function toggleFilter(key) {
    setFilters(f => ({ ...f, [key]: !f[key] }));
  }

  /** The analysis config that travels with every load. */
  function buildConfig() {
    return {
      language_context: language,
      analysis_mode:    mode,
      threshold,
      severity_filters: { critical: filters.critical, naming: filters.naming },
    };
  }

  /** New history entry. The score stays empty until CUQA has analysed the repo. */
  function appendHistory(data, src) {
    const isPolyglot = data.is_polyglot || false;
    const primary    = data.primary_language || null;
    const displayLanguage = language !== 'All' ? language : isPolyglot ? 'Mixed' : (primary || 'Unknown');

    const entry = {
      id:                 Date.now(),
      name:               data.repo_name,
      source:             src,
      url:                src === 'github' ? githubUrl.trim() : null,
      language:           displayLanguage,
      language_breakdown: data.language_breakdown || {},
      detected_languages: data.detected_languages || [],
      primary_language:   primary,
      is_polyglot:        isPolyglot,
      date:               new Date().toISOString(),
      files_found:        data.files_found,
      score:              null,
      score_source:       'pending',
      threshold,
      filters:            { ...filters },
      mode,
    };
    const updated = [entry, ...loadHistory()].slice(0, MAX_HISTORY);
    saveHistory(updated);
    setHistory(updated);
    return entry;
  }

  function finishLoad(data, src) {
    setLoaded({
      name: data.repo_name,
      files: data.files_found,
      breakdown: data.language_breakdown || {},
      languages: data.detected_languages || [],
      primary: data.primary_language || null,
      polyglot: data.is_polyglot || false,
    });
    const entry = appendHistory(data, src);
    onLoaded?.({ ...data, config: buildConfig(), historyEntry: entry });
  }

  // ── ZIP upload ─────────────────────────────────────────────────────────────
  async function handleZip(file) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.zip')) { setError('Only .zip files are supported.'); return; }
    if (file.size > 500 * 1024 * 1024) { setError('The ZIP is larger than the 500 MB limit.'); return; }
    setError(null); setLoaded(null); setLoading(true); setLoadingWhat(`Uploading ${file.name}…`);

    try {
      const data = await CUQAAgentService.uploadZip(file);
      try {
        const sourceFiles = await readSourceFilesFromZip(file);
        cacheSourceFiles(sourceFiles, { repoName: data.repo_name || file.name, origin: 'uploaded_zip' });
      } catch {
        saveSourceContext({ repoName: data.repo_name || file.name, origin: 'uploaded_zip' });
      }
      finishLoad(data, 'zip');
    } catch (e) { setError(e.message); }
    finally { setLoading(false); setLoadingWhat(''); }
  }

  // ── GitHub load ────────────────────────────────────────────────────────────
  async function handleGitHub() {
    if (!githubUrl.trim()) { setError('Paste a GitHub repository link first.'); return; }
    if (!urlLooksValid) { setError('That does not look like a GitHub repository link (https://github.com/owner/repo).'); return; }
    setError(null); setLoaded(null); setLoading(true); setLoadingWhat('Downloading the repository from GitHub…');

    try {
      const data = await CUQAAgentService.loadGithubRepo(githubUrl);
      saveSourceContext({ repoName: data.repo_name || '', repoUrl: githubUrl.trim(), origin: 'github' });
      finishLoad(data, 'github');
    } catch (e) { setError(e.message); }
    finally { setLoading(false); setLoadingWhat(''); }
  }

  function reuse(entry) {
    if (entry.source === 'github') {
      setSource('github');
      setGithubUrl(entry.url || `https://github.com/${entry.name}`);
    } else {
      setSource('zip');
    }
    document.querySelector('.main-content')?.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // ── Export history as CSV ──────────────────────────────────────────────────
  function exportHistory() {
    if (!history.length) return;
    const header = 'Repository,Source,Language,Date,Files,Smells,Quality (CUQA),Mode,Threshold';
    const rows = history.map(r => {
      const score = measuredScore(r);
      return `"${r.name}","${r.source}","${r.language}","${fmtDate(r.date)}","${r.files_found}","${r.smells ?? ''}","${score ?? ''}","${r.mode}","${r.threshold}%"`;
    });
    const blob = new Blob([[header, ...rows].join('\n')], { type: 'text/csv' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = 'cuqa_analysis_history.csv';
    a.click();
    URL.revokeObjectURL(url);
  }

  const loadedTotal = loaded ? Object.values(loaded.breakdown).reduce((a, b) => a + b, 0) : 0;

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="page-container">

      {/* ── Intro ───────────────────────────────────────────────── */}
      <section className="ui-hero">
        <span className="ui-kicker">Step 1 · Input for the CUQA agent</span>
        <h1 className="ui-title">Load a repository</h1>
        <p className="ui-lead">
          Give RefactorIQ a Java, Python or C codebase. CUQA unpacks it, finds the source files and opens its
          analysis straight away.
        </p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 14 }}>
          <span className="ui-chip"><b>1</b> Choose a source</span>
          <span className="ui-chip"><b>2</b> Set the analysis options</span>
          <span className="ui-chip"><b>3</b> CUQA analyses it</span>
        </div>
      </section>

      {/* ── Main grid ───────────────────────────────────────────── */}
      <div className="rg-side-340" style={{ gap: 20, alignItems: 'start' }}>

        {/* LEFT: source */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="card">
            <div className="card-header"><span className="card-title">Repository source</span></div>
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div className="ui-tabs" role="tablist">
                <button role="tab" aria-selected={source === 'github'} className={`ui-tab ${source === 'github' ? 'on' : ''}`} onClick={() => setSource('github')}>🔗 GitHub link</button>
                <button role="tab" aria-selected={source === 'zip'} className={`ui-tab ${source === 'zip' ? 'on' : ''}`} onClick={() => setSource('zip')}>📁 Upload ZIP</button>
              </div>

              {source === 'github' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <div className="input-with-icon" style={{ flex: '1 1 260px' }}>
                      <span className="input-icon">🔗</span>
                      <input
                        id="github-url"
                        className="input"
                        placeholder="https://github.com/owner/repository"
                        value={githubUrl}
                        onChange={e => { setGithubUrl(e.target.value); setError(null); }}
                        onKeyDown={e => e.key === 'Enter' && handleGitHub()}
                        disabled={loading}
                      />
                    </div>
                    <button className="btn btn-primary" onClick={handleGitHub} disabled={loading || !githubUrl.trim()}>
                      {loading ? 'Loading…' : 'Load repository →'}
                    </button>
                  </div>
                  <div style={{ fontSize: 12.5, color: githubUrl && !urlLooksValid ? 'var(--color-medium)' : 'var(--text-muted)' }}>
                    {githubUrl && !urlLooksValid
                      ? 'Use the form https://github.com/owner/repository'
                      : 'Public repositories only. The default branch is downloaded (500 MB limit).'}
                  </div>
                </div>
              ) : (
                <div
                  className={`upload-zone ${dragging ? 'dragging' : ''}`}
                  onDragOver={e => { e.preventDefault(); setDragging(true); }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={e => { e.preventDefault(); setDragging(false); handleZip(e.dataTransfer.files?.[0]); }}
                  onClick={() => !loading && fileRef.current?.click()}
                  role="button"
                  tabIndex={0}
                  onKeyDown={e => { if (e.key === 'Enter') fileRef.current?.click(); }}
                  style={{ cursor: loading ? 'wait' : 'pointer' }}
                >
                  <span className="upload-zone-icon">{dragging ? '📥' : '📁'}</span>
                  <div className="upload-zone-title">{dragging ? 'Drop to upload' : 'Drag a ZIP here or click to choose'}</div>
                  <div className="upload-zone-sub">Up to 500 MB · .java, .py, .c and .h files are analysed</div>
                  <input ref={fileRef} type="file" accept=".zip" style={{ display: 'none' }}
                    onChange={e => { handleZip(e.target.files?.[0]); e.target.value = ''; }} />
                </div>
              )}

              {loading && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13.5, color: 'var(--text-secondary)' }}>
                  <div className="spinner" style={{ width: 18, height: 18, borderWidth: 2 }} />
                  {loadingWhat}
                </div>
              )}
              {error && <div className="alert alert-error">⚠ {error}</div>}
            </div>
          </div>

          {/* Loaded result */}
          {loaded && (
            <div className="card" style={{ borderColor: 'var(--border-accent)' }}>
              <div className="card-header">
                <span className="card-title" style={{ color: 'var(--color-ok)' }}>✓ {loaded.name} loaded</span>
                <span className="ui-chip"><b>{loaded.files}</b> source files</span>
              </div>
              <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
                {loaded.languages.length === 0 ? (
                  <div style={{ fontSize: 13, color: 'var(--color-medium)' }}>⚠ No supported source files found (.py, .java, .c, .h).</div>
                ) : loaded.languages.map(lang => {
                  const count = loaded.breakdown[lang] || 0;
                  const pct = loadedTotal ? Math.round((count / loadedTotal) * 100) : 0;
                  return (
                    <div key={lang}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                        <span style={{ fontWeight: 600, color: LANG_COLORS[lang] }}>
                          {LANG_ICON[lang]} {lang} {lang === loaded.primary && <small style={{ color: 'var(--color-ok)', marginLeft: 6 }}>PRIMARY</small>}
                        </span>
                        <span style={{ color: 'var(--text-muted)' }}>{count} files · {pct}%</span>
                      </div>
                      <div className="progress-bar"><div className="progress-fill" style={{ width: `${pct}%`, background: LANG_COLORS[lang] }} /></div>
                    </div>
                  );
                })}
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Opening the CUQA analysis…</div>
              </div>
            </div>
          )}
        </div>

        {/* RIGHT: options */}
        <div className="card">
          <div className="card-header"><span className="card-title">Analysis options</span></div>
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 18 }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <label className="field-label" htmlFor="threshold" style={{ margin: 0 }}>Quality threshold</label>
                <span className="badge badge-accent">{threshold}%</span>
              </div>
              <input id="threshold" type="range" min={0} max={100} value={threshold}
                onChange={e => setThreshold(+e.target.value)} style={{ width: '100%' }} />
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--text-muted)' }}>
                <span>Lenient</span><span>Strict</span>
              </div>
              <p style={{ fontSize: 12.5, color: 'var(--text-secondary)', marginTop: 6, lineHeight: 1.5 }}>
                Files scoring below {threshold}% are marked for refactoring on the CUQA page.
              </p>
            </div>

            <div>
              <label className="field-label">Show these smells</label>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {SEVERITY_FILTERS.map(({ key, label, sub }) => (
                  <label key={key} className="ui-switch">
                    <input type="checkbox" checked={filters[key]} onChange={() => toggleFilter(key)} />
                    <span className="track" />
                    <span><b>{label}</b><small>{sub}</small></span>
                  </label>
                ))}
              </div>
            </div>

            <div>
              <label className="field-label">Language context</label>
              <div className="toggle-group">
                {['All', 'Java', 'Python', 'C'].map(l => (
                  <button key={l} className={`toggle-item ${language === l ? 'active' : ''}`} onClick={() => setLanguage(l)}>{l}</button>
                ))}
              </div>
            </div>

            <div>
              <label className="field-label" htmlFor="mode">Analysis mode</label>
              <select id="mode" className="input" value={mode} onChange={e => setMode(e.target.value)} style={{ cursor: 'pointer' }}>
                {MODES.map(m => <option key={m}>{m}</option>)}
              </select>
            </div>

            <p className="ui-source">
              These options are saved with the analysis and used by the CUQA page. Change the defaults in Settings.
            </p>
          </div>
        </div>
      </div>

      {/* ── Recent analyses ─────────────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">⏱ Recent analyses</span>
          <button className="btn btn-outline btn-sm" onClick={exportHistory} disabled={!history.length}>↓ Export CSV</button>
        </div>

        {history.length === 0 ? (
          <div className="empty-state" style={{ padding: 32 }}>
            <span className="empty-icon">📂</span>
            <p>No analyses yet. Load a repository above to see it here.</p>
          </div>
        ) : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Repository</th>
                    <th>Language</th>
                    <th>Date</th>
                    <th>Files</th>
                    <th>Smells</th>
                    <th>Quality (CUQA)</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((r) => {
                    const score = measuredScore(r);
                    return (
                      <tr key={r.id}>
                        <td>
                          <span style={{ marginRight: 8 }}>{r.source === 'github' ? '🔗' : '📁'}</span>
                          <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{r.name}</span>
                        </td>
                        <td><span className={`badge badge-${(r.language || 'unknown').toLowerCase()}`}>{r.language}</span></td>
                        <td>{fmtDate(r.date)}</td>
                        <td>{(r.files_found || 0).toLocaleString()}</td>
                        <td>{r.smells ?? '—'}</td>
                        <td>
                          {score == null ? (
                            <span style={{ color: 'var(--text-muted)' }}>not scored</span>
                          ) : (
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <div className="progress-bar" style={{ width: 70, flex: 'none' }}>
                                <div className="progress-fill" style={{ width: `${score}%`, background: scoreColor(score) }} />
                              </div>
                              <b style={{ color: scoreColor(score) }}>{score}</b>
                            </div>
                          )}
                        </td>
                        <td>
                          <button className="btn btn-ghost btn-sm" onClick={() => reuse(r)}
                            title={r.source === 'github' ? 'Put this link back in the GitHub box' : 'Open the ZIP upload to upload it again'}>
                            {r.source === 'github' ? 'Load again' : 'Upload again'}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div style={{ padding: '12px 20px', textAlign: 'center', borderTop: '1px solid var(--border)', fontSize: 12.5, color: 'var(--text-muted)' }}>
              {history.length} {history.length === 1 ? 'analysis' : 'analyses'} stored in this browser
              {history.length >= MAX_HISTORY && ' (limit reached, oldest entries are removed)'}
            </div>
          </>
        )}
      </div>

    </div>
  );
}
