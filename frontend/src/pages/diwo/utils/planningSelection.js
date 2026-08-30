/**
 * Stage 2 decision semantics
 * ==========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The rules that decide what the developer's clicks MEAN — which steps a bulk
 * action touches, which decisions survive a re-rank, and, most importantly,
 * which steps are forwarded to the Safe Transformation Agent as automatic
 * transformations and which are not.
 *
 * Split out of planningDecisionSupport.js for one reason: that module imports
 * the theme, which is JSX, so nothing in it can be exercised by a plain `node`
 * test. These rules are the ones worth testing — "a manual-only step is never
 * forwarded as an automatic transformation" is a claim about the system, not
 * about a stylesheet — so they live in a file with no imports at all.
 * planningDecisionSupport.js re-exports everything here, so callers still see
 * one module.
 *
 * NOTHING HERE SCORES ANYTHING. The category, the Decision Support Score and
 * `auto_select_eligible` are computed by the DIWO backend
 * (domain/planning_recommendation.py) and arrive on the step. This module
 * reads those fields; it never derives them.
 */

// ─── Categories, as the backend names them ───────────────────────────────────

export const RECOMMENDED = "recommended";
export const REVIEW = "review";
export const NOT_RECOMMENDED = "not_recommended";
export const MANUAL_ONLY = "manual_only";

/** Display order: best first, so a summary row reads top-down. */
export const CATEGORY_ORDER = [RECOMMENDED, REVIEW, NOT_RECOMMENDED, MANUAL_ONLY];

// ─── Decision states ─────────────────────────────────────────────────────────

/** Approved for automatic transformation. The ONLY state SCTVA ever receives. */
export const APPROVE = "approve";

/** Dropped from the plan. */
export const REJECT = "reject";

/**
 * Marked for the developer to do by hand.
 *
 * A third state exists because "approve or reject?" is not the question a
 * manual-only step asks. SCTVA has no safe automatic form for that
 * refactoring, so "approve" would forward a step that cannot execute, and
 * "reject" would throw away a refactoring DIWO thinks is worth doing. `manual`
 * says the developer accepted the advice and will do it themselves: it counts
 * as decided, it is recorded, and it is never forwarded.
 *
 * The backend needs no change to understand it. build_approved_plan() forwards
 * `approve` and drops `reject`; anything else lands in `pending_step_ids` and
 * is never mapped to an SCTVA action.
 */
export const MANUAL = "manual";

export const DECIDED = [APPROVE, REJECT, MANUAL];

// ─── Reading the backend's assessment ────────────────────────────────────────

/** `step.decision_support`, or null when the backend did not assess this step. */
export const supportOf = (step) =>
  step && typeof step.decision_support === "object" && step.decision_support !== null
    ? step.decision_support
    : null;

export const categoryOf = (step) => supportOf(step)?.category || null;

/** Only the backend may mark a step auto-selectable. Absence means "no". */
export const isAutoSelectable = (step) => supportOf(step)?.auto_select_eligible === true;

/**
 * A step's identity across plan revisions.
 *
 * step_id cannot be used: the preference re-ranker drops rejected steps and
 * renumbers the rest from 1, so step 1 of a new plan is usually a different
 * refactoring than step 1 of the old one. Same triple as the backend's
 * domain/planning_recommendation.py::step_identity, so a decision made here
 * and a feedback row written there describe the same thing.
 */
export const stepIdentity = (step) =>
  `${step?.smell_id ?? ""}|${step?.refactoring ?? ""}|${step?.target?.file ?? ""}`;

// ─── Overrides ───────────────────────────────────────────────────────────────

/**
 * The reasons offered when a developer overrides a recommendation.
 *
 * Optional everywhere — the workflow is never blocked on one. They exist as
 * chips rather than a text box because a disagreement with DIWO is the most
 * informative feedback the system can collect (it is the only signal saying a
 * recommendation was wrong rather than merely unseen), and a free-text box
 * collects it least often. "I disagree with the recommendation" is a
 * first-class answer, not a fallback.
 */
