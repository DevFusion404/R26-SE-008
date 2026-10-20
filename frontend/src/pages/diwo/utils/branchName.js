/**
 * branchName.js
 * =============
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The client half of branch naming for "Push to GitHub". It mirrors the
 * backend's services/branch_naming.py so a typo is flagged as it is typed;
 * the backend still has the final word — it runs `git check-ref-format
 * --branch` and asks the remote whether the branch already exists.
 */

export const BRANCH_PREFIX = "refactoring/diwo-";

const INVALID_CHARS = new Set(["~", "^", ":", "?", "*", "[", "\\"]);

/** Lowercase letters, digits and single hyphens only. */
export function slugify(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** `refactoring/diwo-<run id>` — the same name the backend generates. */
export function suggestBranchName(runId) {
  return `${BRANCH_PREFIX}${slugify(runId).slice(0, 60) || "run"}`;
}

/** Every naming rule `name` breaks, as messages. [] when it looks valid. */
export function branchNameErrors(name) {
  if (!name) return ["Branch name is required."];
  const errors = [];
  if (name.length > 200) errors.push("Branch name is longer than 200 characters.");
  if (/\s/.test(name)) errors.push("Branch name must not contain spaces or other whitespace.");
  if (name.includes("//")) errors.push("Branch name must not contain consecutive slashes (//).");
  if (name.includes("..")) errors.push("Branch name must not contain two consecutive dots (..).");
  if (name.endsWith("/")) errors.push("Branch name must not end with a slash.");
  if (name.endsWith(".")) errors.push("Branch name must not end with a dot.");
  if (name.startsWith("/")) errors.push("Branch name must not start with a slash.");
  if (name.startsWith("-")) errors.push("Branch name must not start with a hyphen.");
  if (name === "@") errors.push("'@' on its own is not a valid branch name.");
  if (name.includes("@{")) errors.push("Branch name must not contain '@{'.");
  if (name.endsWith(".lock") || name.includes("/.") || name.startsWith(".") || name.includes(".lock/")) {
    errors.push("No part of a branch name may start with a dot or end with '.lock'.");
  }
  const bad = [...new Set([...name].filter((ch) => INVALID_CHARS.has(ch)))];
  if (bad.length) {
    errors.push(`Branch name contains characters git does not allow: ${bad.map((c) => `'${c}'`).join(" ")}.`);
  }
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(name)) errors.push("Branch name must not contain control characters.");
  return errors;
}
