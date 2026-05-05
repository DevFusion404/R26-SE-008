/**
 * QualityReportView.jsx
 * ---------------------
 * Displays the CUQA quality report JSON — code smell list,
 * severity breakdown, per-file metrics, and aggregate repo score.
 * This is the structured output the CUQA Agent passes to the RDP Agent.
 */

import { useState, useEffect } from 'react';

const API = 'http://localhost:8001';

// ── Severity badge ──────────────────────────────────────────────────────────
function SeverityBadge({ level }) {
  return <span className={`pill pill-${level}`}>{level.toUpperCase()}</span>;
}

// ── Score ring ──────────────────────────────────────────────────────────────
function ScoreRing({ score }) {
  const r = 36;
  const circ = 2 * Math.PI * r;
  const dash = (score / 100) * circ;
  const color = score >= 75 ? '#69db7c' : score >= 50 ? '#ffa94d' : '#ff6b6b';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
      <svg width={92} height={92} viewBox="0 0 92 92">
        <circle cx={46} cy={46} r={r} fill="none" stroke="var(--border)" strokeWidth={7} />
        <circle
          cx={46} cy={46} r={r} fill="none"
          stroke={color} strokeWidth={7}
          strokeDasharray={`${dash} ${circ}`}
          strokeLinecap="round"
          transform="rotate(-90 46 46)"
          style={{ transition: 'stroke-dasharray 0.6s ease' }}
        />
        <text x={46} y={46} textAnchor="middle" dominantBaseline="central"
          fill={color} fontSize={18} fontWeight={700} fontFamily="Inter,sans-serif">
          {score}
        </text>
      </svg>
      <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Quality Score</span>
    </div>
  );
}