export const OVERRIDE_REASONS = [
  "Too risky",
  "Not useful",
  "Wrong refactoring",
  "Too much scope",
  "Project requirement",
  "Prefer manual change",
  "I disagree with the recommendation",
  "Other",
];

/**
 * True when the developer's verdict goes against what DIWO advised.
 *
 * Marking a manual-only step as manual work is NOT an override — it is the
 * recommendation being followed. Only forcing it through as an automatic
 * transformation is.
 */
export function isOverride(step, decision) {
  const category = categoryOf(step);
  if (!category || !decision) return false;
  if (decision === APPROVE) {
    return category === NOT_RECOMMENDED || category === MANUAL_ONLY;
  }
  if (decision === REJECT) return category === RECOMMENDED;
  return false;
}

// ─── What SCTVA receives ─────────────────────────────────────────────────────

/**
 * The steps that leave Stage 2 as automatic transformations.
 *
 * One rule, stated once: an explicit `approve`, and nothing else. A step
 * marked for manual work, rejected, or left pending is not in this list — so
 * "what did the developer actually authorise SCTVA to do?" has a single
 * answer, which the submit handler and the tests both read from here rather
 * than each re-deriving.
 */
export const automaticSteps = (steps, decisions = {}) =>
  (steps || []).filter((step) => decisions[step.step_id] === APPROVE);

/** Steps the developer took on themselves. Recorded, never transformed. */
export const manualSteps = (steps, decisions = {}) =>
  (steps || []).filter((step) => decisions[step.step_id] === MANUAL);

/** Steps forwarded despite DIWO saying SCTVA cannot execute them. */
export const forcedManualOnlySteps = (steps, decisions = {}) =>
  automaticSteps(steps, decisions).filter((step) => categoryOf(step) === MANUAL_ONLY);

/** Approved / rejected / manual / pending counts over a step list. */
export function countDecisions(steps, decisions = {}) {
  const counts = { approved: 0, rejected: 0, manual: 0, pending: 0 };
  (steps || []).forEach((step) => {
    const verdict = decisions[step.step_id];
    if (verdict === APPROVE) counts.approved += 1;
    else if (verdict === REJECT) counts.rejected += 1;
    else if (verdict === MANUAL) counts.manual += 1;
    else counts.pending += 1;
  });
  return counts;
}

// ─── Bulk actions ────────────────────────────────────────────────────────────

/**
 * "Select Recommended": approve the steps the backend marked
 * `auto_select_eligible`, and only those.
 *
 * A step the developer has ALREADY decided is left exactly as it is. A bulk
 * convenience that silently re-approves a green step they deliberately
 * rejected would overturn a decision made on purpose, and they would have no
 * way of noticing. Clearing decisions is a separate, explicit action.
 *
 * LOCAL STATE ONLY. Returns a new decision map; it calls nothing.
 */
export function selectRecommended(steps, decisions = {}) {
  const next = { ...decisions };
  (steps || []).forEach((step) => {
    if (!isAutoSelectable(step)) return;
    if (next[step.step_id]) return; // never overturn an existing decision
    next[step.step_id] = APPROVE;
  });
  return next;
}

/**
 * "Select All": decide every step at once.
 *
 * Manual-only steps are marked as manual work rather than approved. Approving
 * one would forward a step SCTVA has no automatic form for — the bulk button
 * quietly producing the exact failure the manual-only category exists to
 * prevent. Everything else is approved, warnings and all: that is what the
 * button says it does, and the confirmation spells out what it is including.
 */
export function selectAll(steps, decisions = {}) {
  const next = { ...decisions };
  (steps || []).forEach((step) => {
    next[step.step_id] = categoryOf(step) === MANUAL_ONLY ? MANUAL : APPROVE;
  });
  return next;
}

