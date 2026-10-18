/**
 * QualityAssessmentPanel.jsx
 * --------------------------
 * CUQA function F4: the repository quality score, how many files are good,
 * fair or poor, and every file's score against the quality threshold chosen
 * on the Repository Input page. Lowest scores first, because those are the
 * files to refactor first.
 */

import { useState } from 'react';

const band = (score) => (score >= 80 ? 'good' : score >= 60 ? 'fair' : 'poor');
const BAND_COLOR = { good: 'var(--color-ok)', fair: 'var(--color-medium)', poor: 'var(--color-critical)' };

function Gauge({ value }) {
  const r = 58;
  const circ = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value ?? 0)) / 100;
  const color = value == null ? 'var(--text-muted)' : BAND_COLOR[band(value)];
  return (
    <svg className="cuqa-gauge" viewBox="0 0 150 150" role="img" aria-label={`Average quality ${value ?? 'unknown'} out of 100`}>
      <circle cx="75" cy="75" r={r} fill="none" stroke="var(--bg-hover)" strokeWidth="12" />
      <circle
        cx="75" cy="75" r={r} fill="none" stroke={color} strokeWidth="12" strokeLinecap="round"
        strokeDasharray={`${circ * pct} ${circ}`} transform="rotate(-90 75 75)"
      />
      <text x="75" y="72" textAnchor="middle" fill="var(--text-primary)" fontSize="34" fontWeight="800" fontFamily="var(--font-body)">
        {value == null ? '—' : Math.round(value)}
      </text>
      <text x="75" y="96" textAnchor="middle" fill="var(--text-muted)" fontSize="13" fontFamily="var(--font-body)">/ 100</text>
    </svg>
  );
}

export default function QualityAssessmentPanel({ report, threshold = 75, onSelectFile }) {
  const [showAll, setShowAll] = useState(false);
  const files = (report?.files || [])
    .filter((f) => typeof f.quality_score === 'number')
    .map((f) => ({
      name: f.file,
      path: f.relative_path || f.file,
      language: f.language,
      score: f.quality_score,
      smells: (f.code_smells || []).length,
      loc: f.metrics?.lines_of_code,
    }))
    .sort((a, b) => a.score - b.score);

  if (!report) {
    return <div className="card"><div className="empty-state" style={{ padding: 28 }}><p>Run the analysis to see quality scores.</p></div></div>;
  }

  const avg = report.summary?.average_quality_score ?? null;
  const counts = { good: 0, fair: 0, poor: 0 };
  files.forEach((f) => { counts[band(f.score)] += 1; });
  const below = files.filter((f) => f.score < threshold).length;
  const shown = showAll ? files : files.slice(0, 10);

  return (
    <div className="cuqa-quality">
      <div className="cuqa-gauge-card">
        <Gauge value={avg} />
        <div className="cuqa-gauge-label">Average quality of {files.length} file(s)</div>
        <div className="cuqa-bands">
          {['good', 'fair', 'poor'].map((b) => (
            <div key={b} className="cuqa-band" style={{ borderColor: `color-mix(in srgb, ${BAND_COLOR[b]} 40%, transparent)` }}>
              <b style={{ color: BAND_COLOR[b] }}>{counts[b]}</b>
              <small>{b === 'good' ? 'Good ≥80' : b === 'fair' ? 'Fair 60–79' : 'Poor <60'}</small>
            </div>
          ))}
        </div>
        <div className="cuqa-gauge-label">
          <strong style={{ color: below ? 'var(--color-critical)' : 'var(--color-ok)' }}>{below}</strong> file(s) below the {threshold}% threshold
        </div>
      </div>

      <div className="cuqa-scorelist">
        <div className="cuqa-scorelist-head">
          <b>File quality scores (lowest first)</b>
          <span>Line on each bar = {threshold}% threshold · click a file to see its AST</span>
        </div>
        {shown.map((f) => (
          <div
            key={f.path}
            className="cuqa-scorerow"
            role="button"
            tabIndex={0}
            onClick={() => onSelectFile?.(f)}
            onKeyDown={(e) => { if (e.key === 'Enter') onSelectFile?.(f); }}
            title={f.path}
          >
            <span className="name">
              {f.name}
              <small>{f.smells} smell(s){f.loc != null ? ` · ${f.loc} LOC` : ''}</small>
            </span>
            <span className="cuqa-scorebar">
              <i style={{ width: `${Math.max(2, f.score)}%`, background: BAND_COLOR[band(f.score)] }} />
              <span className="threshold" style={{ left: `${threshold}%` }} />
            </span>
            <span className="score" style={{ color: BAND_COLOR[band(f.score)] }}>{Math.round(f.score)}</span>
          </div>
        ))}
        {files.length > 10 && (
          <button className="btn btn-ghost btn-sm" style={{ margin: 8, alignSelf: 'center' }} onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Show lowest 10' : `Show all ${files.length} files`}
          </button>
        )}
      </div>
    </div>
  );
}
