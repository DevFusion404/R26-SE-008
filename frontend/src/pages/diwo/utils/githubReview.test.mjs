/**
 * githubReview.test.mjs
 * =====================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Guards what the GitHub-linked review screens offer: a change that was not
 * validated is never offered for acceptance, an applied change cannot be
 * re-decided, submitting needs an accepted, validated change plus write-back
 * and authorization, and the selection shared from GitHub never pre-selects
 * a finding the report does not contain.
 *
 *     npm run test:github     (from frontend/)
 */

import {
  canAccept, canReject, checkLabel, reviewCounts, sharedSelection, stepDisplayId, submitState,
} from "./githubReview.js";

let failures = 0;
const check = (label, cond, detail = "") => {
  if (!cond) failures += 1;
  console.log(`  [${cond ? "PASS" : "FAIL"}] ${label}${!cond && detail ? `  -- ${detail}` : ""}`);
};
const eq = (label, got, want) =>
  check(label, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);

const proposed = { id: "T001", status: "proposed", validated: true };
const failed = { id: "T002", status: "failed", validated: false };
const accepted = { id: "T003", status: "accepted", validated: true };
const applied = { id: "T004", status: "applied", validated: true };

console.log("\n1. Accept / reject gating");
check("validated proposal can be accepted", canAccept(proposed, true));
check("unvalidated change cannot be accepted", !canAccept(failed, true));
check("nothing can be accepted without authorization", !canAccept(proposed, false));
check("an accepted change is not accepted twice", !canAccept(accepted, true));
check("an applied change cannot be rejected", !canReject(applied, true));
check("a failed change can still be rejected", canReject(failed, true));

console.log("\n2. Submitting accepted changes");
eq("needs an accepted change", submitState([proposed], { canReview: true, writebackEnabled: true }).enabled, false);
check("explains disabled write-back",
  /GITHUB_PR_WRITEBACK_ENABLED/.test(submitState([accepted], { canReview: true, writebackEnabled: false }).reason));
eq("needs authorization", submitState([accepted], { canReview: false, writebackEnabled: true }).enabled, false);
eq("refuses an accepted change that is not validated",
  submitState([{ ...accepted, validated: false }], { canReview: true, writebackEnabled: true }).enabled, false);
eq("enabled with the count of accepted changes",
  submitState([accepted, proposed], { canReview: true, writebackEnabled: true }), { enabled: true, reason: null, count: 1 });

console.log("\n3. Decision status counts");
eq("counts per status", reviewCounts([proposed, failed, accepted, applied, accepted]),
  { proposed: 1, accepted: 2, applied: 1, rejected: 0, failed: 1, no_change: 0 });

console.log("\n4. Selection shared from GitHub");
const rows = [{ id: "src/a.py:1:0" }, { id: "src/a.py:5:1" }];
eq("keeps only findings of this report", sharedSelection(["src/a.py:5:1", "old.py:9:0"], rows), ["src/a.py:5:1"]);
eq("no draft means no preselection", sharedSelection(null, rows), null);
eq("an empty draft clears the selection", sharedSelection([], rows), []);

console.log("\n5. Ids and validation labels");
eq("step ids", [stepDisplayId(1), stepDisplayId(12), stepDisplayId("x")], ["P001", "P012", "Px"]);
const record = { validation: { checks: { syntax: { passed: true }, behavioral: { passed: false } } } };
eq("check labels never invent a pass", [checkLabel(record, "syntax"), checkLabel(record, "behavioral"),
  checkLabel(record, "invariant")], ["passed", "failed", "not run"]);

console.log(failures ? `\n${failures} check(s) FAILED\n` : "\nAll GitHub review checks passed.\n");
process.exit(failures ? 1 : 0);
