/**
 * planningSelection.test.mjs
 * ==========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The Stage 2 claims that are about the SYSTEM rather than about the styling,
 * asserted directly:
 *
 *   - Select Recommended approves the green steps and nothing else.
 *   - It does not overturn a decision the developer already made.
 *   - Only an explicit approve reaches the Safe Transformation Agent.
 *   - A manual-only step is never forwarded by any default or bulk path.
 *   - Decisions survive a re-rank on step identity, not on step_id.
 *   - The frontend never assigns a category — it reads the backend's.
 *
 * The last one is why this file exists at all. Everything about the Decision
 * Support Score is owned by domain/planning_recommendation.py; the risk on this
 * side of the wire is not a wrong score but a right score being applied to the
 * wrong step, or being quietly ignored by a bulk button. Those are the failures
 * tested here.
 *
 * Dependency-free — planningSelection.js imports nothing, so this runs under
 * plain node with no bundler and no DOM:
 *
 *     npm run test:planning        (from frontend/)
 */

import {
  APPROVE, MANUAL, REJECT,
  actionModel, approveRecommendedIn, automaticSteps, carryDecisions,
  countDecisions, decideGroup, distributionDelta, forcedManualOnlySteps,
  groupBreakdown, isAutoSelectable, isOverride, manualSteps, planSummary,
  rejectAll, selectAll, selectRecommended, stepIdentity, summarizeSteps,
  activeBulkVerdict, approvalState, fileOf, groupStepsByFile,
  groupStepsByMethod, methodOf, methodOptions,
} from "./planningSelection.js";

let failures = 0;
const check = (label, condition, detail = "") => {
  if (!condition) failures += 1;
  console.log(`  [${condition ? "PASS" : "FAIL"}] ${label}${!condition && detail ? `  -- ${detail}` : ""}`);
};
const eq = (label, actual, expected) =>
  check(label, JSON.stringify(actual) === JSON.stringify(expected),
        `got ${JSON.stringify(actual)}, expected ${JSON.stringify(expected)}`);

/**
 * A plan step as the backend delivers it. `decision_support` is written by
 * domain/planning_recommendation.py; the fixtures below only ever COPY the
 * fields it produces — no test computes a category or an eligibility flag,
 * because neither does the code under test.
 */
const step = (id, category, extra = {}) => ({
  step_id: id,
  smell_id: extra.smell_id ?? `s${id}`,
  refactoring: extra.refactoring ?? "Extract Method",
  smell_type: extra.smell_type ?? "Long Method",
  risk: extra.risk ?? "low",
  impact: extra.impact ?? "high",
  target: { file: extra.file ?? "OrderService.java" },
  decision_support: category === null ? undefined : {
    category,
    label: category,
    score: extra.score ?? 87,
    // The backend sets this for the recommended category and nothing else.
    auto_select_eligible: category === "recommended",
    impact: {
      quality_gain_points: extra.gain ?? 6.3,
      potential_gain_points: extra.potential ?? 5.8,
      effort_minutes: extra.minutes ?? 6,
      blast_radius_files: 1,
      risk_band: extra.riskBand ?? "low",
      validation: ["behavioural"],
      has_record: true,
    },
    deferral: { carried_points: 6.3, change_pressure: "high", churn_known: true },
    developer_strategy: extra.strategy ?? "balanced",
  },
});

const PLAN = [
  step(1, "recommended", { file: "OrderService.java", smell_id: "s1" }),
  step(2, "recommended", { file: "OrderService.java", smell_id: "s2", refactoring: "Extract Class" }),
  step(3, "review", { file: "OrderService.java", smell_id: "s3", riskBand: "high" }),
  step(4, "not_recommended", { file: "PaymentGateway.java", smell_id: "s4" }),
  step(5, "manual_only", { file: "PaymentGateway.java", smell_id: "s5", refactoring: "Move Method" }),
  step(6, null, { file: "LegacyUtil.java", smell_id: "s6" }),
];

console.log("\nStage 2 decision semantics\n");

