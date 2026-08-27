/**
 * Refactoring session report
 * ==========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Builds the printable report the Results stage hands the developer: one
 * self-contained HTML document describing everything the session did.
 *
 * What it replaces
 * ----------------
 * The previous report had four sections — metrics, severity counts, a
 * selection/approval line, and an audit table of
 *
 *     Stage | Action | Actor | Time
 *     Plan Approval | plan approved | developer | 27/08/2026, 14:32
 *
 * so the document a reviewer keeps recorded that a plan was approved without
 * recording which refactorings it contained, which file each touched, or which
 * smell any of it was for. Everything needed was already in the audit rows'
 * `details`; the report never looked at them.
 *
 * Now the report is built FROM the persisted trail: the detected smells per
 * file, what the developer selected and dropped, every planned
 * `smell → refactoring` pair with DIWO's recommendation, what was approved,
 * rejected and kept for manual work, the per-step transformation outcome, and
 * the trail itself with its evidence.
 *
 * Two rules
 * ---------
 * 1. NOTHING IS INVENTED. Every section is omitted when the trail carries no
 *    data for it, and says so, rather than printing a plausible zero. A report
 *    is evidence; a fabricated row in it is worse than a missing section.
 *
 * 2. EVERYTHING IS ESCAPED. The document is assembled as a string and written
 *    into a new window, so a file path, a developer note or a smell type
 *    containing markup would otherwise be parsed as HTML — and `notes` is a
 *    free-text box. esc() is applied at every interpolation.
 *
 * Pure: takes data, returns a string. No DOM, no network — so it is testable
 * under plain `node`.
 */

import { STAGE_LABEL, describeTotals, narrateAll } from "./auditNarrative.js";

// ─── Escaping ────────────────────────────────────────────────────────────────

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Escape a value for HTML text or an attribute. Applied at EVERY interpolation. */
export const esc = (value) => {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (ch) => ENTITIES[ch]);
};

/** Escaped, or an em dash when there is genuinely nothing to show. */
const or = (value, fallback = "—") => {
  if (value === null || value === undefined || value === "") return fallback;
  return esc(value);
};

// ─── Trail access ────────────────────────────────────────────────────────────

/** The details of the LAST row with this action — the outcome that stuck. */
export function lastDetails(rows, action) {
  const matches = (rows || []).filter((row) => row?.action === action);
  const row = matches[matches.length - 1];
  return row && typeof row.details === "object" && row.details ? row.details : null;
}

/** Every row with this action, oldest first. */
const allWith = (rows, action) => (rows || []).filter((row) => row?.action === action);

const fmtTime = (value) => {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
};

/** How long the session ran, from the first recorded event to the last. */
export function sessionSpan(rows) {
  const stamps = (rows || [])
    .map((row) => new Date(row?.timestamp).getTime())
    .filter((n) => Number.isFinite(n))
    .sort((a, b) => a - b);
  if (stamps.length < 2) return null;

  const minutes = Math.round((stamps[stamps.length - 1] - stamps[0]) / 60000);
  return {
    started: new Date(stamps[0]).toLocaleString(),
    ended: new Date(stamps[stamps.length - 1]).toLocaleString(),
    minutes,
  };
}

// ─── Building blocks ─────────────────────────────────────────────────────────

const section = (title, body) =>
  body ? `<section><h2>${esc(title)}</h2>${body}</section>` : "";

