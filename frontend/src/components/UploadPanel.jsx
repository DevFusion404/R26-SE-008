/**
 * UploadPanel.jsx
 * ---------------
 * First-step UI for the CUQA Agent.
 * Accepts a ZIP file (drag-and-drop or click) OR a public GitHub URL.
 * Posts to the CUQA backend and triggers the analysis pipeline.
 */

import { useState, useRef } from 'react';

const API = 'http://localhost:8001';

export default function UploadPanel({ onLoaded }) {
  const [mode, setMode] = useState('zip');   // 'zip' | 'github'
  const [githubUrl, setGithubUrl] = useState('');
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(null);
  const fileRef = useRef(null);

  // ── ZIP upload ──────────────────────────────────────────────────────────
  async function handleZipUpload(file) {
    if (!file) return;
    if (!file.name.endsWith('.zip')) {
      setError('Please upload a .zip file.');
      return;
    }
    setError(null);
    setSuccess(null);
    setLoading(true);

    const form = new FormData();
    form.append('file', file);

    try {
      const res = await fetch(`${API}/api/upload-zip`, { method: 'POST', body: form });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Upload failed.');
      setSuccess(`✔ Loaded "${data.repo_name}" — ${data.files_found} source files found.`);
      onLoaded?.(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // ── GitHub clone ────────────────────────────────────────────────────────
  async function handleGitHubLoad() {
    if (!githubUrl.trim()) {
      setError('Please enter a GitHub URL.');
      return;
    }
    setError(null);
    setSuccess(null);
    setLoading(true);

    try {
      const res = await fetch(`${API}/api/github-repo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: githubUrl.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load repository.');
      setSuccess(`✔ Loaded "${data.repo_name}" from GitHub — ${data.files_found} source files found.`);
      onLoaded?.(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  // ── Drag events ─────────────────────────────────────────────────────────
  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    handleZipUpload(file);
  }

  return (
    <div className="card">
      <div className="card-header">
        <span className="card-title">📥 Load Repository</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className={`btn ${mode === 'zip' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setMode('zip')}
          >
            📦 Upload ZIP
          </button>
          <button
            className={`btn ${mode === 'github' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setMode('github')}
          >
            🐙 GitHub URL
          </button>
        </div>
      </div>

      <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {/* ── ZIP mode ── */}
        {mode === 'zip' && (
          <div
            className={`upload-zone ${dragging ? 'dragging' : ''}`}
            onDragOver={e => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
          >
            <span className="upload-icon">📂</span>
            <p style={{ color: 'var(--text-primary)', fontWeight: 600, marginBottom: 4 }}>
              Drop your project ZIP here
            </p>
            <p style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
              or click to browse — supports Python (.py) and Java (.java) files
            </p>
            <input
              ref={fileRef}
              type="file"
              accept=".zip"
              style={{ display: 'none' }}
              onChange={e => handleZipUpload(e.target.files?.[0])}
            />
          </div>
        )}

        {/* ── GitHub mode ── */}
        {mode === 'github' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Public GitHub Repository URL
            </label>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                id="github-url-input"
                className="input"
                placeholder="https://github.com/owner/repository"
                value={githubUrl}
                onChange={e => setGithubUrl(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleGitHubLoad()}
              />
              <button
                className="btn btn-primary"
                onClick={handleGitHubLoad}
                disabled={loading}
                style={{ flexShrink: 0 }}
              >
                {loading ? 'Loading…' : '→ Analyse'}
              </button>
            </div>
            <p style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              Only public repositories are supported. The repo is downloaded as a ZIP — no git required.
            </p>
          </div>
        )}

        {/* ── Loading ── */}
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-secondary)' }}>
            <div className="spinner" style={{ width: 20, height: 20, borderWidth: 2 }} />
            <span style={{ fontSize: 13 }}>Processing repository…</span>
          </div>
        )}

        {/* ── Error / Success ── */}
        {error   && <div className="alert alert-error">⚠ {error}</div>}
        {success && <div className="alert alert-success">{success}</div>}
      </div>
    </div>
  );
}
