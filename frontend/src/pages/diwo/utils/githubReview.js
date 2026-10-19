/**
 * githubReview.js
 * ===============
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Pure rules behind the GitHub-linked review screens. The backend enforces
 * every one of them; these only decide what the UI offers, so a button is
 * never shown enabled for an action the server will refuse.
 */

/** Can this change be accepted from DIWO right now? */
export function canAccept(record, canReview) {
  return Boolean(canReview && record?.validated
    && !["accepted", "applied"].includes(record.status));
}

/** Can this change be rejected right now? (An applied change cannot.) */
export function canReject(record, canReview) {
  return Boolean(canReview && record && !["rejected", "applied"].includes(record.status));
}

/** Whether "Submit accepted changes" is available, and why not when it is not. */
export function submitState(records, { canReview, writebackEnabled }) {
  const accepted = (records || []).filter((r) => r.status === "accepted");
  if (!accepted.length) return { enabled: false, reason: "Accept at least one validated change first." };
  if (!writebackEnabled) {
    return { enabled: false,
             reason: "Write-back is disabled on this DIWO backend (GITHUB_PR_WRITEBACK_ENABLED); download the patch instead." };
  }
  if (!canReview) return { enabled: false, reason: "You are not authorized to change this repository." };
  if (accepted.some((r) => !r.validated)) {
    return { enabled: false, reason: "An accepted change is not validated." };
  }
  return { enabled: true, reason: null, count: accepted.length };
}

/** Counts for the summary line. */
export function reviewCounts(records) {
  const counts = { proposed: 0, accepted: 0, applied: 0, rejected: 0, failed: 0, no_change: 0 };
  for (const r of records || []) counts[r.status] = (counts[r.status] || 0) + 1;
  return counts;
}

/**
 * The GitHub draft selection, restricted to findings this report really has.
 * An id the report does not contain (an older commit's finding) is dropped,
 * never shown as selected.
 */
export function sharedSelection(draftIds, rows) {
  if (!Array.isArray(draftIds)) return null;
  const known = new Set((rows || []).map((r) => r.id));
  return draftIds.filter((id) => known.has(id));
}

/** "P001" for a step, as plan commands name it. */
export function stepDisplayId(stepId) {
  const n = Number(stepId);
  return Number.isInteger(n) ? `P${String(n).padStart(3, "0")}` : `P${stepId}`;
}

/** The label of one validation check: passed / failed / not run. */
export function checkLabel(record, name) {
  const check = record?.validation?.checks?.[name];
  if (!check) return "not run";
  return check.passed === true ? "passed" : "failed";
}
