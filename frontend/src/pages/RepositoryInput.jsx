/**
 * RepositoryInput.jsx
 * Matches the screenshot design exactly:
 * - Left: GitHub URL, ZIP upload, language context, analysis mode, START button
 * - Right: Analysis threshold slider, severity filters, info box
 * - Bottom: Recent Analysis table
 */

import { useState, useRef } from 'react';

const API = 'http://localhost:8080';

const RECENT = [
  { name: 'LegacyOrderSys',  lang: 'Java',   date: 'Oct 24, 2023', files: '1,242',      score: 42,  color: '#ef4444' },
  { name: 'InventoryAPI',    lang: 'Python',  date: 'Oct 21, 2023', files: '458',        score: 88,  color: '#00d4e8' },
  { name: 'E-Shop-Core',     lang: 'Java',    date: 'Oct 15, 2023', files: '3,120',      score: 65,  color: '#8b5cf6' },
  { name: 'tmux',            lang: 'C',       date: 'Recent',       files: 'C/Header',   score: 70,  color: '#22c55e' },
];

export default function RepositoryInput({ onLoaded }) {
  const [githubUrl,   setGithubUrl]   = useState('');
  const [language,    setLanguage]    = useState('All');
  const [mode,        setMode]        = useState('Comprehensive Refactoring');
  const [threshold,   setThreshold]   = useState(75);
  const [filters,     setFilters]     = useState({ critical: true, naming: true, optimise: false });
  const [dragging,    setDragging]    = useState(false);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState(null);
  const [success,     setSuccess]     = useState(null);
  const fileRef = useRef(null);

  function toggleFilter(key) {
    setFilters(f => ({ ...f, [key]: !f[key] }));
  }

  async function handleZip(file) {
    if (!file?.name.endsWith('.zip')) { setError('Only .zip files supported.'); return; }
    setError(null); setSuccess(null); setLoading(true);
    const form = new FormData();
    form.append('file', file);
    try {
      const res  = await fetch(`${API}/api/upload-zip`, { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Upload failed.');
      setSuccess(`✔ "${data.repo_name}" loaded — ${data.files_found} source files detected.`);
      onLoaded?.(data);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  async function handleGitHub() {
    if (!githubUrl.trim()) { setError('Enter a GitHub URL.'); return; }
    setError(null); setSuccess(null); setLoading(true);
    try {
      const res  = await fetch(`${API}/api/github-repo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: githubUrl.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load repo.');
      setSuccess(`✔ "${data.repo_name}" loaded — ${data.files_found} source files detected.`);
      onLoaded?.(data);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  return (
    <div className="page-container">

      {/* ── Header ────────────────────────────────────────── */}
      <div>
        <h1 className="page-title">Repository Input</h1>
        <p className="page-subtitle">
          Connect your codebase for agentic refactoring. R26-SE-008 analyses architectural
          patterns and applies deep-tech transformations.
        </p>
      </div>

      {/* ── Main grid ─────────────────────────────────────── */}
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
              <input ref={fileRef} type="file" accept=".zip" style={{ display:'none' }}
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

          {/* Alerts */}
          {error   && <div className="alert alert-error">⚠ {error}</div>}
          {success && <div className="alert alert-success">{success}</div>}

          {/* CTA Button */}
          <button
            className="btn btn-primary-full"
            onClick={handleGitHub}
            disabled={loading}
          >
            {loading
              ? <><div className="spinner" style={{ width:18, height:18, borderWidth:2 }} /> Analysing…</>
              : <>🚀 START SYSTEM ANALYSIS</>
            }
          </button>
        </div>

        {/* RIGHT PANEL */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

          {/* Threshold */}
          <div className="card card-body" style={{ padding: 20 }}>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom: 8 }}>
              <label className="field-label" style={{ margin: 0 }}>Analysis Threshold</label>
              <span className="badge badge-accent">{threshold}%</span>
            </div>
            <input
              type="range" min={0} max={100}
              value={threshold}
              onChange={e => setThreshold(+e.target.value)}
              style={{ width:'100%', marginBottom: 4 }}
            />
            <div style={{ display:'flex', justifyContent:'space-between' }}>
              <span style={{ fontSize: 10, color:'var(--text-muted)' }}>LENIENT</span>
              <span style={{ fontSize: 10, color:'var(--text-muted)' }}>STRICT</span>
            </div>
          </div>

          {/* Severity Filter */}
          <div className="card card-body" style={{ padding: 20 }}>
            <label className="field-label">Severity Filter</label>
            <div style={{ display:'flex', flexDirection:'column', gap: 8 }}>
              {[
                { key:'critical', label:'Critical Structural Issues',   sub:'Memory leaks, circular dependencies' },
                { key:'naming',   label:'Naming & Style Violations',    sub:'PEP8, Java Conventions, C Coding Practices' },
                { key:'optimise', label:'Optimisation Suggestions',     sub:'Non-critical performance improvements' },
              ].map(({ key, label, sub }) => (
                <div
                  key={key}
                  className={`check-row ${filters[key] ? 'checked' : ''}`}
                  onClick={() => toggleFilter(key)}
                >
                  <div className="check-box">{filters[key] ? '✓' : ''}</div>
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color:'var(--text-primary)' }}>{label}</div>
                    <div style={{ fontSize: 11, color:'var(--text-muted)', marginTop: 2 }}>{sub}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Info box */}
          <div style={{
            background:'var(--accent-muted)', border:'1px solid var(--border-accent)',
            borderRadius:'var(--r-sm)', padding: '12px 14px',
            display:'flex', gap: 10, alignItems:'flex-start',
          }}>
            <span style={{ fontSize: 16, flexShrink: 0, marginTop: 1 }}>ℹ</span>
            <p style={{ fontSize: 12, color:'var(--text-secondary)', lineHeight: 1.6 }}>
              Settings applied here will calibrate the{' '}
              <strong style={{ color:'var(--accent)' }}>CUQA Agent's</strong>{' '}
              reasoning engine during the pre-processing phase.
            </p>
          </div>
        </div>
      </div>

      {/* ── Recent Analysis ───────────────────────────────── */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">⏱ Recent Analysis</span>
          <button className="btn btn-outline btn-sm">Export All</button>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Repository Name</th>
                <th>Language</th>
                <th>Date</th>
                <th>Files</th>
                <th>Last Score</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {RECENT.map((r, i) => (
                <tr key={i}>
                  <td>
                    <div style={{ display:'flex', alignItems:'center', gap: 8 }}>
                      <span style={{ color:'var(--text-muted)' }}>📁</span>
                      <span style={{ color:'var(--text-primary)', fontWeight: 500 }}>{r.name}</span>
                    </div>
                  </td>
                  <td>
                    <span className={`badge badge-${r.lang.toLowerCase()}`}>{r.lang}</span>
                  </td>
                  <td>{r.date}</td>
                  <td>{r.files}</td>
                  <td>
                    <div style={{ display:'flex', alignItems:'center', gap: 10 }}>
                      <div className="progress-bar" style={{ width: 80, flex: 'none' }}>
                        <div className="progress-fill" style={{ width:`${r.score}%`, background: r.color }} />
                      </div>
                      <span style={{ fontSize: 12, fontWeight: 700, color: r.color }}>{r.score}%</span>
                    </div>
                  </td>
                  <td>
                    <button className="btn btn-ghost btn-sm">View →</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ padding: '14px 20px', textAlign:'center', borderTop:'1px solid var(--border)' }}>
          <a href="#" style={{ fontSize: 13, color:'var(--text-secondary)', textDecoration:'none' }}>
            View All Historical Records →
          </a>
        </div>
      </div>

    </div>
  );
}