/**
 * "Reject All": drop every step in the plan.
 *
 * The counterpart to Select All, and the honest way out of a plan the
 * developer does not want. Without it, rejecting twelve steps meant twelve
 * clicks — or, worse, using the file-level "All ✕" once per file and hoping
 * none were missed. There was already a reject-all at the file and category
 * level; its absence at the plan level was an asymmetry, not a safeguard.
 *
 * Every step takes REJECT, including ones already approved: the caller
 * confirms first, and a bulk action that quietly skipped the approved steps
 * would leave a plan that is neither fully rejected nor obviously not.
 *
 * Note that this cannot start a transformation — a plan with nothing approved
 * cannot be forwarded at all. The destructive direction is Select All, and
 * that is the one the UI makes you confirm hardest.
 */
export function rejectAll(steps, decisions = {}) {
  const next = { ...decisions };
  (steps || []).forEach((step) => {
    next[step.step_id] = REJECT;
  });
  return next;
}

/**
 * One verdict for every step in a group (a file, or a category section).
 * Re-applying the verdict a group already carries clears it back to pending,
 * so the group header doubles as an undo.
 */
export function decideGroup(groupSteps, verdict, decisions = {}) {
  const list = groupSteps || [];
  const alreadyAll = list.length > 0 && list.every((s) => decisions[s.step_id] === verdict);
  const next = { ...decisions };
  list.forEach((step) => {
    if (alreadyAll) delete next[step.step_id];
    else next[step.step_id] = verdict;
  });
  return next;
}

/**
 * A group's "Approve Recommended": the same rule as the plan-level button,
 * scoped to one file. Preferred over approving a file wholesale, because a
 * file rarely holds four equally safe steps.
 */
export const approveRecommendedIn = (groupSteps, decisions = {}) =>
  selectRecommended(groupSteps, decisions);

// ─── Surviving a re-rank ─────────────────────────────────────────────────────

/**
 * Carry decisions from the old plan to the new one by step identity.
 *
 * Changing the developer goal re-ranks the plan, which renumbers step_id. A
 * decision map keyed by step_id is meaningless the moment the new plan
 * arrives — "3: reject" would land on whatever refactoring is now third.
 * Re-keying by identity puts each verdict back on the step it was actually
 * made on, and drops the verdicts whose steps are gone.
 *
 * `extras` carries anything keyed the same way (override reasons) through the
 * same remapping, so a reason cannot end up attached to a different step than
 * the decision it explains.
 */
export function carryDecisions(prevSteps, nextSteps, decisions = {}, extras = {}) {
  const byIdentity = new Map();
  const extrasByIdentity = new Map();

  (prevSteps || []).forEach((step) => {
    const identity = stepIdentity(step);
    if (decisions[step.step_id]) byIdentity.set(identity, decisions[step.step_id]);
    if (extras[step.step_id]) extrasByIdentity.set(identity, extras[step.step_id]);
  });

  const carried = {};
  const carriedExtras = {};
  (nextSteps || []).forEach((step) => {
    const identity = stepIdentity(step);
    if (byIdentity.has(identity)) carried[step.step_id] = byIdentity.get(identity);
    if (extrasByIdentity.has(identity)) carriedExtras[step.step_id] = extrasByIdentity.get(identity);
  });

  return { decisions: carried, extras: carriedExtras };
}

// ─── Per-category action model ───────────────────────────────────────────────

/**
 * Which buttons a card offers, and which one leads.
 *
 * The four categories are not four shades of one question. A green step asks
 * "approve?"; a yellow one asks "have you read this?"; a red one asks "are you
 * sure?"; a blue one is not an automation question at all. Giving all four an
 * identical [Approve][Reject] pair is what made the recommendation
 * decorative — the developer's cheapest action was the same whatever DIWO
 * said.
 *
 * `confirmApprove` marks the categories where approving for automatic
 * transformation opens a confirmation first. It is never a block: the
 * developer may always override, and that authority stays theirs.
 */
