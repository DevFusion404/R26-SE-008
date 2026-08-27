/**
 * RepositoryAtAGlance.jsx
 * -----------------------
 * Stat cards component displaying top-level repository metrics:
 * - Source Files
 * - Languages
 * - Lines of Code (LOC)
 * - Likely Entry Points
 * - Total Directories
 * - Total Dependencies
 */

export default function RepositoryAtAGlance({ repository = {}, languageBreakdown = [], entryPoints = [], dependencySummary = {} }) {
  const {
    name = 'Repository',
    source_files = 0,
    directories = 0,
    lines_of_code = 0,
    primary_language = 'Unknown',
    detected_languages = [],
    is_polyglot = false,
  } = repository;

  const totalLocalDeps = dependencySummary.local_relationships || 0;
  const totalExtDeps = dependencySummary.external_dependencies || 0;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
      {/* Source Files Card */}
      <div className="metric-card">
        <div className="metric-label-text">📄 Source Files</div>
        <div className="metric-value" style={{ color: '#00d4e8' }}>
          {source_files.toLocaleString()}
        </div>
        <div className="metric-sub">{directories} directories</div>
      </div>

      {/* Languages Card */}
      <div className="metric-card">
        <div className="metric-label-text"> Languages</div>
        <div className="metric-value" style={{ color: '#3b82f6', fontSize: 20, wordBreak: 'break-word' }}>
          {detected_languages.length > 0 ? detected_languages.join(' · ') : primary_language || '—'}
        </div>
        <div className="metric-sub">
          {is_polyglot ? 'Polyglot Repository' : 'Single Language'}
        </div>
      </div>

      {/* Lines of Code Card */}
      <div className="metric-card">
        <div className="metric-label-text">≡ Lines of Code</div>
        <div className="metric-value" style={{ color: '#8b5cf6' }}>
          {lines_of_code > 0 ? lines_of_code.toLocaleString() : '—'}
        </div>
        <div className="metric-sub">Total Source LOC</div>
      </div>

      {/* Entry Points Card */}
      <div className="metric-card">
        <div className="metric-label-text">🚀 Entry Points</div>
        <div className="metric-value" style={{ color: entryPoints.length > 0 ? '#22c55e' : '#f59e0b' }}>
          {entryPoints.length}
        </div>
        <div className="metric-sub">
          {entryPoints.length > 0 ? `${entryPoints[0].language} (${entryPoints[0].confidence} confidence)` : 'None detected'}
        </div>
      </div>

      {/* Dependencies Summary Card */}
      <div className="metric-card">
        <div className="metric-label-text">📦 Dependencies</div>
        <div className="metric-value" style={{ color: '#a855f7' }}>
          {totalLocalDeps + totalExtDeps}
        </div>
        <div className="metric-sub">
          {totalLocalDeps} local · {totalExtDeps} external
        </div>
      </div>
    </div>
  );
}
