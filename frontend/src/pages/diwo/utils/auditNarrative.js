/**
 * Audit trail narration
 * =====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Turns one backend audit row into something a person can read.
 *
 * The rows arrive as
 *
 *     { stage, action, actor, timestamp, details: { ... } }
 *
 * and the sidebar used to render them as `[plan_approval] plan_approved
 * (developer)` — the stage name and the event name, which is roughly "an agent
 * ran, then another agent ran". Everything that made the entry worth keeping
 * was inside `details`, and `details` was thrown away.
 *
 * This module reads `details` and produces:
 *
 *     title    one line naming what happened, with the numbers in it
 *     facts    the headline figures, as label/value pairs
 *     groups   the itemised evidence — file -> smell -> refactoring
 *
 * PRESENTATION ONLY. Every fact comes from the row; nothing here recomputes a
 * count, re-derives a category, or infers a file. Where the backend recorded
 * nothing, the entry renders as its title and stops — an audit trail that
 * invents detail is worse than one that is terse.
 *
 * Dependency-free so it can be tested under plain `node`:
 *
 *     npm run test:audit          (from frontend/)
 */

// ─── Stages ──────────────────────────────────────────────────────────────────

export const STAGE_LABEL = {
  smell_review: "Code Understanding",
  smell_selection: "Smell Selection",
  plan_approval: "Refactoring Plan",
  transformation: "Transformation",
  comparison: "Results",
  completed: "Completed",
};

/** Semantic tone, used for the colour AND stated in words by the caller. */
export const TONE = {
  INFO: "info",
  SUCCESS: "success",
  WARN: "warn",
  DANGER: "danger",
};

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "Long Method x2, Feature Envy" from {"Long Method": 2, "Feature Envy": 1}. */
export function describeTotals(totals, { max = 4 } = {}) {
  const entries = Object.entries(totals || {})
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return "";

  const shown = entries.slice(0, max)
    .map(([name, n]) => (n > 1 ? `${name} ×${n}` : name));
  const rest = entries.length - shown.length;
  return shown.join(", ") + (rest > 0 ? `, +${rest} more` : "");
}

/** "src/main/java/OrderService.java" -> "OrderService.java" for a tight column. */
export const baseName = (path) =>
  typeof path === "string" ? path.split(/[\\/]/).pop() || path : "";

// ─── Item renderers ──────────────────────────────────────────────────────────

/** A detected smell: "Long Method · processOrder · L10-60". */
const smellLine = (smell) => ({
  key: smell.id || `${smell.file}:${smell.type}:${(smell.lines || []).join("-")}`,
  primary: smell.type || "Unknown smell",
  secondary: [
    smell.entity,
    (smell.lines || []).length ? `L${smell.lines.join("-")}` : null,
  ].filter(Boolean).join(" · "),
  file: smell.file,
  tag: smell.severity,
});

/** A plan step: "Long Method → Extract Method". */
const stepLine = (step) => ({
  key: `${step.step_id}:${step.smell_id || step.refactoring}`,
  primary: step.smell_type
    ? `${step.smell_type} → ${step.refactoring}`
    : step.refactoring || "Step",
  secondary: [
    step.entity,
    (step.lines || []).length ? `L${step.lines.join("-")}` : null,
    step.risk ? `risk ${step.risk}` : null,
  ].filter(Boolean).join(" · "),
  file: step.file,
  tag: step.recommendation || step.decision,
});

/** A per-file rollup row from smells_by_file / steps_by_file. */
const fileRollupLine = (row) => ({
  key: row.file,
  primary: baseName(row.file),
  secondary: describeTotals(row.types || row.refactorings),
  file: row.file,
  tag: `${row.count}`,
});

const group = (label, items, renderer) =>
  Array.isArray(items) && items.length
    ? { label, items: items.map(renderer) }
    : null;

// ─── Per-action narration ────────────────────────────────────────────────────