// ── 1. Recommended ───────────────────────────────────────────────────────────
console.log("Select Recommended");
{
  const decisions = selectRecommended(PLAN, {});
  eq("approves only the auto-selectable steps", decisions, { 1: APPROVE, 2: APPROVE });
  check("leaves the review step pending", decisions[3] === undefined);
  check("leaves the not-recommended step pending", decisions[4] === undefined);
  check("leaves the manual-only step pending", decisions[5] === undefined);
  check("leaves the unassessed step pending", decisions[6] === undefined);

  // The flag is the backend's, and absence means no.
  check("eligibility is read, never inferred",
        isAutoSelectable(PLAN[0]) === true && isAutoSelectable(PLAN[2]) === false
        && isAutoSelectable(PLAN[5]) === false);

  // A high-scoring step the backend did NOT mark eligible stays untouched,
  // whatever its score says — the frontend must not re-derive the threshold.
  const highScoreNotEligible = step(9, "review", { score: 99 });
  eq("a 99-scored review step is still not selected",
     selectRecommended([highScoreNotEligible], {}), {});
}

// ── 2. It does not overturn the developer ────────────────────────────────────
console.log("\nSelect Recommended vs. an existing decision");
{
  const existing = { 1: REJECT };
  const decisions = selectRecommended(PLAN, existing);
  check("a deliberately rejected green step stays rejected", decisions[1] === REJECT);
  check("the other green step is still approved", decisions[2] === APPROVE);
  eq("nothing else is touched", Object.keys(decisions).sort(), ["1", "2"]);

  const manualKept = selectRecommended(PLAN, { 5: MANUAL });
  check("a step already marked manual stays manual", manualKept[5] === MANUAL);
}

// ── 3. What reaches SCTVA ────────────────────────────────────────────────────
console.log("\nWhat is forwarded to the Transformation Agent");
{
  const decisions = { 1: APPROVE, 2: REJECT, 3: APPROVE, 4: REJECT, 5: MANUAL };
  eq("only explicit approvals are forwarded",
     automaticSteps(PLAN, decisions).map((s) => s.step_id), [1, 3]);
  eq("manual work is not forwarded",
     manualSteps(PLAN, decisions).map((s) => s.step_id), [5]);
  check("a pending step is not forwarded",
        automaticSteps(PLAN, decisions).every((s) => s.step_id !== 6));
  eq("counts add up to the plan",
     countDecisions(PLAN, decisions),
     { approved: 2, rejected: 2, manual: 1, pending: 1 });
}

// ── 4. Manual-only is never forwarded by a bulk path ─────────────────────────
console.log("\nManual-only steps");
{
  const all = selectAll(PLAN, {});
  check("Select All marks the manual-only step as manual, not approved", all[5] === MANUAL);
  check("Select All approves the not-recommended step (as it says it does)", all[4] === APPROVE);
  eq("Select All forwards no manual-only step",
     forcedManualOnlySteps(PLAN, all).map((s) => s.step_id), []);
  eq("Select Recommended forwards no manual-only step",
     forcedManualOnlySteps(PLAN, selectRecommended(PLAN, {})).map((s) => s.step_id), []);
  eq("a file-level approve-recommended forwards no manual-only step",
     forcedManualOnlySteps(PLAN, approveRecommendedIn(PLAN, {})).map((s) => s.step_id), []);

  // The deliberate override is still possible — it is the developer's call,
  // and it is the one path that produces a forced forward.
  eq("an explicit override does forward it",
     forcedManualOnlySteps(PLAN, { 5: APPROVE }).map((s) => s.step_id), [5]);

  const actions = actionModel("manual_only");
  check("its primary action is manual work, not approve", actions.primary === MANUAL);
  check("approving it asks for confirmation", actions.confirmApprove === true);
  check("a recommended step needs no confirmation",
        actionModel("recommended").confirmApprove === false);
  check("a not-recommended step leads with reject",
        actionModel("not_recommended").primary === REJECT);
  check("a review step leads with review", actionModel("review").primary === "explain");
}

// ── 4b. Reject All ───────────────────────────────────────────────────────────
console.log("\nReject All");
{
  const all = rejectAll(PLAN, {});
  eq("every step is rejected",
     Object.values(all), PLAN.map(() => REJECT));
  eq("nothing is left forwardable",
     automaticSteps(PLAN, all).map((s) => s.step_id), []);
  check("not even the manual-only step is left marked manual", all[5] === REJECT);

  // It overwrites deliberately — the header asks for a second click first.
  const over = rejectAll(PLAN, { 1: APPROVE, 5: MANUAL });
  check("an existing approval is overwritten", over[1] === REJECT);
  check("an existing manual mark is overwritten", over[5] === REJECT);

  eq("an empty plan is a no-op, not a crash", rejectAll([], {}), {});

  // A fully rejected plan cannot proceed, which is the real safeguard: the
  // page requires at least one approved step before Forward is enabled.
  const counts = countDecisions(PLAN, all);
  check("no approved step remains", counts.approved === 0);
  check("nothing is left pending either", counts.pending === 0);
}

