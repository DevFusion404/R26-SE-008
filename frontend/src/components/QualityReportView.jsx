/**
 * QualityReportView.jsx
 * ---------------------
 * Displays the CUQA quality report JSON — code smell list,
 * severity breakdown, per-file metrics, and aggregate repo score.
 * This is the structured output the CUQA Agent passes to the RDP Agent.
 *
 * Supports Python (.py), Java (.java), and C (.c, .h) source files.
 *
 * v2: Adds Code Smell Overview panel with category taxonomy,
 *     priority badges, animated bars, and enriched smell cards.
 */

import { useState, useEffect } from 'react';
import CUQAAgentService from '../services/cuqaAgentService';

// ── Category config (mirrors SMELL_CATEGORY_MAP priority) ──────────────────
const CATEGORY_CONFIG = {
  'Bloaters': {
    icon: '📈',
    priority: 'critical',
    description: 'Code that has grown too large to work with comfortably.',
    color: '#ef4444',
    glow: 'rgba(239,68,68,0.18)',
    border: 'rgba(239,68,68,0.30)',
  },
  'Object-Orientation Abusers': {
    icon: '🔀',
    priority: 'medium',
    description: 'Incorrect or incomplete application of OO principles.',
    color: '#eab308',
    glow: 'rgba(234,179,8,0.18)',
    border: 'rgba(234,179,8,0.30)',
  },
  'Change Preventers': {
    icon: '🔒',
    priority: 'critical',
    description: 'Make it hard to change one part without cascading updates.',
    color: '#f97316',
    glow: 'rgba(249,115,22,0.18)',
    border: 'rgba(249,115,22,0.30)',
  },
  'Dispensables': {
    icon: '🗑',
    priority: 'low',
    description: 'Unnecessary elements whose removal would clean the code.',
    color: '#3b82f6',
    glow: 'rgba(59,130,246,0.18)',
    border: 'rgba(59,130,246,0.30)',
  },
  'Couplers': {
    icon: '🔗',
    priority: 'medium',
    description: 'Excessive coupling between classes or modules.',
    color: '#a855f7',
    glow: 'rgba(168,85,247,0.18)',
    border: 'rgba(168,85,247,0.30)',
  },
  'Security / Language-Specific': {
    icon: '🛡',
    priority: 'critical',
    description: 'Language-level or security-sensitive patterns.',
    color: '#ef4444',
    glow: 'rgba(239,68,68,0.18)',
    border: 'rgba(239,68,68,0.30)',
  },
  'Uncategorized': {
    icon: '❓',
    priority: 'low',
    description: 'Unclassified smell type.',
    color: '#6b7280',
    glow: 'rgba(107,114,128,0.18)',
    border: 'rgba(107,114,128,0.30)',
  },
};

const PRIORITY_CONFIG = {
  critical: { label: 'CRITICAL', color: '#ef4444', bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.35)' },
  medium:   { label: 'MEDIUM',   color: '#eab308', bg: 'rgba(234,179,8,0.15)', border: 'rgba(234,179,8,0.35)' },
  low:      { label: 'LOW',      color: '#3b82f6', bg: 'rgba(59,130,246,0.15)', border: 'rgba(59,130,246,0.35)' },
};

const CATEGORY_ORDER = [
  'Bloaters',
  'Object-Orientation Abusers',
  'Change Preventers',
  'Dispensables',
  'Couplers',
  'Security / Language-Specific',
  'Uncategorized',
];

// ── Severity badge ──────────────────────────────────────────────────────────
function SeverityBadge({ level }) {
  return <span className={`pill pill-${level}`}>{level.toUpperCase()}</span>;
}