/**
 * One entry per backend action. Each returns { title, tone, facts, groups }.
 *
 * The map is exhaustive over the actions the backend emits; anything not
 * listed falls through to a readable default rather than being hidden, because
 * a new event type appearing as a plain row is a much smaller problem than one
 * silently missing from the trail.
 */
const NARRATORS = {
  workflow_started: (d) => ({
    title: `Workflow started on ${d.target || "the project"}`,
    tone: TONE.INFO,
    facts: [
      ["Language", d.language],
      ["Smells detected", d.smell_count],
      ["Files affected", d.files_affected],
      ["Source", d.source],
    ],
    groups: [
      group("Files with detected smells", d.by_file, fileRollupLine),
      group("Detected smells", d.smells, smellLine),
    ],
    note: d.smells_omitted ? `${d.smells_omitted} further smells not listed.` : null,
  }),

  cuqa_report_ingested: (d) => ({
    title: `Code Understanding agent analysed ${plural(d.files_analyzed || 0, "file")}`,
    tone: TONE.INFO,
    facts: [
      ["Repository", d.repo_name],
      ["Smells found", d.smell_count],
      ["Files with smells", d.files_affected],
      ["Smell types", describeTotals(d.smell_types)],
      ["Severity", describeTotals(d.severities)],
    ],
    groups: [group("Files with detected smells", d.by_file, fileRollupLine)],
  }),

  smells_selected: (d) => ({
    title: `Developer selected ${plural(d.selected_count ?? (d.selected || []).length, "smell")}`
      + (d.excluded_count ? `, excluded ${d.excluded_count}` : ""),
    tone: TONE.SUCCESS,
    facts: [
      ["Files", (d.selected_files || []).length],
      ["Smell types", describeTotals(d.smell_types)],
      ["Severity", describeTotals(d.severities)],
      ["Mode", d.selection_mode],
    ],
    groups: [
      group("Selected — by file", d.by_file, fileRollupLine),
      group("Selected smells", d.selected_smells, smellLine),
      group("Excluded smells", d.excluded_smells, smellLine),
    ],
  }),

  plan_generated: (d) => ({
    title: `Planning agent produced ${plural(d.total_steps ?? d.steps ?? 0, "refactoring step")}`,
    tone: TONE.INFO,
    facts: [
      ["Plan", d.plan_id],
      ["Source", d.source === "rdp_agent" ? "RDP agent" : d.source],
      ["Refactorings", describeTotals(d.refactorings)],
      ["Recommended", d.recommended],
      ["Review carefully", d.review],
      ["Not recommended", d.not_recommended],
      ["Manual only", d.manual_only],
    ],
    groups: [
      group("Planned work — by file", d.by_file, fileRollupLine),
      group("Steps: smell → refactoring", d.step_detail, stepLine),
    ],
    note: d.step_detail_omitted ? `${d.step_detail_omitted} further steps not listed.` : null,
  }),

  plan_modified: (d) => ({
    title: `Plan reduced to ${plural(d.steps_after ?? 0, "approved step")}`,
    tone: TONE.WARN,
    facts: [
      ["Approved", d.approved?.count],
      ["Rejected", d.rejected?.count],
      ["Manual", d.manual?.count],
    ],
    groups: [
      group("Rejected", d.rejected?.steps, stepLine),
      group("Approved", d.approved?.steps, stepLine),
    ],
  }),

  plan_approved: (d) => ({
    title: `Developer approved ${plural(d.approved?.count ?? d.steps ?? 0, "step")} for transformation`,
    tone: TONE.SUCCESS,
    facts: [
      ["Plan", d.plan_id],
      ["Approved", d.approved?.count],
      ["Rejected", d.rejected?.count],
      ["Manual work", d.manual?.count],
      ["Approved against advice", d.overrides?.approved_not_recommended],
      ["Rejected despite advice", d.overrides?.rejected_recommended],
    ],
    groups: [
      group("Sent to the Transformation agent", d.approved?.steps, stepLine),
      group("Kept for manual work", d.manual?.steps, stepLine),
      group("Rejected", d.rejected?.steps, stepLine),
    ],
  }),

  plan_steps_manual: (d) => ({
    title: `${plural(d.count ?? 0, "step")} kept for manual refactoring`,
    tone: TONE.INFO,
    facts: [],
    groups: [group("Not sent to SCTVA", d.steps, stepLine)],
    note: d.note,
  }),

  plan_rejected: (d) => ({
    title: "Developer rejected the whole plan",
    tone: TONE.DANGER,
    facts: [["Reason", d.reason]],
    groups: [],
  }),

  rdp_plan_generated: (d) => ({
    title: `Planning agent returned a plan (${d.steps ?? "?"} steps)`,
    tone: TONE.INFO,
    facts: [["Plan", d.plan_id], ["RDP", d.rdp_url]],
    groups: [],
  }),

  rdp_plan_failed: (d) => ({
    title: "Planning agent unavailable — local fallback plan used",
    tone: TONE.WARN,
    facts: [["RDP", d.rdp_url], ["Reason", d.reason || d.error]],
    groups: [],
  }),

  sctva_transformation_executed: (d) => ({
    title: d.success
      ? `Transformation agent applied ${plural(d.executable ?? 0, "action")}`
      : "Transformation agent reported a failure",
    tone: d.rollback ? TONE.WARN : d.success ? TONE.SUCCESS : TONE.DANGER,
    facts: [
      ["Language", d.language],
      ["Actions", d.actions],
      ["Executable", d.executable],
      ["No-ops", d.noops],
      ["Files sent", d.files_sent],
      ["Files missing", d.files_missing],
      ["Rolled back", d.rollback ? "yes" : "no"],
    ],
    // No per-action list. The dispatched actions are a one-to-one restatement
    // of the approved plan steps, which the plan_approved entry already names
    // as smell -> refactoring -> file — so listing them again here repeated the
    // same content under a second heading. The Actions / Executable / No-ops
    // facts above carry the counts, which is the part this entry adds.
    groups: [
      group("Files transformed", d.file_detail, (f) => ({
        key: f.file,
        primary: baseName(f.file),
        secondary: f.changed
          ? `${plural(f.replacements || 0, "replacement")}`
          : "unchanged",
        file: f.file,
        tag: f.rolled_back ? "rolled back" : f.success ? "ok" : "failed",
      })),
    ],
  }),

  sctva_execute_failed: (d) => ({
    title: "Transformation agent call failed",
    tone: TONE.DANGER,
    facts: [["SCTVA", d.sctva_url], ["Status", d.status], ["Reason", d.reason]],
    groups: [],
  }),

  sctva_sources_failed: (d) => ({
    title: "Source files could not be read for transformation",
    tone: TONE.DANGER,
    facts: [["Missing", (d.missing || []).length]],
    groups: [group("Unreadable paths", (d.missing || []).map((f) => ({ file: f })),
                   (r) => ({ key: r.file, primary: baseName(r.file), file: r.file }))],
  }),

  transformation_completed: (d) => ({
    title: `Transformation finished — ${d.passed ?? 0} passed, ${d.failed ?? 0} failed`,
    tone: d.failed ? TONE.WARN : TONE.SUCCESS,
    facts: [["Plan", d.plan_id], ["Status", d.status]],
    groups: [
      group("Step outcomes", d.step_detail, (st) => ({
        key: `${st.step_id}:${st.refactoring}`,
        primary: st.refactoring || `Step ${st.step_id}`,
        secondary: st.message || "",
        file: st.file,
        tag: st.status,
      })),
      group("Files changed", (d.files_changed || []).map((f) => ({ file: f })),
            (r) => ({ key: r.file, primary: baseName(r.file), file: r.file })),
    ],
  }),

  rollback_triggered: (d) => ({
    title: "Developer rolled the transformation back",
    tone: TONE.DANGER,
    facts: [["Reason", d.reason], ["Snapshot", d.snapshot_id]],
    groups: [],
  }),

  transformation_accepted: (d) => ({
    title: `Developer accepted ${plural((d.accepted_files || []).length, "file")}`,
    tone: TONE.SUCCESS,
    facts: [["Rating", d.rating], ["Reverted", (d.rejected_files || []).length]],
    groups: [
      group("Accepted", (d.accepted_files || []).map((f) => ({ file: f })),
            (r) => ({ key: r.file, primary: baseName(r.file), file: r.file })),
    ],
  }),

  refactoring_reverted: (d) => ({
    title: `Reverted ${baseName(d.file) || "a file"}`,
    tone: TONE.WARN,
    facts: [
      ["File", d.file],
      ["Refactorings undone", (d.refactorings || []).join(", ")],
    ],
    groups: [],
  }),

  workflow_completed: (d) => ({
    title: "Workflow completed",
    tone: TONE.SUCCESS,
    facts: [["Notes", d.final_notes]],
    groups: [],
  }),

  archive_built: (d) => ({
    title: "Refactored project archived",
    tone: TONE.INFO,
    facts: [["Files", d.files], ["Bytes", d.bytes]],
    groups: [],
  }),

  archive_failed: (d) => ({
    title: "Archive could not be built",
    tone: TONE.WARN,
    facts: [["Reason", d.reason]],
    groups: [],
  }),
};

