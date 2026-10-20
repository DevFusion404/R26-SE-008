/**
 * GithubPrBanner.jsx
 * ==================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Shown above the stages when the DIWO page was opened from a GitHub Check Run
 * link (?diwo_workflow=<id>). Names the repository, pull request and the exact
 * commit CUQA analysed, and says plainly when that commit is no longer the
 * PR head — the findings below then describe older code.
 *
 * Milestone 4: also shows where the developer review stands (plan approved /
 * rejected, and by whom) and, when this user may not decide on the
 * repository or the commit is outdated, why — the backend enforces the same
 * rules, this only explains them before a click fails.
 *
 * Also renders the failure state for such a link (not signed in, no access,
 * unknown workflow), because silently falling back to a fresh CUQA workflow
 * would show the developer a different repository than the one they clicked.
 */

import { C } from "../diwoTheme.jsx";

const box = (tone) => ({
  margin: "0 0 18px",
  padding: "12px 16px",
  borderRadius: 8,
  border: `1px solid color-mix(in srgb, ${tone} 35%, transparent)`,
  background: `color-mix(in srgb, ${tone} 8%, transparent)`,
  fontSize: 13,
  color: C.text,
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "6px 16px",
});

const link = { color: C.info, textDecoration: "none", fontWeight: 600 };
const mono = { fontFamily: "'JetBrains Mono', Consolas, monospace", fontSize: 12 };

export default function GithubPrBanner({ github, workflowStatus, error, onLeave,
                                         transformationEnabled = false }) {
  if (error) {
    const signIn = error.status === 401;
    return (
      <div role="alert" style={box(signIn ? C.warn : C.danger)}>
        <strong>{signIn ? "Sign in required" : "This GitHub-linked workflow could not be opened"}</strong>
        <span style={{ color: C.textSub }}>{error.message}</span>
        <button type="button" onClick={onLeave}
          style={{ marginLeft: "auto", background: "none", border: `1px solid ${C.border}`, color: C.textSub, borderRadius: 6, padding: "4px 10px", cursor: "pointer" }}>
          Start a new workflow instead
        </button>
      </div>
    );
  }
  if (!github) return null;

  return (
    <>
      <div style={box(C.info)}>
        <strong>GitHub pull request</strong>
        <span>
          {github.repository}{" "}
          <a href={github.pull_request_url} target="_blank" rel="noreferrer" style={link}>
            #{github.pr_number}
          </a>
        </span>
        <span>
          Analysed commit{" "}
          <a href={github.commit_url} target="_blank" rel="noreferrer" style={{ ...link, ...mono }}
            title={github.head_sha}>
            {github.head_sha?.slice(0, 7)}
          </a>
        </span>
        {typeof github.smell_count === "number" && (
          <span style={{ color: C.textSub }}>
            {github.smell_count} smell{github.smell_count === 1 ? "" : "s"}
            {typeof github.files_analyzed === "number" ? ` in ${github.files_analyzed} analysed file(s)` : ""}
          </span>
        )}
        <button type="button" onClick={onLeave}
          style={{ marginLeft: "auto", background: "none", border: "none", color: C.textMuted, cursor: "pointer", fontSize: 12 }}>
          Leave PR view
        </button>
      </div>
      {github.is_latest_commit === false && (
        <div role="status" style={box(C.warn)}>
          A newer commit has been pushed to this pull request. These findings describe
          commit <span style={mono}>{github.head_sha?.slice(0, 7)}</span>; open the latest
          RefactorIQ / DIWO check on GitHub for the current code.
        </div>
      )}
      {workflowStatus === "plan_approved" && (
        <div role="status" style={box(C.accent)}>
          <strong>Plan approved</strong>
          <span style={{ color: C.textSub }}>
            {github.reviewed_by ? `by ${github.reviewed_by}. ` : ""}
            {transformationEnabled
              ? "SCTVA is transforming the approved steps on the pull-request commit; review its proposed changes below. Nothing is committed until you accept and submit them."
              : "The approval is recorded and shown on the pull request. Transformation of pull-request code is not enabled on this backend, so no code has been changed."}
          </span>
        </div>
      )}
      {workflowStatus === "completed" && github.review_status === "applied" && (
        <div role="status" style={box(C.accent)}>
          <strong>Accepted changes applied</strong>
          <span style={{ color: C.textSub }}>
            They were committed to the pull request branch; the new commit is analysed as a new snapshot.
          </span>
        </div>
      )}
      {workflowStatus === "rolled_back" && (
        <div role="status" style={box(C.warn)}>
          The refactoring plan for this commit was rejected
          {github.reviewed_by ? ` by ${github.reviewed_by}` : ""}.
        </div>
      )}
      {github.can_review === false && github.reason && (
        <div role="status" style={box(C.warn)}>
          <strong>View only</strong>
          <span style={{ color: C.textSub }}>{github.reason}</span>
        </div>
      )}
    </>
  );
}