// ── Priority badge ──────────────────────────────────────────────────────────
function PriorityBadge({ priority }) {
  const cfg = PRIORITY_CONFIG[priority] || PRIORITY_CONFIG.low;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      padding: '2px 8px', borderRadius: 9999,
      fontSize: 10, fontWeight: 700, letterSpacing: '0.5px',
      background: cfg.bg, color: cfg.color,
      border: `1px solid ${cfg.border}`,
      whiteSpace: 'nowrap',
    }}>
      {priority === 'critical' && <span>●</span>}
      {cfg.label}
    </span>
  );
}

// ── Language icon helper ────────────────────────────────────────────────────
function langIcon(language, filename) {
  if (language === 'python') return '🐍';
  if (language === 'java')   return '☕';
  if (language === 'c') {
    const ext = filename?.split('.').pop()?.toLowerCase();
    return ext === 'h' ? '🔩' : '⚙️';
  }
  return '📄';
}

const LANG_LABEL = { python: 'Python', java: 'Java', c: 'C' };
const LANG_COLOR = { python: '#3b82f6', java: '#f59e0b', c: '#22c55e' };

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

// ── Category progress bar ───────────────────────────────────────────────────
function CategoryBar({ count, maxCount, color }) {
  const pct = maxCount > 0 ? (count / maxCount) * 100 : 0;
  return (
    <div style={{
      height: 6, borderRadius: 3,
      background: 'var(--bg-hover)', overflow: 'hidden', flex: 1,
    }}>
      <div style={{
        height: '100%', borderRadius: 3,
        width: `${pct}%`, background: color,
        transition: 'width 0.6s cubic-bezier(0.4,0,0.2,1)',
        boxShadow: `0 0 6px ${color}80`,
      }} />
    </div>
  );
}

