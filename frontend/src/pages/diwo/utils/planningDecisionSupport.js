/**
 * Stage 2 decision-support presentation helpers
 * =============================================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * PRESENTATION ONLY. The recommendation itself — the category, the Decision
 * Support Score, `auto_select_eligible`, the reasons and the warnings — is
 * computed by the DIWO backend in domain/planning_recommendation.py and
 * arrives on each step as `step.decision_support`.
 *
 * Nothing here re-derives it. Two implementations of one scoring formula, in
 * two languages, is two answers to the same question and no way to tell which
 * one produced a given screenshot — so this module maps a category the backend
 * already chose onto a colour, an icon and a label, and stops.
 *
 * The one thing it does compute is `summarizeSteps()`, and only as a fallback
 * for a plan that arrived without `decision_support_summary` (an older backend,
 * or the bundled sample plan). It counts categories the backend assigned; it
 * never assigns one.
 */

import { C } from "../diwoTheme.jsx";

export const RECOMMENDED = "recommended";
export const REVIEW = "review";
export const NOT_RECOMMENDED = "not_recommended";
export const MANUAL_ONLY = "manual_only";

/** Display order, worst-to-best being the wrong way round for a summary row. */
export const CATEGORY_ORDER = [RECOMMENDED, REVIEW, NOT_RECOMMENDED, MANUAL_ONLY];

/**
 * Colour, icon and wording per category.
 *
 * The icon and the text label are not decoration: the four categories must be
 * distinguishable without colour, so every surface that renders a category
 * renders `icon` and `label` too, never the swatch alone.
 */
export const CATEGORY_STYLE = {
  [RECOMMENDED]: {
    color: C.accent,
    icon: "🟢",
    label: "Recommended",
    short: "Recommended",
    verb: "DIWO recommends this step",
  },
  [REVIEW]: {
    color: C.warn,
    icon: "🟡",
    label: "Review Carefully",
    short: "Review",
    verb: "DIWO suggests reading this step first",
  },
  [NOT_RECOMMENDED]: {
    color: C.danger,
    icon: "🔴",
    label: "Not Recommended",
    short: "Not recommended",
    verb: "DIWO does not recommend this step",
  },
  [MANUAL_ONLY]: {
    color: C.info,
    icon: "🔵",
    label: "Manual Refactoring Suggested",
    short: "Manual only",
    verb: "DIWO suggests doing this by hand",
  },
};

const UNCLASSIFIED = {
  color: C.textMuted,
  icon: "○",
  label: "Not assessed",
  short: "Not assessed",
  verb: "No recommendation was produced for this step",
};

/** The style block for a category, or a neutral one for an unassessed step. */
export const categoryStyle = (category) => CATEGORY_STYLE[category] || UNCLASSIFIED;

/** `step.decision_support`, or null when the backend did not assess this step. */
export const supportOf = (step) =>
  step && typeof step.decision_support === "object" ? step.decision_support : null;

export const categoryOf = (step) => supportOf(step)?.category || null;

/** Only the backend may mark a step auto-selectable. Absence means "no". */
export const isAutoSelectable = (step) => supportOf(step)?.auto_select_eligible === true;

/** Human label for the developer strategy the backend reported. */
export const STRATEGY_OPTIONS = [
  { value: "safety_first", icon: "🛡", label: "Safety First",
    hint: "Only low-risk, well-covered transformations" },
  { value: "balanced", icon: "⚖", label: "Balanced",
    hint: "Weigh expected improvement against transformation risk" },
  { value: "max_improvement", icon: "🚀", label: "Maximum Improvement",
    hint: "Favour the largest quality gain, accepting more risk" },
];

export const strategyLabel = (value) =>
  STRATEGY_OPTIONS.find((option) => option.value === value)?.label || "Balanced";

/**
 * A per-file breakdown, so a file-level "approve all" says what it is about to
 * approve. §47: a file holding 2 recommended, 1 review and 1 manual-only must
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
    /** True when the file's steps do not all carry the same recommendation. */
    mixed: CATEGORY_ORDER.filter((c) => counts[c] > 0).length + (unassessed ? 1 : 0) > 1,
    nonGreen: counts[REVIEW] + counts[NOT_RECOMMENDED] + counts[MANUAL_ONLY] + unassessed,
  };
}

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

/** "~6.3" / "—". Never invents a value for a figure the backend left null. */
export const formatPoints = (value, { signed = true } = {}) => {
  if (typeof value !== "number" || Number.isNaN(value)) return "—";
  const rounded = Math.round(value * 10) / 10;
  return `${signed && rounded > 0 ? "+" : ""}${rounded}`;
};

export const formatMinutes = (value) =>
  typeof value === "number" && !Number.isNaN(value) ? `~${value} min` : "—";
