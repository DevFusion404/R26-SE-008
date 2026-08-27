/**
 * auditNarrative.test.mjs
 * =======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The audit trail is the artefact the research claim rests on: it is the
 * evidence that a human decided, and that the system recorded what was decided
 * about which smell in which file. So the things asserted here are the ones
 * that would quietly destroy that evidence:
 *
 *   - a row's detail is rendered, not dropped (the original bug);
 *   - a file / smell / refactoring is never invented when absent;
 *   - a numeric 0 survives, because "0 files missing" and "not recorded" are
 *     different claims;
 *   - an unknown action still renders rather than vanishing from the trail.
 *
 * Fixtures below mirror what backend/domain/audit_detail.py emits. Nothing
 * here recomputes a count or a category — the narrator only reads.
 *
 * Dependency-free:
 *
 *     npm run test:audit          (from frontend/)
 */

import {
  STAGE_LABEL, describeTotals, groupByStage, narrate, narrateAll,
} from "./auditNarrative.js";

let failures = 0;
const check = (label, condition, detail = "") => {
  if (!condition) failures += 1;
  console.log(`  [${condition ? "PASS" : "FAIL"}] ${label}${!condition && detail ? `  -- ${detail}` : ""}`);
};
const eq = (label, actual, expected) =>
  check(label, JSON.stringify(actual) === JSON.stringify(expected),
        `got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);

const factValue = (entry, label) =>
  entry.facts.find((f) => f.label === label)?.value;
const groupNamed = (entry, label) =>
  entry.groups.find((g) => g.label === label);

console.log("\nAudit trail narration\n");

// ── 1. Detection: which files, which smells ──────────────────────────────────
console.log("Code understanding");
{
  const entry = narrate({
    id: 1, stage: "smell_review", action: "cuqa_report_ingested", actor: "system",
    timestamp: "2026-08-27T10:00:00Z",
    details: {
      repo_name: "shop-service", files_analyzed: 12, smell_count: 3,
      files_affected: 2,
      smell_types: { "Long Method": 2, "Feature Envy": 1 },
      severities: { high: 1, medium: 1, low: 1 },
      by_file: [
        { file: "src/OrderService.java", count: 2,
          types: { "Long Method": 1, "Feature Envy": 1 },
          severities: { high: 1, medium: 1 } },
        { file: "src/Pay.java", count: 1, types: { "Long Method": 1 },
          severities: { low: 1 } },
      ],
    },
  });

  check("the title says what the agent did", /analysed 12 files/.test(entry.title), entry.title);
  check("the stage is named for a human", entry.stageLabel === "Code Understanding");
  eq("the repository is a stated fact", factValue(entry, "Repository"), "shop-service");
  eq("smell types are summarised", factValue(entry, "Smell types"), "Long Method ×2, Feature Envy");

  const files = groupNamed(entry, "Files with detected smells");
  check("the per-file rollup is rendered", !!files);
  check("both files are listed", files.items.length === 2);
  eq("a file row names the file", files.items[0].primary, "OrderService.java");
  eq("and what was found in it", files.items[0].secondary, "Long Method, Feature Envy");
  eq("and how many", files.items[0].tag, "2");
  check("the full path is kept for the tooltip",
        files.items[0].file === "src/OrderService.java");
  check("the entry is expandable because it has evidence", entry.expandable === true);
}

// ── 2. Selection: kept vs dropped ────────────────────────────────────────────
console.log("\nSmell selection");
{
  const entry = narrate({
    id: 2, stage: "smell_selection", action: "smells_selected", actor: "developer",
    details: {
      selected_count: 2, excluded_count: 1, selection_mode: "file",
      selected_files: ["src/OrderService.java"],
      smell_types: { "Long Method": 2 },
      selected_smells: [
        { id: "a:10:0", type: "Long Method", severity: "high",
          file: "src/OrderService.java", entity: "processOrder", lines: [10, 60] },
        { id: "a:80:1", type: "Long Method", severity: "medium",
          file: "src/OrderService.java", entity: "audit", lines: [80] },
      ],
      excluded_smells: [
        { id: "b:5:0", type: "Feature Envy", severity: "low", file: "src/Pay.java" },
      ],
    },
  });

  check("the title carries both numbers",
        /selected 2 smells/.test(entry.title) && /excluded 1/.test(entry.title), entry.title);
  check("the developer is credited as the actor", entry.actor === "developer");

  const kept = groupNamed(entry, "Selected smells");
  eq("a smell row names the smell type", kept.items[0].primary, "Long Method");
  eq("with its entity and lines", kept.items[0].secondary, "processOrder · L10-60");
  eq("and its severity as a tag", kept.items[0].tag, "high");

  const dropped = groupNamed(entry, "Excluded smells");
  check("what was excluded is recorded too", dropped.items.length === 1);
  eq("and named", dropped.items[0].primary, "Feature Envy");
}

// ── 3. Planning: smell -> refactoring ────────────────────────────────────────
console.log("\nPlanning");
{
  const entry = narrate({
    id: 3, stage: "plan_approval", action: "plan_generated", actor: "system",
    details: {
      plan_id: "plan_7c2", source: "rdp_agent", total_steps: 2,
      recommended: 1, review: 1,
      refactorings: { "Extract Method": 1, "Move Method": 1 },
      by_file: [{ file: "src/OrderService.java", count: 2,
                  refactorings: { "Extract Method": 1, "Move Method": 1 } }],
      step_detail: [
        { step_id: 1, smell_type: "Long Method", refactoring: "Extract Method",
          file: "src/OrderService.java", entity: "OrderService.processOrder",
          lines: [10, 60], risk: "low", recommendation: "recommended" },
        { step_id: 2, smell_type: "Feature Envy", refactoring: "Move Method",
          file: "src/OrderService.java", risk: "medium", recommendation: "review" },
      ],
      step_detail_omitted: 3,
    },
  });

  check("the title counts the planned work",
        /2 refactoring steps/.test(entry.title), entry.title);
  eq("the plan id is a fact", factValue(entry, "Plan"), "plan_7c2");
  eq("the agent is named, not its code", factValue(entry, "Source"), "RDP agent");

  const steps = groupNamed(entry, "Steps: smell → refactoring");
  eq("the pairing is the headline of a step row",
     steps.items[0].primary, "Long Method → Extract Method");
  eq("with target and risk", steps.items[0].secondary,
     "OrderService.processOrder · L10-60 · risk low");
  eq("and the recommendation as a tag", steps.items[0].tag, "recommended");
  check("truncation is admitted, never hidden",
        entry.note === "3 further steps not listed.", entry.note);
}

// ── 4. Approval: what the developer authorised ───────────────────────────────
console.log("\nApproval");
{
  const entry = narrate({
    id: 4, stage: "plan_approval", action: "plan_approved", actor: "developer",
    details: {
      plan_id: "plan_7c2",
      approved: { count: 1, steps: [{ step_id: 1, smell_type: "Long Method",
                                      refactoring: "Extract Method",
                                      file: "src/OrderService.java" }] },
      rejected: { count: 1, steps: [{ step_id: 2, smell_type: "Feature Envy",
                                      refactoring: "Move Method",
                                      file: "src/Pay.java" }] },
      manual: { count: 1, steps: [{ step_id: 3, smell_type: "God Class",
                                    refactoring: "Extract Class",
                                    file: "src/Big.java" }] },
      overrides: { approved_not_recommended: 1, rejected_recommended: 0 },
    },
  });

  check("the title states the authorisation",
        /approved 1 step for transformation/.test(entry.title), entry.title);
  eq("an override against advice is surfaced",
     factValue(entry, "Approved against advice"), "1");
  // Zero is a recorded observation, not an absence.
  eq("a zero override count is still stated",
     factValue(entry, "Rejected despite advice"), "0");

  check("what goes to SCTVA is its own group",
        groupNamed(entry, "Sent to the Transformation agent").items.length === 1);
  check("manual work is listed separately",
        groupNamed(entry, "Kept for manual work").items[0].primary
        === "God Class → Extract Class");
  check("rejections are kept, not discarded",
        groupNamed(entry, "Rejected").items.length === 1);
}

// ── 5. Transformation outcome ────────────────────────────────────────────────
console.log("\nTransformation");
{
  const entry = narrate({
    id: 5, stage: "transformation", action: "transformation_completed", actor: "system",
    details: {
      status: "ok", passed: 1, failed: 1, plan_id: "plan_7c2",
      step_detail: [
        { step_id: 1, refactoring: "Extract Method",
          file: "src/OrderService.java", status: "passed" },
        { step_id: 2, refactoring: "Move Method", file: "src/Pay.java",
          status: "failed", message: "target class not found" },
      ],
      files_changed: ["src/OrderService.java"],
    },
  });

  check("the title carries the outcome", /1 passed, 1 failed/.test(entry.title), entry.title);
  check("a run with a failure is not tinted as success", entry.tone === "warn");

  const outcomes = groupNamed(entry, "Step outcomes");
  eq("the failing step is named", outcomes.items[1].primary, "Move Method");
  eq("with the reason it failed", outcomes.items[1].secondary, "target class not found");
  eq("and its status", outcomes.items[1].tag, "failed");
}

// ── 5b. The SCTVA entry does not restate the plan ────────────────────────────
console.log("\nTransformation agent entry");
{
  const entry = narrate({
    id: 9, stage: "transformation", action: "sctva_transformation_executed",
    actor: "system",
    details: {
      language: "java", actions: 14, executable: 12, noops: 2,
      files_sent: 3, files_missing: 0, success: true, rollback: false,
      file_detail: [
        { file: "src/OrderService.java", changed: true, success: true,
          replacements: 4, rolled_back: false },
      ],
    },
  });

  // The counts belong here; the itemised refactorings belong to plan_approved,
  // which already names them. Repeating them produced a block of rows reading
  // "action" over and over.
  eq("dispatched actions are a count, not a list",
     entry.groups.map((g) => g.label), ["Files transformed"]);
  eq("the counts are still stated", factValue(entry, "Actions"), "14");
  eq("including the no-ops", factValue(entry, "No-ops"), "2");
  eq("a transformed file carries real detail",
     groupNamed(entry, "Files transformed").items[0].secondary, "4 replacements");

  // The guard that would have caught the original bug: no group may render a
  // row whose only text is the renderer's own fallback word.
  const placeholders = entry.groups
    .flatMap((g) => g.items)
    .filter((item) => item.primary === "action" || !item.primary);
  eq("no placeholder rows survive", placeholders, []);
}

// ── 6. Nothing is invented ───────────────────────────────────────────────────
console.log("\nHonesty");
{
  const bare = narrate({
    id: 6, stage: "plan_approval", action: "plan_generated", actor: "system", details: {},
  });
  check("an empty detail block still renders a title", typeof bare.title === "string");
  eq("but lists no evidence", bare.groups, []);
  check("and is not offered as expandable when there is nothing to show",
        bare.facts.length === 0 ? bare.expandable === false : true);

  const unknown = narrate({
    id: 7, stage: "smell_review", action: "some_future_event", actor: "system", details: {},
  });
  eq("an unknown action is humanised rather than dropped",
     unknown.title, "Some future event");
  check("an unknown stage still gets a label", narrate({ stage: "weird" }).stageLabel === "Weird");

  check("a malformed row does not throw", typeof narrate(null).title === "string");
  eq("non-objects are filtered out of a trail", narrateAll([null, 5, undefined]).length, 0);

  // The empty-string / null guard: absent facts must not render as blanks.
  const sparse = narrate({
    id: 8, stage: "comparison", action: "refactoring_reverted",
    details: { file: "src/A.java", refactorings: [] },
  });
  eq("an empty list is not shown as a fact",
     sparse.facts.map((f) => f.label), ["File"]);
}

// ── 7. Helpers ───────────────────────────────────────────────────────────────
console.log("\nHelpers");
{
  eq("totals are ordered by frequency",
     describeTotals({ A: 1, B: 5, C: 3 }), "B ×5, C ×3, A");
  eq("a long tail is summarised, not truncated silently",
     describeTotals({ A: 9, B: 8, C: 7, D: 6, E: 5, F: 4 }),
     "A ×9, B ×8, C ×7, D ×6, +2 more");
  eq("nothing in, nothing out", describeTotals({}), "");
  eq("zero counts are not listed", describeTotals({ A: 0, B: 2 }), "B ×2");

  const grouped = groupByStage(narrateAll([
    { id: 1, stage: "smell_review", action: "workflow_started", details: {} },
    { id: 2, stage: "plan_approval", action: "plan_generated", details: {} },
    { id: 3, stage: "smell_review", action: "cuqa_report_ingested", details: {} },
  ]));
  eq("stages keep first-seen order", grouped.map((g) => g.stage),
     ["smell_review", "plan_approval"]);
  check("entries stay with their stage", grouped[0].entries.length === 2);
  eq("and are labelled", grouped[0].label, STAGE_LABEL.smell_review);
}

console.log(`\n${failures === 0 ? "All checks passed." : `${failures} check(s) FAILED.`}\n`);
process.exit(failures === 0 ? 0 : 1);