export const ACTION_MODEL = {
  [RECOMMENDED]: {
    order: [APPROVE, REJECT],
    primary: APPROVE,
    approveLabel: "✓ Approve",
    rejectLabel: "✕ Reject",
    confirmApprove: false,
  },
  [REVIEW]: {
    // Review leads. Approving a yellow step without opening it is the habit
    // this stage exists to break, so the cheapest click is "read it".
    order: ["explain", APPROVE, REJECT],
    primary: "explain",
    explainLabel: "Review details",
    approveLabel: "Approve",
    rejectLabel: "Reject",
    confirmApprove: false,
  },
  [NOT_RECOMMENDED]: {
    // Reject leads and approval is demoted — an equal pair would present "do
    // the thing DIWO advised against" as an equally weighted option.
    order: [REJECT, APPROVE],
    primary: REJECT,
    approveLabel: "Approve anyway",
    rejectLabel: "✕ Reject",
    confirmApprove: true,
  },
  [MANUAL_ONLY]: {
    // Not an approve/reject question: SCTVA cannot execute this refactoring,
    // so the honest choice is "I will do it" or "leave it".
    order: [MANUAL, REJECT, APPROVE],
    primary: MANUAL,
    manualLabel: "Add to manual work",
    rejectLabel: "Skip",
    approveLabel: "Force automatic",
    confirmApprove: true,
  },
};

const DEFAULT_ACTIONS = {
  order: [APPROVE, REJECT],
  primary: APPROVE,
  approveLabel: "✓ Approve",
  rejectLabel: "✕ Reject",
  confirmApprove: false,
};

/** The action layout for a category, or the plain pair for an unassessed step. */
export const actionModel = (category) => ACTION_MODEL[category] || DEFAULT_ACTIONS;

// ─── Plan-level summary (fallback only) ──────────────────────────────────────

/**
 * Fallback plan-level summary, for a plan that arrived without one.
 *
 * Counts only. The categories were assigned by the backend; a step with no
 * `decision_support` is counted as `unclassified` rather than being guessed
 * into one of the four, because a plan half of which was never assessed must
 * not read as a plan that was.
 */
export function summarizeSteps(steps, developerStrategy = "balanced") {
  const list = steps || [];
  const counts = { [RECOMMENDED]: 0, [REVIEW]: 0, [NOT_RECOMMENDED]: 0, [MANUAL_ONLY]: 0 };
  const riskRank = { low: 0, medium: 1, high: 2 };

  let unclassified = 0;
  let autoSelectable = 0;
  let projectedGain = 0;
  let gainSeen = false;
  let reviewMinutes = 0;
  let minutesSeen = false;
  let totalMinutes = 0;
  let totalMinutesSeen = false;
  let maxRisk = null;

  list.forEach((step) => {
    const support = supportOf(step);
    if (!support) {
      unclassified += 1;
      return;
    }
    if (support.category in counts) counts[support.category] += 1;

    const impact = support.impact || {};
    const minutes = typeof impact.effort_minutes === "number" ? impact.effort_minutes : null;

    // Gain and effort describe the same set — the steps Select Recommended
    // would tick — so the two numbers in the header can be read together.
    if (support.auto_select_eligible) {
      autoSelectable += 1;
      if (typeof impact.quality_gain_points === "number") {
        projectedGain += impact.quality_gain_points;
        gainSeen = true;
      }
      if (minutes !== null) {
        reviewMinutes += minutes;
        minutesSeen = true;
      }
    }

    if (minutes !== null) {
      totalMinutes += minutes;
      totalMinutesSeen = true;
    }

    const band = impact.risk_band;
    if (band in riskRank && (maxRisk === null || riskRank[band] > riskRank[maxRisk])) {
      maxRisk = band;
    }
  });

  return {
    ...counts,
    unclassified,
    total_steps: list.length,
    auto_selectable: autoSelectable,
    projected_quality_gain: gainSeen ? Math.round(projectedGain * 100) / 100 : null,
    estimated_review_minutes: minutesSeen ? reviewMinutes : null,
    total_review_minutes: totalMinutesSeen ? totalMinutes : null,
    max_risk: maxRisk,
    developer_strategy: developerStrategy,
    source: "frontend_fallback",
  };
}

