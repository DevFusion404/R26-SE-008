/**
 * Stage 2 decision-support presentation
 * =====================================
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
 * The decision SEMANTICS — what a bulk action touches, what survives a
 * re-rank, what reaches SCTVA — live in planningSelection.js, which imports
 * nothing and is therefore testable under plain `node`. They are re-exported
 * here so the components keep importing one module.
 */

import { C } from "../diwoTheme.jsx";
import {
  MANUAL_ONLY, NOT_RECOMMENDED, RECOMMENDED, REVIEW,
} from "./planningSelection.js";

// The decision semantics, re-exported so components import one module.
export * from "./planningSelection.js";

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
    section: "Recommended",
    sectionIcon: "✓",
    verb: "DIWO recommends this step",
    blurb: "High expected improvement, low transformation risk, and SCTVA can execute it.",
  },
  [REVIEW]: {
    color: C.warn,
    icon: "🟡",
    label: "Review Carefully",
    short: "Review",
    section: "Needs Your Review",
    sectionIcon: "⚠",
    verb: "DIWO suggests reading this step first",
    blurb: "Worth doing, but something about it needs your judgement before it runs.",
  },
  [NOT_RECOMMENDED]: {
    color: C.danger,
    icon: "🔴",
    label: "Not Recommended",
    short: "Not recommended",
    section: "Not Recommended",
    sectionIcon: "✕",
    verb: "DIWO does not recommend this step",
    blurb: "The expected benefit does not cover the risk or the missing evidence.",
  },
  [MANUAL_ONLY]: {
    color: C.info,
    icon: "🔵",
    label: "Manual Refactoring Suggested",
    short: "Manual only",
    section: "Manual Refactoring",
    sectionIcon: "🔧",
    verb: "DIWO suggests doing this by hand",
    blurb: "SCTVA has no safe automatic form for this refactoring in the current build.",
  },
};

const UNCLASSIFIED = {
  color: C.textMuted,
  icon: "○",
  label: "Not assessed",
  short: "Not assessed",
  section: "Not Assessed",
  sectionIcon: "○",
  verb: "No recommendation was produced for this step",
  blurb: "DIWO decision support is unavailable for this step — review the RDP evidence manually.",
};

/** The style block for a category, or a neutral one for an unassessed step. */
export const categoryStyle = (category) => CATEGORY_STYLE[category] || UNCLASSIFIED;

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

/** Risk band → colour, shared by the header metric and the card fact strip. */
export const RISK_COLOR = { low: C.low, medium: C.warn, high: C.danger };

/** Impact band → colour, for the "Benefit" fact. */
export const BENEFIT_COLOR = { high: C.accent, medium: C.warn, low: C.textSub };