const table = (headers, rows) => {
  if (!rows || rows.length === 0) return "";
  return `<table>
    <thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((cells) => `<tr>${cells.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody>
  </table>`;
};

const emptyNote = (text) => `<p class="muted">${esc(text)}</p>`;

/** Highlighted "no data" so an absent section reads as absent, not as zero. */
const missing = (what) =>
  `<p class="missing">Not recorded in this session's audit trail — ${esc(what)}</p>`;

// ─── Sections ────────────────────────────────────────────────────────────────

function sessionSection({ workflowId, target, language, rows, planId, sctva }) {
  const span = sessionSpan(rows);
  const lines = [
    ["Workflow ID", workflowId],
    ["Target", target],
    ["Language", language],
    ["Plan", planId],
    ["SCTVA request", sctva?.request_id],
    ["Recorded events", (rows || []).length],
    ["Session started", span?.started],
    ["Session ended", span?.ended],
    ["Elapsed", span ? `${span.minutes} min` : null],
    ["Report generated", new Date().toLocaleString()],
  ].filter(([, value]) => value !== null && value !== undefined && value !== "");

  return `<div class="grid">${lines
    .map(([label, value]) => `<div><span class="k">${esc(label)}</span><span class="v">${esc(value)}</span></div>`)
    .join("")}</div>`;
}

function metricsSection(mb, ma, improvements) {
  const has = Object.keys(mb || {}).length || Object.keys(ma || {}).length;
  if (!has) return missing("no before/after metrics were captured");

  const row = (label, before, after, change) =>
    [esc(label), or(before, "0"), or(after, "0"), esc(change)];

  return table(["Metric", "Before", "After", "Change"], [
    row("Cyclomatic complexity", mb.cyclomatic_complexity ?? 0, ma.cyclomatic_complexity ?? 0,
        `${improvements.complexity_reduced_by || 0} reduced`),
    row("Code duplication (%)", mb.code_duplication_pct ?? 0, ma.code_duplication_pct ?? 0,
        `${improvements.duplication_reduced_by || 0}% reduced`),
    row("Maintainability index", mb.maintainability_index ?? 0, ma.maintainability_index ?? 0,
        `${improvements.maintainability_gained || 0} gained`),
    row("Total code smells", mb.total_smells ?? 0, ma.total_smells ?? 0,
        `${(mb.total_smells || 0) - (ma.total_smells || 0)} resolved`),
  ]);
}

/** Which files had smells, and of what kind — from the detection entries. */
function detectionSection(rows) {
  const details = lastDetails(rows, "cuqa_report_ingested")
    || lastDetails(rows, "workflow_started");
  if (!details) return missing("no detection event was recorded");

  const byFile = details.by_file || [];
  const head = `<div class="grid">
    ${details.repo_name ? `<div><span class="k">Repository</span><span class="v">${esc(details.repo_name)}</span></div>` : ""}
    ${details.files_analyzed ? `<div><span class="k">Files analysed</span><span class="v">${esc(details.files_analyzed)}</span></div>` : ""}
    <div><span class="k">Smells detected</span><span class="v">${esc(details.smell_count ?? 0)}</span></div>
    <div><span class="k">Files affected</span><span class="v">${esc(details.files_affected ?? byFile.length)}</span></div>
    <div><span class="k">Smell types</span><span class="v">${esc(describeTotals(details.smell_types, { max: 8 }) || "—")}</span></div>
    <div><span class="k">Severity</span><span class="v">${esc(describeTotals(details.severities, { max: 8 }) || "—")}</span></div>
  </div>`;

  const fileTable = table(["File", "Smells", "Types", "Severity"],
    byFile.map((f) => [
      `<code>${esc(f.file)}</code>`,
      esc(f.count),
      esc(describeTotals(f.types, { max: 6 })),
      esc(describeTotals(f.severities, { max: 6 })),
    ]));

  return head + (fileTable || emptyNote("No per-file breakdown was recorded."));
}

/** What the developer kept and what they dropped. */
function selectionSection(rows) {
  const details = lastDetails(rows, "smells_selected");
  if (!details) return missing("the developer never committed a smell selection");

  const smellRows = (list) => (list || []).map((s) => [
    esc(s.type),
    `<code>${esc(s.file)}</code>`,
    or(s.entity),
    esc((s.lines || []).join("–")),
    esc(s.severity),
  ]);

  const head = `<div class="grid">
    <div><span class="k">Selected</span><span class="v">${esc(details.selected_count ?? 0)}</span></div>
    <div><span class="k">Excluded</span><span class="v">${esc(details.excluded_count ?? 0)}</span></div>
    <div><span class="k">Files</span><span class="v">${esc((details.selected_files || []).length)}</span></div>
    <div><span class="k">Mode</span><span class="v">${or(details.selection_mode)}</span></div>
  </div>`;

  const kept = table(["Smell", "File", "Entity", "Lines", "Severity"],
                     smellRows(details.selected_smells));
  const dropped = table(["Smell", "File", "Entity", "Lines", "Severity"],
                        smellRows(details.excluded_smells));

  return head
    + (kept ? `<h3>Forwarded to planning</h3>${kept}` : "")
    + (dropped ? `<h3>Excluded by the developer</h3>${dropped}` : "");
}

/** Every planned step: which smell, which refactoring, and what DIWO advised. */
function planSection(rows) {
  const details = lastDetails(rows, "plan_generated");
  if (!details) return missing("no refactoring plan was generated");

  const head = `<div class="grid">
    <div><span class="k">Plan</span><span class="v">${or(details.plan_id)}</span></div>
    <div><span class="k">Produced by</span><span class="v">${details.source === "rdp_agent" ? "RDP agent" : or(details.source)}</span></div>
    <div><span class="k">Steps</span><span class="v">${esc(details.total_steps ?? details.steps ?? 0)}</span></div>
    <div><span class="k">Refactorings</span><span class="v">${esc(describeTotals(details.refactorings, { max: 8 }) || "—")}</span></div>
    <div><span class="k">DIWO recommended</span><span class="v">${esc(details.recommended ?? 0)}</span></div>
    <div><span class="k">Review carefully</span><span class="v">${esc(details.review ?? 0)}</span></div>
    <div><span class="k">Not recommended</span><span class="v">${esc(details.not_recommended ?? 0)}</span></div>
    <div><span class="k">Manual only</span><span class="v">${esc(details.manual_only ?? 0)}</span></div>
  </div>`;

  const steps = table(
    ["#", "Code smell", "Refactoring applied", "File", "Target", "Risk", "DIWO recommendation"],
    (details.step_detail || []).map((s) => [
      esc(s.step_id),
      or(s.smell_type),
      `<strong>${or(s.refactoring)}</strong>`,
      `<code>${esc(s.file)}</code>`,
      or(s.entity),
      or(s.risk),
      `<span class="tag ${esc(s.recommendation || "none")}">${or(s.recommendation)}</span>`,
    ]));

  const note = details.step_detail_omitted
    ? emptyNote(`${details.step_detail_omitted} further steps were not itemised in the trail.`)
    : "";

  return head + (steps || emptyNote("No step detail was recorded.")) + note;
}

/** What the developer authorised, refused, or took on by hand. */
function decisionSection(rows) {
  const details = lastDetails(rows, "plan_approved") || lastDetails(rows, "plan_modified");
  if (!details) return missing("the plan was never approved");

  const stepRows = (bucket) => (bucket?.steps || []).map((s) => [
    or(s.smell_type),
    `<strong>${or(s.refactoring)}</strong>`,
    `<code>${esc(s.file)}</code>`,
    or(s.entity),
    `<span class="tag ${esc(s.recommendation || "none")}">${or(s.recommendation)}</span>`,
  ]);

  const headers = ["Code smell", "Refactoring", "File", "Target", "DIWO advised"];
  const overrides = details.overrides || {};

  const head = `<div class="grid">
    <div><span class="k">Approved for automatic transformation</span><span class="v">${esc(details.approved?.count ?? 0)}</span></div>
    <div><span class="k">Rejected</span><span class="v">${esc(details.rejected?.count ?? 0)}</span></div>
    <div><span class="k">Kept for manual work</span><span class="v">${esc(details.manual?.count ?? 0)}</span></div>
    <div><span class="k">Approved against advice</span><span class="v">${esc(overrides.approved_not_recommended ?? 0)}</span></div>
    <div><span class="k">Rejected despite advice</span><span class="v">${esc(overrides.rejected_recommended ?? 0)}</span></div>
  </div>`;

  const approved = table(headers, stepRows(details.approved));
  const manual = table(headers, stepRows(details.manual));
  const rejected = table(headers, stepRows(details.rejected));

  return head
    + (approved ? `<h3>Sent to the Transformation agent</h3>${approved}` : "")
    + (manual ? `<h3>Kept for manual refactoring — not transformed</h3>${manual}` : "")
    + (rejected ? `<h3>Rejected by the developer</h3>${rejected}` : "");
}

/** What actually happened when the transformation ran. */
function transformationSection(rows) {
  const completed = lastDetails(rows, "transformation_completed");
  const sctva = lastDetails(rows, "sctva_transformation_executed");
  if (!completed && !sctva) return missing("no transformation was executed");

  const head = `<div class="grid">
    ${completed ? `<div><span class="k">Status</span><span class="v">${or(completed.status)}</span></div>` : ""}
    ${completed ? `<div><span class="k">Steps passed</span><span class="v">${esc(completed.passed ?? 0)}</span></div>` : ""}
    ${completed ? `<div><span class="k">Steps failed</span><span class="v">${esc(completed.failed ?? 0)}</span></div>` : ""}
    ${sctva ? `<div><span class="k">Actions dispatched</span><span class="v">${esc(sctva.actions ?? 0)}</span></div>` : ""}
    ${sctva ? `<div><span class="k">Executable</span><span class="v">${esc(sctva.executable ?? 0)}</span></div>` : ""}
    ${sctva ? `<div><span class="k">No-ops</span><span class="v">${esc(sctva.noops ?? 0)}</span></div>` : ""}
    ${sctva ? `<div><span class="k">Rolled back</span><span class="v">${sctva.rollback ? "yes" : "no"}</span></div>` : ""}
  </div>`;

  const steps = table(["Step", "Refactoring", "File", "Outcome", "Detail"],
    (completed?.step_detail || []).map((s) => [
      or(s.step_id),
      or(s.refactoring),
      `<code>${esc(s.file)}</code>`,
      `<span class="tag ${esc(s.status || "none")}">${or(s.status)}</span>`,
      or(s.message, ""),
    ]));

  const files = table(["File", "Changed", "Replacements", "Result"],
    (sctva?.file_detail || []).map((f) => [
      `<code>${esc(f.file)}</code>`,
      f.changed ? "yes" : "no",
      esc(f.replacements ?? 0),
      f.rolled_back ? "rolled back" : f.success ? "ok" : "failed",
    ]));

  return head
    + (steps ? `<h3>Per-step outcome</h3>${steps}` : "")
    + (files ? `<h3>Per-file outcome</h3>${files}` : "");
}

/** What the developer finally kept on disk. */
function outcomeSection({ acceptedFiles, rejectedFiles, rows }) {
  const reverts = allWith(rows, "refactoring_reverted");
  const accepted = (acceptedFiles || []).map((p) => [`<code>${esc(p)}</code>`, "accepted"]);
  const rejected = (rejectedFiles || []).map((p) => {
    const row = reverts.find((r) => r?.details?.file === p);
    const undone = (row?.details?.refactorings || []).join(", ");
    return [`<code>${esc(p)}</code>`, undone ? `reverted — ${esc(undone)} undone` : "reverted"];
  });

  const all = [...accepted, ...rejected];
  if (all.length === 0) return missing("no per-file accept/reject decision was recorded");
  return table(["File", "Final state"], all);
}

/** The trail itself, narrated, with its evidence inline. */
function trailSection(rows) {
  const entries = narrateAll(rows);
  if (entries.length === 0) return missing("the audit trail is empty");

  return entries.map((entry) => {
    const facts = entry.facts.length
      ? `<div class="facts">${entry.facts
          .map((f) => `<span><b>${esc(f.label)}:</b> ${esc(f.value)}</span>`).join("")}</div>`
      : "";

    const groups = entry.groups.map((g) => `
      <div class="evidence">
        <div class="evidence-label">${esc(g.label)} (${g.items.length})</div>
        <ul>${g.items.map((item) => `<li>${esc(item.primary)}${
          item.secondary ? ` <span class="muted">· ${esc(item.secondary)}</span>` : ""
        }${item.file ? ` <code>${esc(item.file)}</code>` : ""}${
          item.tag ? ` <span class="tag ${esc(item.tag)}">${esc(item.tag)}</span>` : ""
        }</li>`).join("")}</ul>
      </div>`).join("");

    return `<article class="entry">
      <div class="entry-head">
        <span class="stage">${esc(entry.stageLabel)}</span>
        <span class="title">${esc(entry.title)}</span>
        <span class="meta">${esc(entry.actor)} · ${esc(fmtTime(entry.timestamp))}</span>
      </div>
      ${facts}${groups}
      ${entry.note ? `<p class="muted">${esc(entry.note)}</p>` : ""}
    </article>`;
  }).join("");
}

// ─── Document ────────────────────────────────────────────────────────────────

const STYLES = `
  * { box-sizing: border-box; }
  body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif;
         padding: 32px 40px; color: #111827; line-height: 1.55; max-width: 1100px; margin: 0 auto; }
  h1 { color: #0f172a; border-bottom: 3px solid #2563eb; padding-bottom: 10px; margin-bottom: 4px; }
  h2 { color: #1e293b; margin-top: 32px; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; }
  h3 { color: #334155; margin-top: 18px; font-size: 14px; text-transform: uppercase;
       letter-spacing: 0.5px; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12.5px; }
  th, td { border: 1px solid #cbd5e1; padding: 7px 9px; text-align: left; vertical-align: top; }
  th { background: #f1f5f9; font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.4px; }
  code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; color: #334155;
         background: #f8fafc; padding: 1px 4px; border-radius: 3px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
          gap: 8px 16px; margin-top: 10px; background: #f8fafc; border: 1px solid #e2e8f0;
          border-radius: 8px; padding: 14px 16px; }
  .grid .k { display: block; font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.5px;
             color: #64748b; }
  .grid .v { display: block; font-weight: 700; font-size: 13.5px; color: #0f172a; }
  .muted { color: #64748b; font-size: 12px; }
  .missing { color: #b45309; background: #fffbeb; border: 1px solid #fde68a;
             border-radius: 6px; padding: 9px 12px; font-size: 12.5px; }
  .tag { display: inline-block; padding: 1px 7px; border-radius: 10px; font-size: 10.5px;
         font-weight: 700; background: #e2e8f0; color: #334155; text-transform: uppercase; }
  .tag.recommended, .tag.passed, .tag.ok { background: #dcfce7; color: #15803d; }
  .tag.review { background: #fef3c7; color: #b45309; }
  .tag.not_recommended, .tag.failed { background: #fee2e2; color: #b91c1c; }
  .tag.manual_only { background: #dbeafe; color: #1d4ed8; }
  .entry { border-left: 3px solid #cbd5e1; padding: 8px 0 8px 12px; margin-top: 12px;
           page-break-inside: avoid; }
  .entry-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
  .entry-head .stage { font-size: 10px; font-weight: 800; text-transform: uppercase;
                       letter-spacing: 0.6px; color: #2563eb; }
  .entry-head .title { font-weight: 700; font-size: 13.5px; }
  .entry-head .meta { margin-left: auto; font-size: 11px; color: #64748b; }
  .facts { margin-top: 4px; font-size: 11.5px; color: #475569;
           display: flex; flex-wrap: wrap; gap: 4px 14px; }
  .evidence { margin-top: 7px; }
  .evidence-label { font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.5px;
                    color: #64748b; font-weight: 700; }
  .evidence ul { margin: 3px 0 0; padding-left: 18px; font-size: 12px; }
  .evidence li { margin-bottom: 2px; }
  .footer { margin-top: 44px; padding-top: 12px; border-top: 1px solid #e2e8f0;
            font-size: 11px; color: #64748b; }
  @media print { body { padding: 0; } h2 { page-break-after: avoid; } table { page-break-inside: auto; } }
`;

/**
 * The whole report, as one HTML string.
 *
 * `rows` is the PERSISTED backend audit trail. Passing the session log instead
 * produces a report describing browser commentary, which is what the previous
 * version did — so the caller must hand over the backend rows.
 */
export function buildSummaryReportHtml({
  workflowId,
  target,
  language,
  rows = [],
  metricsBefore = {},
  metricsAfter = {},
  severityBreakdown = [],
  acceptedFiles = [],
  rejectedFiles = [],
  notes = "",
  sctva = null,
  archive = null,
} = {}) {
  const improvements = metricsAfter?.improvements || {};
  const planId = lastDetails(rows, "plan_generated")?.plan_id || null;

  const severity = table(["Severity", "Before", "After", "Change"],
    (severityBreakdown || []).map((s) => [
      esc(s.severity), esc(s.before), esc(s.after),
      esc(s.before - s.after > 0 ? `${s.before - s.after} resolved` : "—"),
    ]));

  const body = [
    section("Session", sessionSection({ workflowId, target, language, rows, planId, sctva })),
    section("Quality metrics — before and after",
            metricsSection(metricsBefore, metricsAfter, improvements)),
    section("Smell severity breakdown", severity || missing("no severity breakdown was captured")),
    section("1. Code smells detected", detectionSection(rows)),
    section("2. Developer smell selection", selectionSection(rows)),
    section("3. Refactoring plan — smell to refactoring", planSection(rows)),
    section("4. Developer decisions on the plan", decisionSection(rows)),
    section("5. Transformation outcome", transformationSection(rows)),
    section("6. Final file outcome", outcomeSection({ acceptedFiles, rejectedFiles, rows })),
    archive ? section("Downloaded archive", `<div class="grid">
        <div><span class="k">Scope</span><span class="v">${or(archive.scope)}</span></div>
        <div><span class="k">Files</span><span class="v">${esc(archive.included ?? 0)}</span></div>
        ${archive.bytes ? `<div><span class="k">Size</span><span class="v">${Math.round(archive.bytes / 1024)} KB</span></div>` : ""}
      </div>`) : "",
    section("Full audit trail", trailSection(rows)),
    section("Developer final notes",
            notes ? `<p>${esc(notes)}</p>` : emptyNote("No final notes were provided.")),
  ].join("");

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>DIWO Refactoring Report — ${esc(target || workflowId || "session")}</title>
  <style>${STYLES}</style>
</head>
<body>
  <h1>DIWO Refactoring Session Report</h1>
  <p class="muted">
    Developer Interaction &amp; Workflow Orchestration Agent — every figure below
    is taken from this session's persisted audit trail. Sections with no
    recorded data are marked rather than filled in.
  </p>
  ${body}
  <div class="footer">
    Generated by the Developer Interaction &amp; Workflow Orchestration Agent
    (R26-SE-008) on ${esc(new Date().toLocaleString())} from
    ${esc((rows || []).length)} recorded workflow event(s).
  </div>
  <script>window.onload = function () { window.print(); };${"<"}/script>
</body>
</html>`;
}