/** The plan's own summary when it has one, otherwise the counted fallback. */
export function planSummary(plan, developerStrategy) {
  const provided = plan?.decision_support_summary;
  if (provided && typeof provided === "object") return provided;
  return summarizeSteps(plan?.steps, developerStrategy);
}

/**
 * How a goal change moved the recommendation distribution.
 *
 * The developer goal is only meaningful if its effect is visible: "Safety
 * First moved two steps out of Recommended and into Review" is the sentence
 * that makes the control worth touching.
 *
 * Returns null when there is nothing honest to compare — no previous summary,
 * or a summary for the same strategy. A comparison against an absent baseline
 * would be a fabricated one, so the UI simply shows nothing.
 */
export function distributionDelta(previous, current) {
  if (!previous || !current) return null;
  if (!previous.developer_strategy || !current.developer_strategy) return null;
  if (previous.developer_strategy === current.developer_strategy) return null;

  const changes = CATEGORY_ORDER.map((category) => ({
    category,
    from: previous[category] ?? 0,
    to: current[category] ?? 0,
    delta: (current[category] ?? 0) - (previous[category] ?? 0),
  }));

  return {
    from: previous.developer_strategy,
    to: current.developer_strategy,
    changes,
    moved: changes.some((change) => change.delta !== 0),
  };
}

// ─── Per-group breakdown ─────────────────────────────────────────────────────

/**
 * A group's category mix, so a group-level "approve all" says what it is about
 * to approve. A file holding 2 recommended, 1 review and 1 manual-only must
 * not present itself as four equally safe steps.
 */
export function groupBreakdown(steps) {
  const counts = { [RECOMMENDED]: 0, [REVIEW]: 0, [NOT_RECOMMENDED]: 0, [MANUAL_ONLY]: 0 };
  let unassessed = 0;

  (steps || []).forEach((step) => {
    const category = categoryOf(step);
    if (category && category in counts) counts[category] += 1;
    else unassessed += 1;
  });

  return {
    counts,
    unassessed,
    /** True when the group's steps do not all carry the same recommendation. */
    mixed: CATEGORY_ORDER.filter((c) => counts[c] > 0).length + (unassessed ? 1 : 0) > 1,
    nonGreen: counts[REVIEW] + counts[NOT_RECOMMENDED] + counts[MANUAL_ONLY] + unassessed,
    autoSelectable: (steps || []).filter(isAutoSelectable).length,
  };
}

// ─── Formatting ──────────────────────────────────────────────────────────────

/** "+6.3" / "—". Never invents a value for a figure the backend left null. */
export const formatPoints = (value, { signed = true } = {}) => {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  const rounded = Math.round(value * 10) / 10;
  return `${signed && rounded > 0 ? "+" : ""}${rounded}`;
};

export const formatMinutes = (value) =>
  typeof value === "number" && !Number.isNaN(value) ? `~${value} min` : "—";

// ─── Grouping the plan ───────────────────────────────────────────────────────

/** The file a step targets. One name for it, used by every grouping below. */
export const fileOf = (step) => step?.target?.file || "(module level)";

/**
 * The refactoring method a step applies — "Extract Method", "Rename Variable".
 *
 * This is the PLAN METHOD: what RDP decided to do, as opposed to the smell
 * that provoked it. A plan of forty steps is usually six or seven methods, so
 * it is the axis that makes a long plan reviewable in one pass — a developer
 * who trusts Extract Method and distrusts Move Class can act on that belief
 * directly instead of hunting for the steps that happen to embody it.
 */
export const methodOf = (step) => step?.refactoring || "(unspecified)";

/**
 * Approval state over a set of steps, in the shape QuickSelectDropdown reads.
 *
 * Approve is the only verdict counted as "selected": reject and manual are
 * decisions too, but the checkbox in a quick-select answers "is this going to
 * the transformation agent", and neither of those does.
 */
