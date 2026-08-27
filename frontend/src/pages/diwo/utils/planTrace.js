/**
 * RDP plan enrichment (pure helper)
 * =================================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Formerly diwo/rdpApi.js. It carries no HTTP at all, which is why it lives
 * under utils/ rather than services/ — the name no longer claims otherwise.
 *
 * Planning is owned by the DIWO backend: POST /workflows/<id>/select-smells
 * forwards the *updated* smell report — the developer's selection, with
 * deselected smells and now-empty files removed — to the RDP agent's
 * POST /generate (see diwo/rdp_client.py) and returns the plan and its trace.
 *
 * The browser used to POST /generate a second time from
 * RefactoringPlanApprovalPage, built from `workflow.updated_report ||
 * cuqaReport`. Whenever `updated_report` was missing, that fell back to the
 * FULL report and every deselected smell reappeared in the plan on screen.
 * The fetch helpers were removed rather than left unused so the second
 * request cannot be reintroduced by accident — there is now exactly one
 * /generate call per smell-selection flow, and it is made by the backend.
 *
 * What remains is pure: the plan's steps carry only the transformation-facing
 * fields
 *
 *     { step_id, smell_id, refactoring, target, parameters, explanation }
 *
 * while the impact / risk / complexity ratings and the MCDA score the approval
 * page renders live in the trace, on the candidate that was selected for that
 * smell. enrichPlanWithTrace() folds them back onto every step.
 */

const RATINGS = ["low", "medium", "high"];

/** Coerce a knowledge-base rating to one of low | medium | high. */
const rating = (value, fallback = "medium") => {
  const normalized = String(value ?? "").toLowerCase();
  return RATINGS.includes(normalized) ? normalized : fallback;
};

const round = (value, dp = 3) => {
  if (typeof value !== "number" || Number.isNaN(value)) return null;
  const factor = 10 ** dp;
  return Math.round(value * factor) / factor;
};

/**
 * Fold the trace's decision data back onto each plan step.
 *
 * For every step the trace is searched by smell_id for the candidate the
 * decision engine picked, which is where impact / risk / complexity and the
 * MCDA score live. Rejected-but-viable candidates come along as `alternatives`
 * so the developer can see what the agent weighed before approving the step.
 */
export function enrichPlanWithTrace(plan, trace) {
  const selections = new Map(
    (trace?.candidate_generation || []).map((entry) => [entry.smell_id, entry])
  );
  const impacts = new Map(
    (trace?.impact_prediction || []).map((entry) => [entry.smell_id, entry.predictions || []])
  );
  const mcda = new Map(
    (trace?.mcda_selection || []).map((entry) => [entry.smell_id, entry.predictions || []])
  );
  const inputs = new Map(
    (trace?.input_summary?.smells || []).map((smell) => [smell.id, smell])
  );

  const steps = (plan?.steps || []).map((step) => {
    const selection = selections.get(step.smell_id);
    const candidates = selection?.candidates || [];
    const chosen = candidates.find((c) => c.name === step.refactoring) || {};
    const source = inputs.get(step.smell_id) || null;

    const mcdaScore = (mcda.get(step.smell_id) || []).find(
      (m) => m.refactoring === step.refactoring
    );
    const score = selection?.selected_score ?? mcdaScore?.final_score ?? null;

    // A value already on the step wins over anything derived here.
    //
    // The DIWO backend now performs this same fold before it scores the plan
    // (domain/plan_normalizer.fold_trace_into_plan), because the decision
    // support on each step is computed FROM these ratings — a fold that only
    // happened in the browser would leave the backend scoring a step as
    // medium-risk and unscored while this row rendered "Risk: high · RDP 0.84"
    // beside the badge that claim produced.
    //
    // Both folds read the same trace, so they agree; `keep()` is what
    // guarantees it, by making this pass unable to overwrite the backend's
    // answer with its own `rating()` fallback when the trace is thin.
    const keep = (existing, derived) =>
      existing === undefined || existing === null ? derived : existing;

    return {
      ...step,
      impact: keep(step.impact, rating(chosen.impact)),
      risk: keep(step.risk, rating(chosen.risk)),
      complexity: keep(step.complexity, rating(chosen.complexity)),
      score: keep(step.score, round(score)),
      scoring_method: keep(step.scoring_method, selection?.scoring_method || null),
      smell_type: keep(step.smell_type, selection?.smell_type || source?.type || null),
      severity: keep(step.severity, selection?.severity || source?.severity || null),
      location: keep(step.location, source?.location || null),
      smell_metrics: keep(step.smell_metrics, source?.metrics || null),
      prediction: keep(
        step.prediction,
        (impacts.get(step.smell_id) || []).find((p) => p.refactoring === step.refactoring) || null,
      ),
      alternatives: step.alternatives?.length
        ? step.alternatives
        : candidates
            .filter((c) => c.name !== step.refactoring && c.preconditions_met)
            .map((c) => ({
              name: c.name,
              score: round(c.score),
              impact: rating(c.impact),
              risk: rating(c.risk),
            })),
    };
  });

  const generation = trace?.plan_generation || {};

  return {
    ...plan,
    steps,
    source: "rdp",
    skipped_smells: generation.skipped_smells || [],
    smells_skipped: generation.smells_skipped ?? 0,
    total_smells: trace?.input_summary?.total_smells ?? steps.length,
    reordered: Boolean(trace?.dependency_analysis?.reordered),
  };
}
