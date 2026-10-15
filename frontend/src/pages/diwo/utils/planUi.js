/**
 * planUi.js
 * =========
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Small presentation helpers shared by the Refactoring Plan Approval stage's
 * header, step navigator and step workspace. Labels and tones only — the
 * recommendation, the score and the decision rules all come from the backend
 * and utils/planningSelection.js.
 */

import { T } from "../codeSmellTheme.js";
import { APPROVE, MANUAL, REJECT } from "./planningSelection.js";

/** How a developer decision is shown. Pending has no entry — it is the absence of one. */
export const DECISION_META = {
  [APPROVE]: { label: "Approved", icon: "✓", tone: T.accentText },
  [REJECT]: { label: "Rejected", icon: "✕", tone: T.high },
  [MANUAL]: { label: "Manual", icon: "🔧", tone: T.info },
};

/** "OrderService.calculateTotal", the file, or "(module level)". */
export const scopeOf = (step) =>
  [step?.target?.class, step?.target?.method].filter(Boolean).join(".")
  || step?.target?.file
  || "(module level)";

/** "L120–184", or "" when the step carries no line range. */
export const linesOf = (step) =>
  Array.isArray(step?.target?.lines) && step.target.lines.length > 0
    ? `L${step.target.lines.join("–")}`
    : "";

/**
 * Tone for a Decision Support Score, on the backend's own thresholds
 * (≥ 80 recommended, ≥ 60 review carefully — see PlanningScoreBreakdown).
 */
export const scoreTone = (score) =>
  typeof score !== "number" ? T.text3 : score >= 80 ? T.low : score >= 60 ? T.medium : T.high;