// ── 5. Overrides ─────────────────────────────────────────────────────────────
console.log("\nOverride detection");
{
  check("approving a not-recommended step is an override", isOverride(PLAN[3], APPROVE));
  check("forcing a manual-only step is an override", isOverride(PLAN[4], APPROVE));
  check("rejecting a recommended step is an override", isOverride(PLAN[0], REJECT));
  check("marking a manual-only step manual is NOT an override",
        isOverride(PLAN[4], MANUAL) === false);
  check("approving a recommended step is not an override",
        isOverride(PLAN[0], APPROVE) === false);
  check("rejecting a review step is not an override", isOverride(PLAN[2], REJECT) === false);
  check("an unassessed step can never be an override",
        isOverride(PLAN[5], APPROVE) === false);
}

// ── 6. Decisions survive a re-rank ───────────────────────────────────────────
console.log("\nRe-ranking (the only thing that replaces the plan)");
{
  // What the preference re-ranker does: drop a step, re-sort, renumber from 1.
  const reranked = [
    step(1, "review", { file: "OrderService.java", smell_id: "s3", riskBand: "high" }),
    step(2, "recommended", { file: "OrderService.java", smell_id: "s1" }),
    step(3, "manual_only", { file: "PaymentGateway.java", smell_id: "s5", refactoring: "Move Method" }),
  ];

  const before = { 1: APPROVE, 3: REJECT, 5: MANUAL };
  const reasons = { 3: "Too risky" };
  const carried = carryDecisions(PLAN, reranked, before, reasons);

  check("the approval follows its refactoring, not its number",
        carried.decisions[2] === APPROVE);
  check("the rejection follows its refactoring", carried.decisions[1] === REJECT);
  check("the manual mark follows its refactoring", carried.decisions[3] === MANUAL);
  check("a decision on a dropped step is discarded",
        Object.keys(carried.decisions).length === 3);
  eq("the override reason follows the same step", carried.extras, { 1: "Too risky" });

  // The bug this replaced: keying on step_id alone put "1: approve" onto
  // whatever refactoring happened to be first in the new plan.
  check("identity is not the step number",
        stepIdentity(PLAN[0]) === "s1|Extract Method|OrderService.java");
  check("identity distinguishes two steps on the same file",
        stepIdentity(PLAN[0]) !== stepIdentity(PLAN[1]));
}

// ── 7. Group actions ─────────────────────────────────────────────────────────
console.log("\nGroup actions");
{
  const file = PLAN.filter((s) => s.target.file === "OrderService.java");
  const approvedAll = decideGroup(file, APPROVE, {});
  eq("a file verdict applies to every step in it", approvedAll, { 1: APPROVE, 2: APPROVE, 3: APPROVE });
  eq("re-applying the same verdict clears it", decideGroup(file, APPROVE, approvedAll), {});

  const scoped = approveRecommendedIn(file, {});
  eq("approve-recommended is scoped to the group", scoped, { 1: APPROVE, 2: APPROVE });

  const breakdown = groupBreakdown(file);
  check("a mixed file is reported as mixed", breakdown.mixed === true);
  check("it counts the auto-selectable steps", breakdown.autoSelectable === 2);
  check("a uniform file is not mixed", groupBreakdown([PLAN[0], PLAN[1]]).mixed === false);
  check("an unassessed step is counted, not guessed into a category",
        groupBreakdown([PLAN[5]]).unassessed === 1);
}