// ── Code Smell Overview panel ───────────────────────────────────────────────
function CodeSmellOverview({ overview }) {
  const [expanded, setExpanded] = useState(null);

  if (!overview) return null;

  const maxCount = Math.max(
    ...CATEGORY_ORDER.map(cat => overview[cat]?.count ?? 0),
    1,
  );
  const totalSmells = CATEGORY_ORDER.reduce(
    (acc, cat) => acc + (overview[cat]?.count ?? 0), 0,
  );

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border)',
      borderRadius: 12,
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '16px 20px',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        background: 'linear-gradient(135deg, #0f1e30 0%, #0a1520 100%)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 16 }}>🗂</span>
          <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)' }}>
            Code Smell Overview
          </span>
          <span style={{
            fontSize: 10, padding: '2px 8px', borderRadius: 9999,
            background: 'var(--accent-muted)', color: 'var(--accent)',
            border: '1px solid var(--border-accent)', fontWeight: 600,
          }}>
            {totalSmells} total
          </span>
        </div>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          Click a category to expand
        </span>
      </div>

      {/* Category rows */}
      <div style={{ padding: '8px 0' }}>
        {CATEGORY_ORDER.map(cat => {
          const data = overview[cat];
          if (!data) return null;
          const cfg = CATEGORY_CONFIG[cat] || CATEGORY_CONFIG['Uncategorized'];
          const isOpen = expanded === cat;
          const hasSmells = data.count > 0;

          return (
            <div key={cat} style={{
              borderBottom: '1px solid var(--border)',
              transition: 'background 0.15s ease',
            }}>
              {/* Row */}
              <div
                onClick={() => setExpanded(isOpen ? null : cat)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '12px 20px', cursor: 'pointer',
                  background: isOpen ? cfg.glow : 'transparent',
                  transition: 'background 0.15s ease',
                  userSelect: 'none',
                }}
                onMouseEnter={e => {
                  if (!isOpen) e.currentTarget.style.background = `${cfg.glow}`;
                }}
                onMouseLeave={e => {
                  if (!isOpen) e.currentTarget.style.background = 'transparent';
                }}
              >
                {/* Icon */}
                <span style={{ fontSize: 18, flexShrink: 0 }}>{cfg.icon}</span>

                {/* Category name + description */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4,
                  }}>
                    <span style={{
                      fontSize: 12, fontWeight: 600,
                      color: hasSmells ? 'var(--text-primary)' : 'var(--text-muted)',
                    }}>
                      {cat}
                    </span>
                    <PriorityBadge priority={cfg.priority} />
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <CategoryBar
                      count={data.count}
                      maxCount={maxCount}
                      color={hasSmells ? cfg.color : 'var(--border-light)'}
                    />
                  </div>
                </div>

                {/* Count badge */}
                <div style={{
                  flexShrink: 0, minWidth: 36, textAlign: 'center',
                  fontSize: 16, fontWeight: 700,
                  color: hasSmells ? cfg.color : 'var(--text-muted)',
                  textShadow: hasSmells ? `0 0 12px ${cfg.color}60` : 'none',
                }}>
                  {data.count}
                </div>

                {/* Expand chevron */}
                <span style={{
                  flexShrink: 0, fontSize: 12,
                  color: 'var(--text-muted)',
                  transform: isOpen ? 'rotate(90deg)' : 'rotate(0deg)',
                  transition: 'transform 0.2s ease',
                }}>
                  ▸
                </span>
              </div>

              {/* Expanded detail */}
              {isOpen && (
                <div style={{
                  padding: '12px 20px 16px 52px',
                  borderTop: `1px solid ${cfg.border}`,
                  background: `linear-gradient(180deg, ${cfg.glow} 0%, transparent 100%)`,
                  animation: 'fadeIn 0.15s ease',
                }}>
                  <div style={{
                    fontSize: 11, color: 'var(--text-secondary)',
                    marginBottom: 10, fontStyle: 'italic',
                  }}>
                    {cfg.description}
                  </div>
                  {data.smells?.length > 0 ? (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {data.smells.map(smellType => (
                        <span key={smellType} style={{
                          padding: '3px 10px', borderRadius: 6,
                          fontSize: 11, fontWeight: 600, fontFamily: 'var(--font-mono)',
                          background: `${cfg.color}18`,
                          color: cfg.color,
                          border: `1px solid ${cfg.border}`,
                        }}>
                          {smellType}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span style={{
                      fontSize: 11, color: '#22c55e',
                      display: 'flex', alignItems: 'center', gap: 5,
                    }}>
                      ✓ No smells detected in this category
                    </span>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Enriched smell card ─────────────────────────────────────────────────────
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
      {smells.map((s, i) => {
        const catCfg = CATEGORY_CONFIG[s.category] || CATEGORY_CONFIG['Uncategorized'];
        return (
          <div key={i} style={{
            display: 'flex', alignItems: 'flex-start', gap: 10,
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderLeft: `3px solid ${catCfg.color}`,
            borderRadius: 6, padding: '10px 14px',
            transition: 'border-color 0.15s ease',
          }}>
            <SeverityBadge level={s.severity || 'medium'} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 6,
                flexWrap: 'wrap', marginBottom: 3,
              }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>
                  {s.type}
                </span>
                {s.category && (
                  <span style={{
                    fontSize: 10, padding: '1px 6px', borderRadius: 4,
                    background: `${catCfg.color}18`, color: catCfg.color,
                    border: `1px solid ${catCfg.border}`,
                    fontWeight: 600,
                  }}>
                    {catCfg.icon} {s.category}
                  </span>
                )}
                {s.category_priority && (
                  <PriorityBadge priority={s.category_priority} />
                )}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{s.message}</div>
            </div>
            {s.line && (
              <span style={{
                fontFamily: 'var(--font-mono)', fontSize: 11,
                color: 'var(--text-muted)', flexShrink: 0,
              }}>
                L{s.line}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── File report card ─────────────────────────────────────────────────────────
function FileReportCard({ report }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState('smells'); // 'smells' | 'overview'
  const m = report.metrics || {};
  const score = report.quality_score ?? '—';
  const smellCount = report.code_smells?.length ?? 0;
  const hasOverview = !!report.code_smell_overview;

  return (
    <div className="card" style={{ marginBottom: 8 }}>
      <div
        className="card-header"
        style={{ cursor: 'pointer' }}
        onClick={() => setOpen(v => !v)}
      >
        <span className="card-title">
          <span>{langIcon(report.language, report.file)}</span>
          <span style={{ fontFamily: 'var(--font-mono)' }}>{report.file}</span>
          {report.language && (
            <span style={{
              fontSize: 10, fontWeight: 600, padding: '2px 7px',
              borderRadius: 20, marginLeft: 6,
              background: (LANG_COLOR[report.language] || '#374151') + '28',
              color: LANG_COLOR[report.language] || '#9ca3af',
              border: `1px solid ${(LANG_COLOR[report.language] || '#374151')}55`,
              textTransform: 'uppercase', letterSpacing: '0.5px',
            }}>
              {LANG_LABEL[report.language] || report.language}
            </span>
          )}
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
          {/* Core metrics */}
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

          {/* C-specific metrics */}
          {report.language === 'c' && (
            <div>
              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-muted)', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
                C-Specific Metrics
              </div>
              <div className="metrics-grid">
                {[
                  ['Includes', m.include_count],
                  ['Globals', m.global_variables],
                  ['Cyclomatic', m.estimated_cyclomatic_complexity],
                ].map(([label, val]) => (
                  <div className="metric-card" key={label} style={{ borderColor: '#22c55e33' }}>
                    <div className="metric-value" style={{ fontSize: 20, color: '#22c55e' }}>{val ?? '—'}</div>
                    <div className="metric-label">{label}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Tab switcher */}
          {hasOverview && (
            <div style={{ display: 'flex', gap: 4, borderBottom: '1px solid var(--border)', paddingBottom: 0 }}>
              {[
                { key: 'smells', label: '🔍 Smell List' },
                { key: 'overview', label: '🗂 Category Overview' },
              ].map(t => (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  style={{
                    padding: '5px 12px', border: 'none', cursor: 'pointer',
                    background: 'transparent', fontSize: 11, fontWeight: 600,
                    color: tab === t.key ? 'var(--accent)' : 'var(--text-muted)',
                    borderBottom: tab === t.key ? '2px solid var(--accent)' : '2px solid transparent',
                    transition: 'all 0.15s ease',
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}

          {/* Smells */}
          {tab === 'smells' && (
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Code Smells
              </div>
              <SmellList smells={report.code_smells} />
            </div>
          )}

          {/* Per-file overview */}
          {tab === 'overview' && hasOverview && (
            <CodeSmellOverview overview={report.code_smell_overview} />
          )}
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
      const data = await CUQAAgentService.getQualityReport(type === 'file' ? filePath : null);
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
                <div className="card-body" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
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

                  {/* Repo-level category overview */}
                  {report.report.summary.code_smell_overview && (
                    <div>
                      <div style={{
                        fontSize: 11, fontWeight: 600, textTransform: 'uppercase',
                        letterSpacing: '0.5px', color: 'var(--text-muted)', marginBottom: 10,
                      }}>
                        Category Breakdown
                      </div>
                      <CodeSmellOverview overview={report.report.summary.code_smell_overview} />
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* File-level summary */}
            {report.type === 'file' && report.report && (
              <>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 20 }}>
                  <ScoreRing score={report.report.quality_score ?? 0} />
                  <div style={{ flex: 1 }}>
                    <FileReportCard report={report.report} />
                  </div>
                </div>

                {/* Standalone overview card for single-file view */}
                {report.report.code_smell_overview && (
                  <div className="card">
                    <div className="card-header">
                      <span className="card-title">🗂 Code Smell Categories</span>
                    </div>
                    <div className="card-body">
                      <CodeSmellOverview overview={report.report.code_smell_overview} />
                    </div>
                  </div>
                )}
              </>
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
