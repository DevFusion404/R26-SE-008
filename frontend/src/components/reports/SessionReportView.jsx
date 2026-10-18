/**
 * SessionReportView.jsx
 * ---------------------
 * The report of one finished refactoring session, shown on the Reports page
 * after the developer presses "Complete Workflow" in DIWO. One section per
 * pipeline step, each labelled with the agent that produced it, so the whole
 * run — what was found, planned, changed and kept — reads top to bottom.
 *
 * Built from the snapshot in utils/sessionReports.js. A step with no data
 * says "not recorded" instead of showing a zero.
 */

import { DIWO_BASE } from '../../pages/diwo/services/diwoApi';
import { buildSummaryReportHtml } from '../../pages/diwo/utils/summaryReport';
import { narrateAll } from '../../pages/diwo/utils/auditNarrative';
import './sessionReport.css';

const AGENT = {
  cuqa:  { name: 'CUQA',  color: 'var(--color-cuqa)' },
  rdp:   { name: 'RDP',   color: 'var(--color-rdp)' },
  sctva: { name: 'SCTVA', color: 'var(--color-transform)' },
  diwo:  { name: 'DIWO',  color: 'var(--color-orchestrate)' },
};

const SEVERITY_COLOR = { high: 'var(--color-critical)', critical: 'var(--color-critical)', medium: 'var(--color-medium)', low: 'var(--color-low)' };
const CATEGORY_PRIORITY_COLOR = { critical: 'var(--color-critical)', medium: 'var(--color-medium)', low: 'var(--color-low)' };

