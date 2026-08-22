/**
 * Local selection aggregation
 * ===========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Mirrors backend/domain/impact_model.py::aggregate so the trade-off panel can
 * update on the same frame as the checkbox rather than after a round trip.
 *
 * Why duplicate it at all
 * ----------------------
 * The alternative is POSTing /selection-impact on every click. Even debounced
 * that lags the tick, and the panel exists to make the consequence of a click
 * feel immediate. The records themselves are fetched once and never
 * recomputed here — this only sums over them, which is arithmetic the browser
 * can do as fast as it can re-render.
 *
 * The backend stays authoritative: the page calls /selection-impact when the
 * developer commits, and that is the projection written to the audit trail.
 * If the two ever disagree, the server's number is the one that counts.
 *
 * Pure — no React, no network — so it is testable on its own.
 */

const EXECUTABLE = "executable";
const ADVISORY = "advisory";

/**
 * Round the way Python's built-in round() does — half to EVEN.
 *
 * Not a stylistic choice: `Math.round` breaks ties upward, Python breaks them
 * toward the even neighbour, so a baseline of 73.25 renders as 73.3 here and
 * is stored as 73.2 by the backend. The developer would see one number on
 * screen and a different one in the audit trail.
 *
 * Only exact ties diverge. Everywhere else both languages round the same
 * underlying float identically, so those fall through to Math.round.
 *
 * Values in this module are all non-negative (points, minutes, scores), which
 * is why the tie branch does not handle the negative-zero case.
 */
const round = (value, dp = 2) => {
  const factor = 10 ** dp;
  const scaled = value * factor;
  const floor = Math.floor(scaled);

  if (scaled - floor === 0.5) {
    return (floor % 2 === 0 ? floor : floor + 1) / factor;
  }
  return Math.round(scaled) / factor;
};

/**
 * Aggregate impact records against the current selection.
 *
 * @param {Array}  records  every record for the workflow
 * @param {Set}    selectedIds  the smell ids currently ticked
 * @param {number} qualityBefore  the report's average quality score
 */
export function summariseSelection(records, selectedIds, qualityBefore = 0) {
  const selected = new Set(selectedIds || []);
  const all = records || [];

  const picked = all.filter((r) => selected.has(r.smell_id));
  const skipped = all.filter((r) => !selected.has(r.smell_id));

  const sum = (rows, get) => rows.reduce((total, r) => total + (get(r) || 0), 0);

  const captured = sum(picked, (r) => r.if_selected.quality_gain.automated_points);
  const ceiling = sum(all, (r) => r.if_selected.quality_gain.automated_points);
  const forgone = sum(skipped, (r) => r.if_deferred.carried_points);
  const interest = sum(skipped, (r) => r.if_deferred.interest_per_quarter);

  const executable = picked.filter((r) => r.capability.status === EXECUTABLE);
  const advisory = picked.filter((r) => r.capability.status === ADVISORY);
  const risks = executable.map((r) => r.if_selected.risk.score);

  const before = Number(qualityBefore) || 0;

  return {
    selected_count: picked.length,
    executable_count: executable.length,
    advisory_count: advisory.length,
    skipped_count: skipped.length,
    quality_before: round(before, 1),
    quality_projected: round(Math.min(100, before + captured), 1),
    quality_ceiling: round(Math.min(100, before + ceiling), 1),
    captured_points: round(captured),
    ceiling_points: round(ceiling),
    capture_rate: ceiling ? round(captured / ceiling, 3) : 0,
    forgone_points: round(forgone),
    quarterly_interest: round(interest),
    effort_minutes: sum(picked, (r) => r.if_selected.effort_minutes),
    max_risk: risks.length ? round(Math.max(...risks)) : 0,
    mean_risk: risks.length ? round(risks.reduce((a, b) => a + b, 0) / risks.length) : 0,
    warnings: buildWarnings(advisory, executable, skipped),
  };
}

/** Mirrors impact_model._warnings — the things worth interrupting for. */
function buildWarnings(advisory, executable, skipped) {
  const out = [];

  if (advisory.length && !executable.length) {
    out.push({
      level: "error",
      message:
        `All ${advisory.length} selected smells are advisory-only. ` +
        "This run will produce no code changes.",
    });
  } else if (advisory.length) {
    out.push({
      level: "warning",
      message:
        `${advisory.length} of your selections have no automatic fix and will ` +
        "come back as no-ops. They stay in the report as findings.",
    });
  }

  const hot = skipped.filter(
    (r) => r.if_deferred.change_pressure === "high" && r.capability.status === EXECUTABLE
  );
  if (hot.length) {
    out.push({
      level: "info",
      message:
        `${hot.length} skipped smell(s) sit in files the team edits frequently — ` +
        "the most expensive kind to defer.",
    });
  }

  return out;
}

/**
 * The report's baseline quality score — what a projected gain is added to.
 * Falls back to the mean of the per-file scores when the summary omits it.
 */
export function qualityBaseline(report) {
  const summary = report?.summary || {};
  if (typeof summary.average_quality_score === "number") {
    return summary.average_quality_score;
  }

  const scored = (report?.files || []).filter((f) => typeof f.quality_score === "number");
  if (!scored.length) return 0;
  return scored.reduce((total, f) => total + f.quality_score, 0) / scored.length;
}