export function approvalState(steps, decisions = {}) {
  const total = (steps || []).length;
  const selected = (steps || []).filter((s) => decisions[s.step_id] === APPROVE).length;
  return {
    total,
    selected,
    all: total > 0 && selected === total,
    partial: selected > 0 && selected < total,
    none: selected === 0,
  };
}

const worstImpact = (steps) => {
  const rank = { high: 0, medium: 1, low: 2 };
  let worst = null;
  for (const step of steps || []) {
    const impact = step.impact || step.expected_impact;
    if (!(impact in rank)) continue;
    if (worst === null || rank[impact] < rank[worst]) worst = impact;
  }
  return worst;
};

/**
 * Generic single-level grouping, preserving first-seen order.
 *
 * Order matters here in a way it does not in Stage 1: the plan arrives from
 * RDP already sequenced, and re-sorting groups by size or severity would put a
 * step that must run third above one that must run first.
 */
function groupBy(steps, keyOf, { decisions = {} } = {}) {
  const index = new Map();
  const groups = [];

  for (const step of steps || []) {
    const key = keyOf(step);
    let group = index.get(key);
    if (!group) {
      group = { key, label: key, steps: [] };
      index.set(key, group);
      groups.push(group);
    }
    group.steps.push(step);
  }

  return groups.map((group) => ({
    ...group,
    stepCount: group.steps.length,
    fileCount: new Set(group.steps.map(fileOf)).size,
    methodCount: new Set(group.steps.map(methodOf)).size,
    selection: approvalState(group.steps, decisions),
    breakdown: groupBreakdown(group.steps),
    worstImpact: worstImpact(group.steps),
  }));
}

/** file -> its steps. */
export const groupStepsByFile = (steps, options) => groupBy(steps, fileOf, options);

/** refactoring method -> its steps, across every file. */
export const groupStepsByMethod = (steps, options) => groupBy(steps, methodOf, options);

/**
 * Options for the plan-method quick-select.
 *
 * Built from ALL steps rather than the filtered ones, for the same reason
 * Stage 1 builds its smell-type options that way: this dropdown is how a
 * developer reaches a method the current filter is hiding, so a list that
 * shrank with the filter would defeat its own purpose.
 *
 * `findingCount` / `fileCount` are named for what QuickSelectDropdown renders,
 * not for what this stage calls them — the component is shared, and renaming
 * its contract for one caller would break the other.
 */
export function methodOptions(steps, decisions = {}) {
  return groupStepsByMethod(steps, { decisions })
    .map((group) => ({
      key: group.key,
      label: group.label,
      steps: group.steps,
      selection: group.selection,
      findingCount: group.stepCount,
      fileCount: group.fileCount,
      worstSeverity: group.worstImpact,
    }))
    .sort((a, b) => b.findingCount - a.findingCount || a.key.localeCompare(b.key));
}

/**
 * Which whole-plan verdict the current decisions amount to.
 *
 *   "all"          every step approved
 *   "reject"       every step rejected
 *   "recommended"  exactly the auto-selectable set approved, nothing else decided
 *   null           anything else — a review in progress
 *
 * DERIVED, never remembered. A flag set when the button was pressed would go
 * on claiming "all selected" after the developer reopened one step and changed
 * their mind. The highlight has to describe the plan's state, not the last
 * button anyone pressed.
 *
 * "recommended" is deliberately strict: approving one extra step by hand is no
 * longer the recommendation, so the bar stops claiming it is. `all` is checked
 * first, because a plan whose recommended set IS every step is both, and "you
 * approved everything" is the more important of the two to say.
 */
export function activeBulkVerdict(steps, decisions = {}) {
  const list = steps || [];
  if (list.length === 0) return null;

  const counts = countDecisions(list, decisions);
  if (counts.approved === list.length) return "all";
  if (counts.rejected === list.length) return "reject";

  const auto = list.filter(isAutoSelectable);
  if (auto.length === 0) return null;
  if (counts.approved !== auto.length) return null;
  if (counts.rejected > 0 || counts.manual > 0) return null;

  return auto.every((step) => decisions[step.step_id] === APPROVE) ? "recommended" : null;
}