const fmtDate = (iso) => {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString();
};
const pct = (v) => (typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—');
const baseName = (p) => (p || '').split(/[\\/]/).pop() || p;

function scoreTag(score) {
  if (typeof score !== 'number') return <span className="srep-tag">—</span>;
  const cls = score >= 80 ? 'good' : score >= 60 ? 'mid' : 'bad';
  return <span className={`srep-tag ${cls}`}>{Math.round(score)}</span>;
}

function adviceTag(rec) {
  if (!rec) return <span className="srep-tag">—</span>;
  const cls = rec === 'recommended' ? 'good' : rec === 'review' ? 'mid' : rec === 'not_recommended' ? 'bad' : 'info';
  return <span className={`srep-tag ${cls}`}>{rec.replace(/_/g, ' ')}</span>;
}

function decisionTag(decision) {
  if (!decision) return <span className="srep-tag">pending</span>;
  const cls = decision === 'approve' || decision === 'accept' ? 'good' : decision === 'reject' ? 'bad' : 'info';
  const label = { approve: 'approved', accept: 'accepted', reject: 'rejected', manual: 'manual work' }[decision] || decision;
  return <span className={`srep-tag ${cls}`}>{label}</span>;
}

function Kpi({ k, v }) {
  return <div className="srep-kpi"><div className="k">{k}</div><div className="v">{v ?? '—'}</div></div>;
}

function Section({ no, title, agent, open = true, children }) {
  const a = AGENT[agent];
  return (
    <details className="srep-section" open={open} style={a ? { '--c': a.color } : undefined}>
      <summary>
        <span className="srep-no">{no}</span>
        <span className="srep-section-title">{title}</span>
        {a && <span className="srep-by">by <b>{a.name}</b> agent</span>}
        <span className="chev">▸</span>
      </summary>
      <div className="srep-body">{children}</div>
    </details>
  );
}

function Missing({ children }) {
  return <div className="srep-missing">Not recorded in this session — {children}</div>;
}

function Bars({ items, max, colorOf }) {
  const top = Math.max(max ?? 0, ...items.map((i) => i.value), 1);
  return (
    <div className="srep-bars">
      {items.map((item) => (
        <div className="srep-bar" key={item.name}>
          <span className="name" title={item.name}>{item.name}</span>
          <span className="track"><i style={{ width: `${(item.value / top) * 100}%`, background: colorOf(item) }} /></span>
          <span className="n">{item.value}</span>
        </div>
      ))}
    </div>
  );
}

// ── Main ───────────────────────────────────────────────────────────────────

export default function SessionReportView({ report, onDelete }) {
  if (!report) return null;

  const repoName = report.repository?.name || report.analysis?.repo_name || report.target || 'Repository';
  const analysis = report.analysis;
  const selection = report.selection;
  const plan = report.plan;
  const decisions = report.plan_decisions;
  const tx = report.transformation;
  const outcome = report.outcome || {};
  const metrics = report.metrics;
  const rows = report.audit_rows || [];
  const trail = narrateAll(rows);

  const changedFiles = tx?.files?.filter((f) => f.changed).length ?? null;
  const archiveUrl = outcome.archive && report.workflow_id
    ? `${DIWO_BASE}/workflows/${encodeURIComponent(report.workflow_id)}/refactored-archive`
    : null;

  function downloadJson() {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `refactoring_session_${report.workflow_id || report.id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function printReport() {
    const mb = metrics?.before || {};
    const ma = metrics?.after || {};
    const sev = Array.from(new Set([
      ...Object.keys(mb.smell_breakdown || {}), ...Object.keys(ma.smell_breakdown || {}),
    ])).map((s) => ({
      severity: s,
      before: Number((mb.smell_breakdown || {})[s] || 0),
      after: Number((ma.smell_breakdown || {})[s] || 0),
    }));
    const html = buildSummaryReportHtml({
      workflowId: report.workflow_id || report.id,
      target: report.target || repoName,
      language: report.language,
      rows,
      metricsBefore: mb,
      metricsAfter: ma,
      severityBreakdown: sev,
      acceptedFiles: outcome.accepted_files || [],
      rejectedFiles: outcome.reverted_files || [],
      notes: report.notes,
      sctva: tx?.request_id ? { request_id: tx.request_id } : null,
      archive: null,
    });
    const w = window.open('', '_blank');
    if (!w) {
      alert('The report window was blocked. Allow pop-ups for this site and try again.');
      return;
    }
    w.document.write(html);
    w.document.close();
  }

  // One tile per step of the run
  const journey = [
    {
      agent: 'cuqa', what: 'Smells detected',
      num: analysis ? analysis.total_code_smells : null,
      sub: analysis ? `in ${analysis.files_analyzed} file(s) analysed` : 'no CUQA report',
    },
    {
      agent: 'diwo', what: 'Smells selected',
      num: selection ? selection.selected_count : null,
      sub: selection ? `of ${selection.total_smells} · ${selection.excluded_count ?? 0} excluded` : 'no selection',
    },
    {
      agent: plan && plan.source !== 'rdp_agent' ? 'diwo' : 'rdp', what: 'Plan steps',
      num: plan ? plan.total_steps : null,
      sub: plan ? (plan.source === 'rdp_agent' ? 'generated by the RDP agent' : 'DIWO fallback plan') : 'no plan',
    },
    {
      agent: 'diwo', what: 'Steps approved',
      num: decisions ? decisions.approved_count : null,
      sub: decisions ? `${decisions.rejected_count} rejected · ${decisions.manual_count} manual` : 'no decision',
    },
    {
      agent: 'sctva', what: 'Files changed',
      num: tx ? (changedFiles ?? tx.files.length) : null,
      sub: tx ? (tx.by === 'sctva' ? `confidence ${pct(tx.confidence_score)}` : 'simulated result (SCTVA not used)') : 'no transformation',
    },
    {
      agent: 'diwo', what: 'Files kept',
      num: outcome.accepted_files?.length ?? null,
      sub: `${outcome.reverted_files?.length ?? 0} reverted to original`,
    },
  ];

  const SEV_ORDER = ['critical', 'high', 'medium', 'low'];
  const sevItems = analysis
    ? Object.entries(analysis.severity || {})
        .map(([name, value]) => ({ name, value }))
        .sort((a, b) => SEV_ORDER.indexOf(a.name) - SEV_ORDER.indexOf(b.name))
    : [];
  const catItems = analysis
    ? analysis.categories.filter((c) => c.count > 0).sort((a, b) => b.count - a.count).map((c) => ({ name: c.name, value: c.count, priority: c.priority }))
    : [];
  const typeItems = analysis
    ? Object.entries(analysis.smell_types || {}).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([name, value]) => ({ name, value }))
    : [];

  return (
    <div className="srep">
      {/* ── Header ─────────────────────────────────────────── */}
      <div className="srep-head">
        <div className="srep-head-text">
          <div className="srep-kicker">Refactoring session report · whole repository</div>
          <div className="srep-title">📦 {repoName}</div>
          <div className="srep-meta">
            {report.repository?.source && (
              <span className="srep-chip">{report.repository.source === 'github' ? '🔗 GitHub' : '📁 ZIP upload'}</span>
            )}
            {report.language && <span className="srep-chip">Language <b>{report.language}</b></span>}
            {report.workflow_id && <span className="srep-chip">Workflow <b>{report.workflow_id}</b></span>}
            <span className="srep-chip">Completed <b>{fmtDate(report.completed_at)}</b></span>
            {report.completed_on_backend
              ? <span className="srep-chip ok">✓ Closed on the DIWO backend</span>
              : <span className="srep-chip warn" title={report.complete_error || ''}>⚠ Not closed on the backend{report.complete_error ? ` — ${report.complete_error}` : ''}</span>}
            {report.trimmed && <span className="srep-chip warn">Stored in short form (browser storage was full)</span>}
          </div>
        </div>
        <div className="srep-actions">
          <button className="btn btn-primary" onClick={printReport} disabled={rows.length === 0} title={rows.length === 0 ? 'No audit trail was recorded for this session' : 'Open a printable version (save as PDF from the print dialog)'}>
            🖨 Print / PDF
          </button>
          <button className="btn btn-outline" onClick={downloadJson}>↓ JSON</button>
          {archiveUrl && (
            <a className="btn btn-outline" href={archiveUrl} download>↓ Refactored ZIP</a>
          )}
          {onDelete && (
            <button className="btn btn-ghost" onClick={() => onDelete(report.id)} title="Remove this report from this browser">🗑 Remove</button>
          )}
        </div>
      </div>

      {/* ── Journey ───────────────────────────────────────── */}
      <div className="srep-journey">
        {journey.map((step, i) => (
          <div key={i} className={`srep-step ${step.num == null ? 'missing' : ''}`} style={{ '--c': AGENT[step.agent].color }}>
            <span className="who">{i + 1}. {AGENT[step.agent].name}</span>
            <span className="what">{step.what}</span>
            <span className="num">{step.num ?? '—'}</span>
            <span className="sub">{step.sub}</span>
          </div>
        ))}
      </div>

      {/* ── 1. Repository analysis ────────────────────────── */}
      <Section no="1" title="Repository analysis" agent="cuqa">
        {!analysis ? <Missing>the CUQA quality report did not reach the DIWO session.</Missing> : (
          <>
            <div className="srep-kpis">
              <Kpi k="Files analysed" v={analysis.files_analyzed} />
              <Kpi k="Lines of code" v={analysis.total_lines_of_code?.toLocaleString()} />
              <Kpi k="Code smells" v={analysis.total_code_smells} />
              <Kpi k="Avg. quality" v={typeof analysis.average_quality_score === 'number' ? `${analysis.average_quality_score.toFixed(1)}/100` : '—'} />
              <Kpi k="Files with smells" v={analysis.files.filter((f) => f.smells > 0).length} />
            </div>
            <div className="rg-split" style={{ gap: 16 }}>
              <div>
                <div className="srep-note" style={{ marginBottom: 6 }}><b>By severity</b></div>
                <Bars items={sevItems} colorOf={(i) => SEVERITY_COLOR[i.name] || 'var(--accent)'} />
                {catItems.length > 0 && (
                  <>
                    <div className="srep-note" style={{ margin: '12px 0 6px' }}><b>By category</b></div>
                    <Bars items={catItems} colorOf={(i) => CATEGORY_PRIORITY_COLOR[i.priority] || 'var(--accent)'} />
                  </>
                )}
              </div>
              <div>
                <div className="srep-note" style={{ marginBottom: 6 }}><b>Most frequent smell types</b></div>
                <Bars items={typeItems} colorOf={() => 'var(--color-cuqa)'} />
              </div>
            </div>
            <div className="srep-table-wrap">
              <table className="srep-table">
                <thead><tr><th>File</th><th>Lang</th><th>Quality</th><th>Smells</th><th>High</th><th>Smell types</th></tr></thead>
                <tbody>
                  {analysis.files.slice(0, 15).map((f) => (
                    <tr key={f.path}>
                      <td><code title={f.path}>{f.path}</code></td>
                      <td>{f.language || '—'}</td>
                      <td>{scoreTag(f.quality_score)}</td>
                      <td>{f.smells}</td>
                      <td>{f.high}</td>
                      <td>{(f.types || []).join(', ') || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {analysis.files.length > 15 && (
              <div className="srep-note">Showing the 15 files with the most smells out of {analysis.files.length}. The JSON download has all of them.</div>
            )}
          </>
        )}
      </Section>

      {/* ── 2. Smell selection ────────────────────────────── */}
      <Section no="2" title="Developer smell selection" agent="diwo">
        {!selection ? <Missing>no smell selection was committed.</Missing> : (
          <div className="srep-kpis">
            <Kpi k="Smells detected" v={selection.total_smells} />
            <Kpi k="Selected for refactoring" v={selection.selected_count} />
            <Kpi k="Excluded by developer" v={selection.excluded_count} />
          </div>
        )}
      </Section>

      {/* ── 3. Plan & decisions ───────────────────────────── */}
      <Section no="3" title="Refactoring plan and developer decisions" agent={plan && plan.source !== 'rdp_agent' ? 'diwo' : 'rdp'}>
        {!plan ? <Missing>no refactoring plan was generated.</Missing> : (
          <>
            <div className="srep-kpis">
              <Kpi k="Plan" v={plan.plan_id || '—'} />
              <Kpi k="Produced by" v={plan.source === 'rdp_agent' ? 'RDP agent' : 'DIWO fallback'} />
              <Kpi k="Steps" v={plan.total_steps} />
              {decisions && <Kpi k="Approved" v={decisions.approved_count} />}
              {decisions && <Kpi k="Rejected" v={decisions.rejected_count} />}
              {decisions && <Kpi k="Manual work" v={decisions.manual_count} />}
              {decisions && <Kpi k="Against DIWO advice" v={decisions.override_count} />}
            </div>
            {plan.warning && <div className="srep-missing">RDP agent was not used: {plan.warning}</div>}
            <div className="srep-table-wrap">
              <table className="srep-table">
                <thead><tr><th>#</th><th>Smell → refactoring</th><th>File</th><th>Target</th><th>Risk</th><th>DIWO advice</th><th>Developer</th></tr></thead>
                <tbody>
                  {plan.steps.map((s) => (
                    <tr key={s.step_id}>
                      <td>{s.step_id}</td>
                      <td>{s.smell_type ? `${s.smell_type} → ` : ''}<b style={{ color: 'var(--text-primary)' }}>{s.refactoring || '—'}</b></td>
                      <td><code title={s.file || ''}>{baseName(s.file) || '—'}</code></td>
                      <td>{s.entity || '—'}</td>
                      <td>{s.risk || '—'}</td>
                      <td>{adviceTag(s.recommendation)}</td>
                      <td>{decisionTag(s.decision)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>

      {/* ── 4. Transformation ─────────────────────────────── */}
      <Section no="4" title="Code transformation and validation" agent="sctva">
        {!tx ? <Missing>no transformation was executed.</Missing> : (
          <>
            {tx.by !== 'sctva' && (
              <div className="srep-missing">
                This result came from the DIWO backend's simulated fallback, not from the SCTVA agent.
              </div>
            )}
            <div className="srep-kpis">
              <Kpi k="Files in result" v={tx.files.length} />
              <Kpi k="Files changed" v={changedFiles} />
              {tx.by === 'sctva' && <Kpi k="Confidence" v={pct(tx.confidence_score)} />}
              {tx.by === 'sctva' && <Kpi k="Validation" v={pct(tx.validation_score)} />}
              {tx.by === 'sctva' && <Kpi k="Replacements" v={tx.replacements} />}
              {tx.by === 'sctva' && <Kpi k="Rollback" v={tx.rollback ? 'yes' : 'no'} />}
            </div>
            {tx.files.length > 0 && (
              <div className="srep-table-wrap">
                <table className="srep-table">
                  <thead><tr><th>File</th><th>Changed</th><th>Developer review</th></tr></thead>
                  <tbody>
                    {tx.files.map((f) => (
                      <tr key={f.path}>
                        <td><code title={f.path}>{f.path}</code></td>
                        <td>{f.changed == null ? '—' : f.changed ? 'yes' : 'no'}</td>
                        <td>{decisionTag(f.decision)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </Section>

      {/* ── 5. Before / after ─────────────────────────────── */}
      <Section no="5" title="Before and after" agent="diwo">
        {!metrics ? <Missing>the DIWO backend returned no before/after figures.</Missing> : (
          <>
            <div className="srep-note">
              These figures are DIWO's estimates from the smell counts and the share of smells the
              session resolved. They are not measured from the refactored code.
            </div>
            <div className="srep-table-wrap">
              <table className="srep-table">
                <thead><tr><th>Metric</th><th>Before</th><th>After</th></tr></thead>
                <tbody>
                  {[
                    ['Code smells', 'total_smells'],
                    ['Complexity score', 'cyclomatic_complexity'],
                    ['Duplication (%)', 'code_duplication_pct'],
                    ['Maintainability index', 'maintainability_index'],
                  ].map(([label, key]) => (
                    <tr key={key}>
                      <td>{label}</td>
                      <td>{metrics.before?.[key] ?? '—'}</td>
                      <td>{metrics.after?.[key] ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>

      {/* ── 6. Final outcome ──────────────────────────────── */}
      <Section no="6" title="Final outcome" agent="diwo">
        {(outcome.accepted_files?.length || 0) + (outcome.reverted_files?.length || 0) === 0
          ? <Missing>no per-file accept or reject decision.</Missing>
          : (
            <div className="srep-table-wrap">
              <table className="srep-table">
                <thead><tr><th>File</th><th>Final state</th></tr></thead>
                <tbody>
                  {(outcome.accepted_files || []).map((p) => (
                    <tr key={`a-${p}`}><td><code>{p}</code></td><td><span className="srep-tag good">refactored</span></td></tr>
                  ))}
                  {(outcome.reverted_files || []).map((p) => (
                    <tr key={`r-${p}`}><td><code>{p}</code></td><td><span className="srep-tag bad">reverted to original</span></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        {outcome.archive && (
          <div className="srep-note">
            Refactored archive: {outcome.archive.file_count ?? '—'} file(s)
            {outcome.archive.bytes ? `, ${Math.round(outcome.archive.bytes / 1024)} KB` : ''}.
          </div>
        )}
        <div className="srep-note"><b>Developer notes:</b> {report.notes || 'none'}</div>
      </Section>

      {/* ── 7. Audit trail ────────────────────────────────── */}
      <Section no="7" title={`Audit trail (${trail.length} recorded event${trail.length === 1 ? '' : 's'})`} agent="diwo" open={false}>
        {trail.length === 0 ? <Missing>the backend audit trail could not be read.</Missing> : (
          <div className="srep-trail">
            {trail.map((e, i) => (
              <div className="srep-entry" key={e.id ?? i}>
                <div className="h">
                  <span className="stage">{e.stageLabel}</span>
                  <span className="t">{e.title}</span>
                  <span className="m">{e.actor} · {fmtDate(e.timestamp)}</span>
                </div>
                {e.facts.length > 0 && (
                  <div className="f">{e.facts.map((f) => <span key={f.label}><b>{f.label}:</b> {f.value}</span>)}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