/** "plan_step_accepted" -> "Plan step accepted", for an action with no narrator. */
const humanize = (action) => {
  const words = String(action || "event").replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

/**
 * Narrate one audit row.
 *
 * Always returns a renderable entry, even for an action this module has never
 * heard of and even when `details` is empty — the sidebar must not go blank
 * because the backend gained an event.
 */
export function narrate(row) {
  const details = row && typeof row.details === "object" && row.details !== null
    ? row.details
    : {};

  const narrator = NARRATORS[row?.action];
  const base = narrator
    ? narrator(details)
    : { title: humanize(row?.action), tone: TONE.INFO, facts: [], groups: [] };

  // Only pairs the backend actually filled. A fact list padded with "—" is how
  // a detailed entry starts looking like an empty one. A numeric 0 IS kept:
  // "Files missing: 0" and "files missing: not recorded" are different claims,
  // and this trail exists to tell them apart.
  const facts = (base.facts || [])
    .filter(([, value]) =>
      typeof value === "number"
        ? Number.isFinite(value)
        : value !== undefined && value !== null && value !== "")
    .map(([label, value]) => ({ label, value: String(value) }));

  const groups = (base.groups || []).filter(Boolean);

  return {
    id: row?.id,
    stage: row?.stage,
    stageLabel: STAGE_LABEL[row?.stage] || humanize(row?.stage),
    action: row?.action,
    actor: row?.actor || "system",
    timestamp: row?.timestamp,
    title: base.title,
    tone: base.tone || TONE.INFO,
    facts,
    groups,
    note: base.note || null,
    /** True when there is anything worth expanding to. */
    expandable: facts.length > 0 || groups.length > 0,
  };
}

/** Narrate a whole trail, newest first, skipping rows that are not objects. */
export function narrateAll(rows) {
  return (rows || [])
    .filter((row) => row && typeof row === "object")
    .map(narrate);
}

/**
 * Group a narrated trail by workflow stage, preserving arrival order.
 *
 * The trail is a story with chapters — understanding, selection, planning,
 * transformation, results — and a flat list of thirty rows hides that
 * structure exactly when the developer is trying to retrace it.
 */
export function groupByStage(entries) {
  const groups = [];
  const index = new Map();

  (entries || []).forEach((entry) => {
    let bucket = index.get(entry.stage);
    if (!bucket) {
      bucket = { stage: entry.stage, label: entry.stageLabel, entries: [] };
      index.set(entry.stage, bucket);
      groups.push(bucket);
    }
    bucket.entries.push(entry);
  });

  return groups;
}