// ── 8. Summary fallback ──────────────────────────────────────────────────────
console.log("\nPlan summary");
{
  const backendSummary = { recommended: 99, developer_strategy: "balanced", source: "backend" };
  eq("the backend's own summary is used verbatim",
     planSummary({ decision_support_summary: backendSummary, steps: PLAN }, "balanced"),
     backendSummary);

  const fallback = summarizeSteps(PLAN, "balanced");
  check("the fallback counts categories the backend assigned",
        fallback.recommended === 2 && fallback.review === 1
        && fallback.not_recommended === 1 && fallback.manual_only === 1);
  check("an unassessed step is unclassified, not folded into a category",
        fallback.unclassified === 1);
  check("gain and effort describe the recommended set only",
        fallback.projected_quality_gain === 12.6 && fallback.estimated_review_minutes === 12);
  check("the highest risk anywhere in the plan is reported",
        fallback.max_risk === "high");
  check("the fallback is labelled as one", fallback.source === "frontend_fallback");

  // "could not be computed" and "is worth nothing" are different answers.
  const noImpact = [{ step_id: 1, decision_support: { category: "review", impact: {} } }];
  check("an absent gain stays null rather than becoming 0",
        summarizeSteps(noImpact).projected_quality_gain === null);
}

// ── 9. Strategy consequence ──────────────────────────────────────────────────
console.log("\nStrategy consequence");
{
  const balanced = { developer_strategy: "balanced", recommended: 6, review: 3, not_recommended: 1, manual_only: 2 };
  const safety = { developer_strategy: "safety_first", recommended: 4, review: 5, not_recommended: 1, manual_only: 2 };

  const delta = distributionDelta(balanced, safety);
  check("the shift is reported", delta.moved === true);
  eq("per-category movement is exact",
     delta.changes.map((c) => c.delta), [-2, 2, 0, 0]);

  check("no baseline means no comparison", distributionDelta(null, safety) === null);
  check("the same strategy is not compared with itself",
        distributionDelta(balanced, { ...balanced }) === null);
  check("an unchanged distribution is reported as unchanged",
        distributionDelta(balanced, { ...balanced, developer_strategy: "max_improvement" }).moved === false);
}

// ── Grouping the plan ────────────────────────────────────────────────────────
console.log("\nGrouping the plan");
{
  const step = (id, refactoring, file, impact) => ({
    step_id: id, refactoring, impact, target: { file },
  });

  // Two methods, three files, deliberately interleaved so first-seen order and
  // sorted-by-size order differ.
  const plan = [
    step(1, "Extract Method", "src/Order.java", "high"),
    step(2, "Rename Variable", "src/Order.java", "low"),
    step(3, "Extract Method", "src/Cart.java", "medium"),
    step(4, "Extract Method", "src/Pay.java", "low"),
    step(5, "Rename Variable", "src/Cart.java", "low"),
  ];

  eq("a step's file is read from its target", fileOf(plan[0]), "src/Order.java");
  eq("a step with no target is module level", fileOf({ step_id: 9 }), "(module level)");
  eq("a step's method is its refactoring", methodOf(plan[0]), "Extract Method");
  eq("a step with no refactoring is not blank", methodOf({ step_id: 9 }), "(unspecified)");

  const byFile = groupStepsByFile(plan);
  eq("one group per file", byFile.length, 3);
  eq("in the order the plan sequenced them",
     byFile.map((g) => g.key), ["src/Order.java", "src/Cart.java", "src/Pay.java"]);
  eq("each carries its steps", byFile[0].stepCount, 2);
  eq("and how many methods it needs", byFile[0].methodCount, 2);

  const byMethod = groupStepsByMethod(plan);
  eq("one group per method", byMethod.length, 2);
  eq("in plan order, not alphabetical",
     byMethod.map((g) => g.key), ["Extract Method", "Rename Variable"]);
  eq("Extract Method holds three steps", byMethod[0].stepCount, 3);
  eq("across three files", byMethod[0].fileCount, 3);
  eq("Rename Variable holds two", byMethod[1].stepCount, 2);
  eq("across two files", byMethod[1].fileCount, 2);
  eq("the worst impact in the group leads it", byMethod[0].worstImpact, "high");
  eq("and a group of low steps says low", byMethod[1].worstImpact, "low");

  // Approval state — only APPROVE counts as selected.
  const none = approvalState(plan, {});
  check("nothing approved is none", none.none && !none.all && !none.partial);
  eq("but the total is still known", none.total, 5);

  const some = approvalState(plan, { 1: APPROVE, 2: REJECT, 3: MANUAL });
  eq("only approvals count as selected", some.selected, 1);
  check("which is a partial selection", some.partial && !some.all && !some.none);

  const every = approvalState(plan, { 1: APPROVE, 2: APPROVE, 3: APPROVE, 4: APPROVE, 5: APPROVE });
  check("all approved is all", every.all && !every.partial && !every.none);

  eq("an empty group is neither all nor partial",
     [approvalState([], {}).all, approvalState([], {}).partial], [false, false]);

  // Dropdown options.
  const options = methodOptions(plan, { 1: APPROVE, 3: APPROVE, 4: APPROVE });
  eq("one option per method", options.length, 2);
  eq("largest first, so the bulk decision is at the top",
     options.map((o) => o.key), ["Extract Method", "Rename Variable"]);
  eq("counts are named for what the shared dropdown renders",
     [options[0].findingCount, options[0].fileCount], [3, 3]);
  check("a fully approved method reads as fully selected", options[0].selection.all);
  check("an untouched one reads as empty", options[1].selection.none);
  check("the option carries its steps, for the bulk verdict",
        options[0].steps.length === 3);

  // Degenerate input must not throw — the plan can be empty or malformed.
  eq("no steps, no groups", groupStepsByMethod([]).length, 0);
  eq("null is not a crash", groupStepsByFile(null).length, 0);
  eq("and neither is a null option list", methodOptions(null).length, 0);
  eq("a step with neither file nor method still groups",
     groupStepsByMethod([{ step_id: 1 }])[0].key, "(unspecified)");
}

