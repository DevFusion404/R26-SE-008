/**
 * CuqaIdentityHeader.jsx
 * ----------------------
 * The banner at the top of the CUQA page. It says which agent this is, where
 * it sits in the pipeline, what it takes in and what it gives out, so a user
 * can tell the CUQA agent apart from the other three at a glance.
 */

import DeveloperReviewButton from './DeveloperReviewButton';

const PIPELINE = [
  { step: 1, id: 'cuqa',        name: 'CUQA',  role: 'Understand & assess quality', color: 'var(--color-cuqa)' },
  { step: 2, id: 'rdp',         name: 'RDP',   role: 'Plan the refactorings',       color: 'var(--color-rdp)' },
  { step: 3, id: 'transform',   name: 'SCTVA', role: 'Transform & validate code',   color: 'var(--color-transform)' },
  { step: 4, id: 'orchestrate', name: 'DIWO',  role: 'Developer review & approval', color: 'var(--color-orchestrate)' },
];

const LANG_ICON = { Python: '🐍', Java: '☕', C: '⚙️' };

const MODE_LABEL = { rules: 'Rules', hybrid: 'Rules + ML', ml: 'ML model only', off: 'Rules (ML off)' };

export default function CuqaIdentityHeader({
  repoMeta,
  report,
  loading,
  runLabel,
  threshold,
  detectorMode,
  onRun,
  onExport,
  onSendToRdp,
  onOpenDiwo,
}) {
  const summary = report?.summary || {};
  const languages = repoMeta?.detected_languages || [];
  const breakdown = repoMeta?.language_breakdown || {};

  return (
    <header className="cuqa-hero" aria-label="CUQA agent">
      <div className="cuqa-hero-top">
        <div className="cuqa-mark" aria-hidden="true">
          <b>CUQA</b>
          <small>AGENT 1</small>
        </div>

        <div className="cuqa-hero-text">
          <div className="cuqa-eyebrow">
            <span className="step-pill">Agent 1 of 4</span>
            <span>Code Understanding &amp; Quality Assessment</span>
          </div>
          <h1 className="cuqa-hero-title">CUQA <span>Agent</span></h1>
          <p className="cuqa-hero-sub">
            The first agent in RefactorIQ. It reads a legacy repository, explains how it is built,
            parses every file into an AST, finds code smells with rules and a trained ML model,
            scores the quality of each file and hands one structured report to the next agents.
          </p>
        </div>

        {onOpenDiwo !== undefined || repoMeta ? (
          <div className="cuqa-hero-actions has-review">
            {/* The main next step, shown greyed out until the report is ready */}
            <DeveloperReviewButton id="cuqa-diwo-nav-btn" onClick={onOpenDiwo} disabled={!report || !onOpenDiwo} />
            <div className="row">
              <button className="cuqa-btn outline sm" onClick={onRun} disabled={loading}>
                {loading ? 'Analysing…' : (runLabel || '⟳ Re-run')}
              </button>
              <button className="cuqa-btn outline sm" onClick={onExport} disabled={!report}>↓ Export</button>
              {onSendToRdp && (
                <button className="cuqa-btn ghost-rdp sm" onClick={onSendToRdp} disabled={!report}
                  title="Skip the developer review and send the report straight to the RDP planning page">
                  Send to RDP only
                </button>
              )}
            </div>
          </div>
        ) : (
          <div className="cuqa-hero-actions">
            <button className="cuqa-btn primary" onClick={onRun} disabled={loading}>
              {loading ? 'Analysing…' : (runLabel || '⟳ Run analysis')}
            </button>
          </div>
        )}
      </div>

      {/* Where CUQA sits in the pipeline */}
      <nav className="cuqa-pipeline" aria-label="Pipeline position">
        {PIPELINE.map((agent, i) => (
          <div key={agent.id} style={{ display: 'contents' }}>
            <div
              className={`cuqa-pipe-step ${agent.step === 1 ? 'current' : ''}`}
              style={{ '--c': agent.color }}
              aria-current={agent.step === 1 ? 'step' : undefined}
            >
              <span className="n">{agent.step}</span>
              <span className="t">
                <b>{agent.name}</b>
                <small>{agent.role}</small>
              </span>
              {agent.step === 1 && <span className="here">You are here</span>}
            </div>
            {i < PIPELINE.length - 1 && <span className="cuqa-pipe-arrow" aria-hidden="true">→</span>}
          </div>
        ))}
      </nav>

      {/* What goes in and what comes out */}
      <div className="cuqa-io">
        <div className="cuqa-io-box">
          <div className="k">Input</div>
          <div className="v">A source repository as a ZIP upload or a public GitHub link — Java, Python and C files.</div>
        </div>
        <div className="cuqa-io-arrow" aria-hidden="true"><span>CUQA</span><i>➜</i></div>
        <div className="cuqa-io-box out">
          <div className="k">Output</div>
          <div className="v">A structured quality report (JSON): smells with type, category, severity, file and line, plus a quality score per file. Sent to RDP and DIWO.</div>
        </div>
      </div>

      {/* What is loaded right now */}
      <div className="cuqa-status">
        {repoMeta?.repo_name && (
          <span className="cuqa-chip accent" title="Repository being analysed">📦 <b>{repoMeta.repo_name}</b></span>
        )}
        {languages.map((lang) => (
          <span key={lang} className="cuqa-chip">
            {LANG_ICON[lang] || '📄'} <b>{lang}</b> {breakdown[lang] ?? 0} files
          </span>
        ))}
        {report && (
          <>
            <span className="cuqa-chip"><b>{summary.files_analyzed ?? report.files?.length ?? 0}</b> files analysed</span>
            <span className="cuqa-chip"><b>{(summary.total_lines_of_code ?? 0).toLocaleString()}</b> lines</span>
            <span className="cuqa-chip"><b>{summary.total_code_smells ?? 0}</b> smells</span>
            {typeof summary.average_quality_score === 'number' && (
              <span className="cuqa-chip"><b>{summary.average_quality_score.toFixed(1)}</b>/100 avg. quality</span>
            )}
            {threshold != null && <span className="cuqa-chip">Threshold <b>{threshold}%</b></span>}
            {detectorMode && <span className="cuqa-chip">Detector <b>{MODE_LABEL[detectorMode] || detectorMode}</b></span>}
          </>
        )}
      </div>
    </header>
  );
}
