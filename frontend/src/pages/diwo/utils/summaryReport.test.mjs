/**
 * summaryReport.test.mjs
 * ======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The report is the artefact that leaves the tool — printed, filed, attached to
 * a dissertation. Two classes of failure matter:
 *
 *   1. It says something the session did not do. A fabricated row in an
 *      evidence document is worse than a missing section, so an absent record
 *      must render as "not recorded", never as a plausible zero.
 *
 *   2. It executes something. The document is assembled as a string and written
 *      into a new window with document.write, so ANY unescaped interpolation is
 *      live markup — and one of the inputs is a free-text notes box.
 *
 * Both are asserted below against the shapes backend/domain/audit_detail.py
 * actually emits.
 *
 *     npm run test:report          (from frontend/)
 */

import { buildSummaryReportHtml, esc, lastDetails, sessionSpan } from "./summaryReport.js";

let failures = 0;
const check = (label, condition, detail = "") => {
  if (!condition) failures += 1;
  console.log(`  [${condition ? "PASS" : "FAIL"}] ${label}${!condition && detail ? `  -- ${detail}` : ""}`);
};
const eq = (label, actual, expected) =>
  check(label, JSON.stringify(actual) === JSON.stringify(expected),
        `got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);

const ROWS = [
  {
    id: 1, stage: "smell_review", action: "cuqa_report_ingested", actor: "system",
    timestamp: "2026-08-27T10:00:00Z",
    details: {
      repo_name: "shop-service", files_analyzed: 12, smell_count: 3, files_affected: 2,
      smell_types: { "Long Method": 2, "God Class": 1 },
      severities: { high: 2, medium: 1 },
      by_file: [
        { file: "src/OrderService.java", count: 2,
          types: { "Long Method": 1, "God Class": 1 }, severities: { high: 2 } },
        { file: "src/Pay.java", count: 1,
          types: { "Long Method": 1 }, severities: { medium: 1 } },
      ],
    },
  },
  {
    id: 2, stage: "smell_selection", action: "smells_selected", actor: "developer",
    timestamp: "2026-08-27T10:05:00Z",
    details: {
      selected_count: 2, excluded_count: 1, selection_mode: "file",
      selected_files: ["src/OrderService.java"],
      selected_smells: [
        { id: "a:1", type: "Long Method", severity: "high",
          file: "src/OrderService.java", entity: "processOrder", lines: [41, 120] },
      ],
      excluded_smells: [
        { id: "b:1", type: "Long Method", severity: "medium", file: "src/Pay.java" },
      ],
    },
  },
  {
    id: 3, stage: "plan_approval", action: "plan_generated", actor: "system",
    timestamp: "2026-08-27T10:06:00Z",
    details: {
      plan_id: "plan_7c2", source: "rdp_agent", total_steps: 2,
      recommended: 1, review: 1,
      refactorings: { "Extract Method": 1, "Extract Class": 1 },
      by_file: [{ file: "src/OrderService.java", count: 2,
                  refactorings: { "Extract Method": 1, "Extract Class": 1 } }],
      step_detail: [
        { step_id: 1, smell_type: "Long Method", refactoring: "Extract Method",
          file: "src/OrderService.java", entity: "OrderService.processOrder",
          risk: "low", recommendation: "recommended" },
        { step_id: 2, smell_type: "God Class", refactoring: "Extract Class",
          file: "src/OrderService.java", risk: "high", recommendation: "review" },
      ],
    },
  },
  {
    id: 4, stage: "plan_approval", action: "plan_approved", actor: "developer",
    timestamp: "2026-08-27T10:20:00Z",
    details: {
      plan_id: "plan_7c2",
      approved: { count: 1, steps: [{ step_id: 1, smell_type: "Long Method",
                                      refactoring: "Extract Method",
                                      file: "src/OrderService.java",
                                      recommendation: "recommended" }] },
      rejected: { count: 1, steps: [{ step_id: 2, smell_type: "God Class",
                                      refactoring: "Extract Class",
                                      file: "src/OrderService.java",
                                      recommendation: "review" }] },
      manual: { count: 0, steps: [] },
      overrides: { approved_not_recommended: 0, rejected_recommended: 0 },
    },
  },
  {
    id: 5, stage: "transformation", action: "transformation_completed", actor: "system",
    timestamp: "2026-08-27T10:22:00Z",
    details: {
      status: "ok", passed: 1, failed: 0, plan_id: "plan_7c2",
      step_detail: [{ step_id: 1, refactoring: "Extract Method",
                      file: "src/OrderService.java", status: "passed" }],
      files_changed: ["src/OrderService.java"],
    },
  },
];

const build = (overrides = {}) => buildSummaryReportHtml({
  workflowId: "wf_abc123", target: "shop-service", language: "java",
  rows: ROWS,
  metricsBefore: { cyclomatic_complexity: 18, code_duplication_pct: 12,
                   maintainability_index: 61, total_smells: 3 },
  metricsAfter: { cyclomatic_complexity: 11, code_duplication_pct: 7,
                  maintainability_index: 74, total_smells: 1,
                  improvements: { complexity_reduced_by: 7, duplication_reduced_by: 5,
                                  maintainability_gained: 13 } },
  severityBreakdown: [{ severity: "high", before: 2, after: 0 },
                      { severity: "medium", before: 1, after: 1 }],
  acceptedFiles: ["src/OrderService.java"],
  rejectedFiles: [],
  notes: "Looks good.",
  ...overrides,
});

console.log("\nSession report\n");

// ── 1. The substance is present ──────────────────────────────────────────────
console.log("Content");
{
  const html = build();

  check("the session block names the workflow", html.includes("wf_abc123"));
  check("and the target", html.includes("shop-service"));
  check("and the plan id from the trail", html.includes("plan_7c2"));

  check("detected smells are broken down by file",
        html.includes("src/Pay.java") && html.includes("Code smells detected"));
  check("the smell types are summarised", html.includes("Long Method"));

  check("what the developer selected is listed",
        html.includes("Forwarded to planning") && html.includes("processOrder"));
  check("and what they excluded", html.includes("Excluded by the developer"));

  check("every planned smell -> refactoring pair is a row",
        html.includes("Extract Method") && html.includes("Extract Class"));
  check("with DIWO's recommendation beside it", html.includes("recommended"));

  check("the approved set is its own table",
        html.includes("Sent to the Transformation agent"));
  check("the rejected set is kept, not dropped",
        html.includes("Rejected by the developer"));

  check("the transformation outcome is per step",
        html.includes("Per-step outcome") && html.includes("passed"));

  check("metrics are tabulated", html.includes("Cyclomatic complexity")
        && html.includes("13 gained"));
  check("the full trail is included", html.includes("Full audit trail"));
  check("the notes are included", html.includes("Looks good."));
  check("the document is self-contained", html.startsWith("<!doctype html>")
        && html.includes("</html>"));
}

// ── 2. Nothing is invented ───────────────────────────────────────────────────
console.log("\nHonesty");
{
  const empty = buildSummaryReportHtml({ workflowId: "wf_none", rows: [] });

  check("an empty trail does not fabricate a detection section",
        empty.includes("no detection event was recorded"));
  check("nor a selection", empty.includes("never committed a smell selection"));
  check("nor a plan", empty.includes("no refactoring plan was generated"));
  check("nor an approval", empty.includes("the plan was never approved"));
  check("nor a transformation", empty.includes("no transformation was executed"));
  check("absent metrics are declared, not zeroed",
        empty.includes("no before/after metrics were captured"));
  check("it still renders a valid document", empty.startsWith("<!doctype html>"));
  check("and does not claim a step count it does not have",
        !empty.includes("Extract Method"));

  // A partial session — planned but never approved — must not imply approval.
  const partial = buildSummaryReportHtml({ workflowId: "w", rows: ROWS.slice(0, 3) });
  check("a plan with no approval says so",
        partial.includes("the plan was never approved"));
  check("but still reports the plan itself", partial.includes("plan_7c2"));

  check("a missing note is stated, not blank",
        build({ notes: "" }).includes("No final notes were provided."));
}

// ── 3. Escaping — the document is written into a live window ─────────────────
console.log("\nEscaping");
{
  eq("angle brackets are neutralised", esc("<b>x</b>"), "&lt;b&gt;x&lt;/b&gt;");
  eq("quotes are neutralised", esc(`"a" 'b'`), "&quot;a&quot; &#39;b&#39;");
  eq("ampersands are escaped first", esc("&lt;"), "&amp;lt;");
  eq("null renders as nothing", esc(null), "");

  const attack = "</p><script>window.__pwned=1</script>";
  const html = build({ notes: attack });
  check("a script tag in the notes is not emitted live",
        !html.includes("<script>window.__pwned"), "notes were interpolated raw");
  check("but its text is still shown", html.includes("&lt;script&gt;"));

  // The same must hold for anything that reaches the report from a file path
  // or a smell type — those come from an agent, not from a trusted constant.
  const nastyRows = [{
    id: 1, stage: "smell_review", action: "cuqa_report_ingested", actor: "system",
    details: {
      smell_count: 1, files_affected: 1,
      by_file: [{ file: "<img src=x onerror=alert(1)>", count: 1,
                  types: { "<b>Long</b>": 1 }, severities: { high: 1 } }],
    },
  }];
  const nasty = buildSummaryReportHtml({ workflowId: "w", rows: nastyRows });
  check("a hostile file path is escaped", !nasty.includes("<img src=x"));
  check("a hostile smell type is escaped", !nasty.includes("<b>Long</b>"));
  check("the workflow id is escaped",
        !buildSummaryReportHtml({ workflowId: "<script>x</script>", rows: [] })
          .includes("<script>x</script>"));

  // The closing tag of the report's own print script must survive intact.
  check("the report still carries its print script",
        html.includes("window.print()") && html.includes("</script>"));
}

// ── 4. Helpers ───────────────────────────────────────────────────────────────
console.log("\nHelpers");
{
  eq("lastDetails takes the last matching row",
     lastDetails([
       { action: "x", details: { n: 1 } },
       { action: "x", details: { n: 2 } },
     ], "x"), { n: 2 });
  eq("and null when the action never happened", lastDetails(ROWS, "never"), null);
  eq("a non-object details block is refused",
     lastDetails([{ action: "x", details: "nope" }], "x"), null);

  const span = sessionSpan(ROWS);
  check("the session span is measured from the trail", span.minutes === 22, String(span?.minutes));
  check("one event is not a span", sessionSpan([ROWS[0]]) === null);
  check("no events is not a span", sessionSpan([]) === null);
}

console.log(`\n${failures === 0 ? "All checks passed." : `${failures} check(s) FAILED.`}\n`);
process.exit(failures === 0 ? 0 : 1);