// ── Which bulk verdict is in force ───────────────────────────────────────────
// The bar highlights one of these buttons, so a wrong answer here is a page
// telling the developer they did something they did not do.
console.log("\nActive bulk verdict");
{
  const mk = (id, auto) => ({
    step_id: id,
    decision_support: { category: auto ? "recommended" : "review", auto_select_eligible: auto },
  });
  const plan = [mk(1, true), mk(2, true), mk(3, false), mk(4, false)];

  eq("nothing decided is no verdict", activeBulkVerdict(plan, {}), null);
  eq("every step approved is 'all'",
     activeBulkVerdict(plan, { 1: APPROVE, 2: APPROVE, 3: APPROVE, 4: APPROVE }), "all");
  eq("every step rejected is 'reject'",
     activeBulkVerdict(plan, { 1: REJECT, 2: REJECT, 3: REJECT, 4: REJECT }), "reject");
  eq("exactly the recommended set is 'recommended'",
     activeBulkVerdict(plan, { 1: APPROVE, 2: APPROVE }), "recommended");

  // The strictness that matters: one extra approval is no longer "recommended".
  eq("one hand-picked extra drops the claim",
     activeBulkVerdict(plan, { 1: APPROVE, 2: APPROVE, 3: APPROVE }), null);
  eq("approving only half the recommended set is not it either",
     activeBulkVerdict(plan, { 1: APPROVE }), null);
  eq("a rejection alongside it drops the claim",
     activeBulkVerdict(plan, { 1: APPROVE, 2: APPROVE, 3: REJECT }), null);
  eq("and so does a manual mark",
     activeBulkVerdict(plan, { 1: APPROVE, 2: APPROVE, 3: MANUAL }), null);
  eq("approving the wrong two is not the recommendation",
     activeBulkVerdict(plan, { 3: APPROVE, 4: APPROVE }), null);

  // A plan whose recommended set is everything reports the stronger fact.
  const allGreen = [mk(1, true), mk(2, true)];
  eq("all-approved wins over recommended when they coincide",
     activeBulkVerdict(allGreen, { 1: APPROVE, 2: APPROVE }), "all");

  // A plan DIWO recommends nothing in can never read "recommended".
  const noGreen = [mk(1, false), mk(2, false)];
  eq("no recommended steps, no recommended verdict",
     activeBulkVerdict(noGreen, { 1: APPROVE }), null);

  eq("an empty plan has no verdict", activeBulkVerdict([], {}), null);
  eq("null is not a crash", activeBulkVerdict(null, {}), null);
  eq("and neither are absent decisions", activeBulkVerdict(plan), null);
}

console.log(`\n${failures === 0 ? "All checks passed." : `${failures} check(s) FAILED.`}\n`);
process.exit(failures === 0 ? 0 : 1);