// ── Smell list ──────────────────────────────────────────────────────────────
function SmellList({ smells }) {
  if (!smells?.length) {
    return (
      <div className="alert alert-success" style={{ marginTop: 8 }}>
        ✅ No code smells detected in this file.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
      {smells.map((s, i) => (
        <div key={i} style={{
          display: 'flex', alignItems: 'flex-start', gap: 10,
          background: 'var(--bg-elevated)', border: '1px solid var(--border)',
          borderRadius: 6, padding: '10px 14px',
        }}>
          <SeverityBadge level={s.severity || 'medium'} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>
              {s.type}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{s.message}</div>
          </div>
          {s.line && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)', flexShrink: 0 }}>
              L{s.line}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

// ── File report card ─────────────────────────────────────────────────────────
function FileReportCard({ report }) {
  const [open, setOpen] = useState(false);
  const m = report.metrics || {};
  const score = report.quality_score ?? '—';
  const smellCount = report.code_smells?.length ?? 0;

  return (
    <div className="card" style={{ marginBottom: 8 }}>
      <div
        className="card-header"
        style={{ cursor: 'pointer' }}
        onClick={() => setOpen(v => !v)}
      >
        <span className="card-title">
          <span>{report.language === 'python' ? '🐍' : '☕'}</span>
          <span style={{ fontFamily: 'var(--font-mono)' }}>{report.file}</span>
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{
            fontSize: 13, fontWeight: 700,
            color: score >= 75 ? '#69db7c' : score >= 50 ? '#ffa94d' : '#ff6b6b'
          }}>
            {score}/100
          </span>
          {report.smell_summary?.high > 0 && <SeverityBadge level="high" />}
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {smellCount} smell{smellCount !== 1 ? 's' : ''}
          </span>
          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>{open ? '▾' : '▸'}</span>
        </div>
      </div>

      {open && (
        <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Metrics */}
          <div className="metrics-grid">
            {[
              ['LOC', m.lines_of_code],
              ['Blank', m.blank_lines],
              ['Comments', m.comment_lines],
              ['Functions', m.functions],
              ['Classes', m.classes],
            ].map(([label, val]) => (
              <div className="metric-card" key={label}>
                <div className="metric-value" style={{ fontSize: 20 }}>{val ?? '—'}</div>
                <div className="metric-label">{label}</div>
              </div>
            ))}
          </div>

          {/* Smells */}
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
              Code Smells
            </div>
            <SmellList smells={report.code_smells} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

export default function QualityReportView({ repoLoaded, selectedFile }) {
  const [report, setReport]   = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);
  const [scope, setScope]     = useState('repo');   // 'repo' | 'file'
  const [rawJson, setRawJson] = useState(false);

  // When selectedFile changes, switch to file scope automatically
  useEffect(() => {
    if (selectedFile) {
      setScope('file');
      fetchReport('file', selectedFile.path);
    }
  }, [selectedFile]);

  async function fetchReport(type = scope, filePath = null) {
    if (!repoLoaded) return;
    setLoading(true);
    setError(null);
    setReport(null);

    const body = type === 'file' && filePath ? { file_path: filePath } : {};

    try {
      const res = await fetch(`${API}/api/quality-report`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to fetch report.');
      setReport(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function handleScopeChange(newScope) {
    setScope(newScope);
    if (newScope === 'repo') fetchReport('repo');
    else if (newScope === 'file' && selectedFile) fetchReport('file', selectedFile.path);
  }

  // ── States ────────────────────────────────────────────────────────────────

  if (!repoLoaded) {
    return (
      <div className="empty-state">
        <span className="empty-icon">📊</span>
        <p>Load a repository first to generate a quality report.</p>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* Controls */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <button
          className={`btn ${scope === 'repo' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => handleScopeChange('repo')}
        >
          🗂 Full Repo
        </button>
        <button
          className={`btn ${scope === 'file' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => handleScopeChange('file')}
          disabled={!selectedFile}
          title={!selectedFile ? 'Select a file in the Project Structure tab first' : ''}
        >
          📄 {selectedFile ? selectedFile.name : 'Selected File'}
        </button>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button
            className={`btn ${rawJson ? 'btn-primary' : 'btn-ghost'}`}
            style={{ padding: '4px 12px' }}
            onClick={() => setRawJson(v => !v)}
          >
            { rawJson ? '📊 Report View' : '{ } Raw JSON' }
          </button>
          <button
            className="btn btn-ghost"
            style={{ padding: '4px 12px' }}
            onClick={() => fetchReport(scope, selectedFile?.path)}
          >
            ↺ Refresh
          </button>
        </div>
      </div>

      {/* Loading */}
      {loading && (
        <div className="loading-state">
          <div className="spinner" />
          <span>Analysing code quality…</span>
        </div>
      )}

      {/* Error */}
      {error && <div className="alert alert-error">⚠ {error}</div>}

      {/* Report */}
      {report && !loading && (
        rawJson ? (
          <div className="card">
            <div className="card-header">
              <span className="card-title">{ } Raw JSON — CUQA Output (→ RDP Agent)</span>
            </div>
            <div className="card-body" style={{ maxHeight: '65vh', overflowY: 'auto' }}>
              <div className="json-viewer">{JSON.stringify(report.report, null, 2)}</div>
            </div>
          </div>
        ) : (
          <>
            {/* Repo-level summary */}
            {report.type === 'repository' && report.report?.summary && (
              <div className="card">
                <div className="card-header">
                  <span className="card-title">📊 Repository Summary — {report.report.repo_name}</span>
                </div>
                <div className="card-body">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 32, flexWrap: 'wrap' }}>
                    <ScoreRing score={report.report.summary.average_quality_score} />
                    <div className="metrics-grid" style={{ flex: 1 }}>
                      {[
                        ['Files Analysed', report.report.summary.files_analyzed],
                        ['Total LOC', report.report.summary.total_lines_of_code],
                        ['Total Smells', report.report.summary.total_code_smells],
                        ['High', report.report.summary.smell_severity?.high],
                        ['Medium', report.report.summary.smell_severity?.medium],
                        ['Low', report.report.summary.smell_severity?.low],
                      ].map(([label, val]) => (
                        <div className="metric-card" key={label}>
                          <div className="metric-value" style={{ fontSize: 22 }}>{val ?? '—'}</div>
                          <div className="metric-label">{label}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* File-level summary */}
            {report.type === 'file' && report.report && (
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 20 }}>
                <ScoreRing score={report.report.quality_score ?? 0} />
                <div style={{ flex: 1 }}>
                  <FileReportCard report={report.report} />
                </div>
              </div>
            )}

            {/* File list for repo report */}
            {report.type === 'repository' && report.report?.files?.length > 0 && (
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 10 }}>
                  Per-File Reports
                </div>
                {report.report.files.map((fr, i) => (
                  <FileReportCard key={i} report={fr} />
                ))}
              </div>
            )}
          </>
        )
      )}

      {/* No report yet (repo mode, not fetched) */}
      {!report && !loading && !error && (
        <div className="empty-state">
          <span className="empty-icon">📋</span>
          <p>Click <strong>Full Repo</strong> or select a file to generate the quality report.</p>
          <button className="btn btn-primary" onClick={() => fetchReport(scope, selectedFile?.path)}>
            ▶ Generate Report
          </button>
        </div>
      )}
    </div>
  );
}
