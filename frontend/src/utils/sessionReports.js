/**
 * sessionReports.js
 * -----------------
 * The report of one finished refactoring session: what CUQA found in the
 * repository, what the developer selected, the plan RDP produced and what was
 * decided on it, what SCTVA changed, and which files the developer kept.
 *
 * DIWO builds it when the developer presses "Complete Workflow"; the Reports
 * page shows it. Reports are kept in localStorage so they survive a reload.
 *
 * Nothing is filled in: a stage that did not run, or did not return data,
 * stays null and the report says so.
 */

import { categoryOf } from '../pages/diwo/utils/planningSelection.js';

const STORAGE_KEY = 'rfiq_session_reports';
const MAX_REPORTS = 12;
const MAX_SESSION_LOG = 200;

// ── Storage ────────────────────────────────────────────────────────────────

export function loadSessionReports() {
  try {
    const list = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

function persist(list) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
}

/** Drop the bulky parts (trail, notes log, per-file list) of a stored report. */
function slim(report) {
  return {
    ...report,
    audit_rows: [],
    session_log: [],
    analysis: report.analysis ? { ...report.analysis, files: (report.analysis.files || []).slice(0, 15) } : null,
    trimmed: true,
  };
}

/**
 * Save a report as the newest entry and return it with its id. If the
 * browser's storage is full, older reports are slimmed first, then this one.
 */
export function saveSessionReport(report) {
  const entry = {
    ...report,
    id: report.id || `session-${Date.now()}`,
    saved_at: new Date().toISOString(),
  };
  const others = loadSessionReports().filter((r) => r.id !== entry.id);
  const attempts = [
    [entry, ...others],
    [entry, ...others.map(slim)],
    [slim(entry), ...others.map(slim)],
    [slim(entry)],
  ];
  for (const list of attempts) {
    try {
      persist(list.slice(0, MAX_REPORTS));
      return entry;
    } catch {
      /* quota exceeded — try a smaller version */
    }
  }
  return entry;
}

export function deleteSessionReport(id) {
  const list = loadSessionReports().filter((r) => r.id !== id);
  try { persist(list); } catch { /* nothing to free */ }
  return list;
}

// ── Builder ────────────────────────────────────────────────────────────────

const pathOf = (f) => (f?.path || f?.relative_path || f?.file || '').replace(/\\/g, '/');

function countBy(items, keyOf) {
  const out = {};
  items.forEach((item) => {
    const key = keyOf(item);
    if (key) out[key] = (out[key] || 0) + 1;
  });
  return out;
}

function buildAnalysis(report) {
  if (!report || !Array.isArray(report.files)) return null;
  const summary = report.summary || {};
  const allSmells = report.files.flatMap((f) => f.code_smells || []);

  return {
    repo_name: report.repo_name || null,
    files_analyzed: summary.files_analyzed ?? report.files.length,
    total_lines_of_code: summary.total_lines_of_code ?? null,
    total_code_smells: summary.total_code_smells ?? allSmells.length,
    average_quality_score: summary.average_quality_score ?? null,
    severity: summary.smell_severity || countBy(allSmells, (s) => s.severity),
    categories: Object.entries(summary.code_smell_overview || {}).map(([name, value]) => ({
      name,
      count: value?.count ?? 0,
      priority: value?.priority || null,
      smells: value?.smells || [],
    })),
    smell_types: countBy(allSmells, (s) => s.type),
    files: report.files
      .map((f) => {
        const smells = f.code_smells || [];
        return {
          path: pathOf(f),
          language: f.language || null,
          quality_score: typeof f.quality_score === 'number' ? f.quality_score : null,
          loc: f.metrics?.lines_of_code ?? null,
          smells: smells.length,
          high: smells.filter((s) => s.severity === 'high').length,
          types: [...new Set(smells.map((s) => s.type))].slice(0, 6),
        };
      })
      .sort((a, b) => b.smells - a.smells),
  };
}

function buildPlan(plan, planMeta, decisions) {
  if (!plan || !Array.isArray(plan.steps)) return null;
  return {
    plan_id: plan.plan_id || null,
    source: planMeta?.plan_source || null,
    warning: planMeta?.plan_warning || null,
    total_steps: plan.steps.length,
    steps: plan.steps.map((step) => ({
      step_id: step.step_id,
      refactoring: step.refactoring || null,
      smell_type: step.smell_type || null,
      file: step.target?.file || null,
      entity: step.target?.method || step.target?.class || null,
      risk: step.risk || null,
      recommendation: categoryOf(step),
      decision: decisions?.[step.step_id] || null,
    })),
  };
}

function buildTransformation(workflow) {
  const files = workflow?.files || [];
  const sctva = workflow?.sctva || null;
  if (!sctva && files.length === 0) return null;

  return {
    by: sctva ? 'sctva' : 'simulated',
    agent_url: sctva?.agent_url || null,
    request_id: sctva?.request_id || null,
    success: sctva?.success ?? null,
    confidence_score: typeof sctva?.confidence_score === 'number' ? sctva.confidence_score : null,
    validation_score: typeof sctva?.validation_score === 'number' ? sctva.validation_score : null,
    replacements: sctva?.total_replacements ?? null,
    rollback: sctva?.rollback_occurred ?? null,
    file_summary: sctva?.file_summary || null,
    files: files.map((f) => ({
      path: pathOf(f),
      decision: f.decision || null,
      changed: typeof f.changed === 'boolean'
        ? f.changed
        : (typeof f.before === 'string' && typeof f.after === 'string' ? f.before !== f.after : null),
    })),
  };
}

/**
 * Assemble the report from the DIWO page's state at the moment the session
 * was completed. `metricsBefore` / `metricsAfter` must be the backend's own
 * figures, never the page's display fallback.
 */
export function buildSessionReport({
  workflowId,
  language,
  target,
  cuqaReport,
  workflow,
  plan,
  planMeta,
  decisions,
  metricsBefore,
  metricsAfter,
  auditRows,
  sessionLog,
  notes,
  completedOnBackend,
  completeError,
}) {
  const selection = workflow?.smell_selection_summary || null;
  return {
    id: workflowId || `local-${Date.now()}`,
    workflow_id: workflowId || null,
    completed_at: new Date().toISOString(),
    completed_on_backend: Boolean(completedOnBackend),
    complete_error: completeError || null,
    language: language || null,
    target: target || null,
    notes: notes || '',
    analysis: buildAnalysis(cuqaReport),
    selection: selection ? { ...selection } : null,
    plan: buildPlan(plan, planMeta, decisions),
    plan_decisions: workflow?.plan_approval_summary || null,
    transformation: buildTransformation(workflow),
    outcome: {
      accepted_files: workflow?.accepted_files || [],
      reverted_files: workflow?.transformation_rejected_files || [],
      archive: workflow?.archive || null,
    },
    metrics: metricsBefore || metricsAfter
      ? { before: metricsBefore || null, after: metricsAfter || null, estimated: true }
      : null,
    audit_rows: Array.isArray(auditRows) ? auditRows : [],
    session_log: (sessionLog || []).slice(-MAX_SESSION_LOG),
  };
}
