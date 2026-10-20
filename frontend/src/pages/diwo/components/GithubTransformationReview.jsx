/**
 * GithubTransformationReview.jsx
 * ==============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The final review of a GitHub-linked workflow: SCTVA's proposed changes for
 * the analysed PR commit, one per file (T001, T002, ...). Shows the original
 * and refactored source side by side or as a diff, SCTVA's real validation
 * per change, and lets an authorized developer accept or reject each one and
 * submit the accepted set to the pull request.
 *
 * Same records as the /refactoriq commands — a decision taken here appears on
 * GitHub and vice versa. Nothing is committed until accepted changes are
 * explicitly submitted, and the backend re-checks validation, authorization
 * and the PR head before writing.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { C } from "../diwoTheme.jsx";
import { CodeBlock, CodeSurface, DiffBlock, DiffLegend } from "./DiffView.jsx";
import { buildDiffRows } from "../utils/diffMapper";
import {
  canAccept, canReject, checkLabel, reviewCounts, stepDisplayId, submitState,
} from "../utils/githubReview";
import {
  decideGithubTransformations, downloadGithubPatch, fetchGithubTransformations,
  submitGithubRefactoring,
} from "../services/diwoApi";

const STATUS_TONE = {
  proposed: C.info, accepted: C.accent, applied: C.accent,
  rejected: C.textMuted, failed: C.danger, no_change: C.warn,
};

const CHECKS = [
  ["syntax", "Syntax"], ["structural", "Structural"],
  ["behavioral", "Behavioural"], ["invariant", "Invariant"],
];

const chip = (tone) => ({
  fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 999,
  color: tone, border: `1px solid color-mix(in srgb, ${tone} 35%, transparent)`,
  background: `color-mix(in srgb, ${tone} 10%, transparent)`, whiteSpace: "nowrap",
});

const button = (tone, disabled) => ({
  padding: "6px 12px", borderRadius: 6, fontSize: 12, fontWeight: 700,
  cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
  border: `1px solid ${tone}`, background: "transparent", color: tone,
});

function CheckChip({ name, label, record }) {
  const state = checkLabel(record, name);
  const tone = state === "passed" ? C.accent : state === "failed" ? C.danger : C.textMuted;
  return (
    <span style={chip(tone)} title={record.validation?.checks?.[name]?.message
                                   || (state === "not run" ? "SCTVA did not run this check" : "")}>
      {label}: {state}
    </span>
  );
}

export default function GithubTransformationReview({ workflowId, github, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [activeId, setActiveId] = useState(null);
  const [view, setView] = useState("diff");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(async (signal) => {
    try {
      const res = await fetchGithubTransformations(workflowId, { signal });
      setData(res);
      setError(null);
    } catch (e) {
      if (e.name !== "AbortError") setError(e);
    }
  }, [workflowId]);

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      await Promise.resolve();
      load(controller.signal);
    })();
    return () => controller.abort();
  }, [load]);

  // While SCTVA is still running (or a commit is being made), re-read.
  const waiting = data && (
    (data.status === "plan_approved" && data.transformation_enabled && !data.transformations.length)
    || ["pending", "committing"].includes(data.writeback?.status));
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setInterval(() => load(), 5000);
    return () => clearInterval(timer);
  }, [waiting, load]);

  const records = useMemo(() => data?.transformations || [], [data]);
  const active = records.find((r) => r.id === activeId) || records[0] || null;
  const accepted = records.filter((r) => r.status === "accepted");
  const canReview = github?.can_review !== false;
  const counts = reviewCounts(records);
  const submit = submitState(records, { canReview, writebackEnabled: data?.writeback_enabled });
  const rows = useMemo(
    () => (active?.refactored ? buildDiffRows(active.original || "", active.refactored) : []),
    [active]
  );

  const act = async (fn, message) => {
    setBusy(true);
    setNotice(null);
    try {
      await fn();
      setNotice({ tone: C.accent, text: message });
      await load();
      onChanged?.();
    } catch (e) {
      setNotice({ tone: C.danger, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return <div role="alert" style={{ color: C.danger, fontSize: 13 }}>{error.message}</div>;
  }
  if (!data) return <div style={{ color: C.textMuted, fontSize: 13 }}>Loading proposed changes…</div>;

  if (!records.length) {
    return (
      <div style={{ padding: 20, border: `1px solid ${C.border}`, borderRadius: 8, fontSize: 13,
                    color: C.textSub, background: C.panel }}>
        {data.transformation_enabled
          ? (data.status === "plan_approved"
              ? "SCTVA is transforming the approved plan on the pull-request commit… this view updates automatically."
              : "No proposed changes for this workflow.")
          : "The plan is approved and recorded. Transformation of pull-request code is not enabled on this DIWO backend (GITHUB_PR_TRANSFORMATION_ENABLED), so no code has been changed."}
      </div>
    );
  }

  const wb = data.writeback;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Summary + submit */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, padding: "12px 16px",
                    border: `1px solid ${C.border}`, borderRadius: 8, background: C.panel, fontSize: 13 }}>
        <strong>{records.length} file(s) transformed</strong>
        <span style={{ color: C.textSub }}>
          {counts.proposed} awaiting decision · {counts.accepted} accepted · {counts.applied} applied
          {counts.failed + counts.no_change ? ` · ${counts.failed + counts.no_change} not acceptable` : ""}
        </span>
        <span style={{ color: C.textMuted, fontSize: 12 }}>
          Repository tests are not run by SCTVA — validation covers syntax, structure and behaviour of the changed code.
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" style={button(C.textSub, busy || !accepted.length)}
            disabled={busy || !accepted.length}
            onClick={() => act(() => downloadGithubPatch(workflowId), "Patch downloaded.")}>
            Download patch
          </button>
          <button type="button" style={button(C.accent, busy || !submit.enabled)}
            disabled={busy || !submit.enabled} title={submit.reason || ""}
            onClick={() => setConfirming(true)}>
            Submit accepted changes
          </button>
        </div>
      </div>

      {wb && (
        <div role="status" style={{ fontSize: 13, color: wb.status === "committed" ? C.accent
                                    : ["refused", "failed"].includes(wb.status) ? C.danger : C.textSub }}>
          Write-back {wb.status}
          {wb.commit_sha ? ` — commit ${wb.commit_sha.slice(0, 7)} on ${wb.branch}` : ""}
          {wb.error ? ` — ${wb.error}` : ""}
        </div>
      )}
      {notice && <div role="status" style={{ fontSize: 13, color: notice.tone }}>{notice.text}</div>}

      {confirming && (
        <div role="dialog" aria-label="Confirm submission" style={{ padding: 16, borderRadius: 8,
             border: `1px solid ${C.accent}`, background: C.panel, fontSize: 13 }}>
          <p style={{ margin: "0 0 8px" }}>
            Commit these <strong>{accepted.length}</strong> accepted change(s) to the pull request branch?
            It is a fast-forward commit on top of the analysed commit — refused if anyone has pushed since.
          </p>
          <ul style={{ margin: "0 0 12px 18px" }}>
            {accepted.map((r) => <li key={r.id}>{r.id} — {r.file}</li>)}
          </ul>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" style={button(C.accent, busy)} disabled={busy}
              onClick={() => { setConfirming(false); act(() => submitGithubRefactoring(workflowId),
                "Submitted — the commit is being made; this view updates automatically."); }}>
              Commit to pull request
            </button>
            <button type="button" style={button(C.textSub, false)} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* File navigation + detail, each scrolling independently */}
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "stretch" }}>
        <nav aria-label="Transformed files" style={{ flex: "1 1 220px", maxWidth: 320, maxHeight: 560,
             overflowY: "auto", border: `1px solid ${C.border}`, borderRadius: 8, background: C.panel }}>
          {records.map((r) => (
            <button key={r.id} type="button" onClick={() => setActiveId(r.id)}
              aria-current={active?.id === r.id}
              style={{ display: "block", width: "100%", textAlign: "left", padding: "10px 12px",
                       border: "none", borderBottom: `1px solid ${C.border}`, cursor: "pointer",
                       background: active?.id === r.id ? C.accentGlow : "transparent", color: C.text }}>
              <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                <strong style={{ fontSize: 12 }}>{r.id}</strong>
                <span style={chip(STATUS_TONE[r.status] || C.textMuted)}>{r.status.replace("_", " ")}</span>
              </div>
              <div style={{ fontSize: 12, color: C.textSub, marginTop: 4, wordBreak: "break-all" }}>{r.file}</div>
            </button>
          ))}
        </nav>

        {active && (
          <section style={{ flex: "3 1 480px", minWidth: 0, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              <strong style={{ fontSize: 14 }}>{active.id} — {active.file}</strong>
              {active.refactorings.map((x) => (
                <span key={`${x.step_id}`} style={chip(C.info)}>{stepDisplayId(x.step_id)} {x.refactoring}</span>
              ))}
              {typeof active.confidence === "number" && (
                <span style={chip(C.textSub)}>confidence {active.confidence.toFixed(2)}</span>
              )}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {CHECKS.map(([name, label]) => <CheckChip key={name} name={name} label={label} record={active} />)}
            </div>
            {active.status_reason && ["failed", "no_change"].includes(active.status) && (
              <div role="status" style={{ fontSize: 13, color: C.warn }}>
                Not acceptable: {active.status_reason}. The original source is kept.
              </div>
            )}
            {active.coupled && (
              <div style={{ fontSize: 12, color: C.warn }}>
                Part of a cross-file refactoring — accept all of its files together or none.
              </div>
            )}
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
              <button type="button" style={button(C.accent, busy || !canAccept(active, canReview))}
                disabled={busy || !canAccept(active, canReview)}
                onClick={() => act(() => decideGithubTransformations(workflowId, { [active.id]: "accept" }),
                  `${active.id} accepted.`)}>
                Accept
              </button>
              <button type="button" style={button(C.danger, busy || !canReject(active, canReview))}
                disabled={busy || !canReject(active, canReview)}
                onClick={() => act(() => decideGithubTransformations(workflowId, { [active.id]: "reject" }),
                  `${active.id} rejected — the original source is kept.`)}>
                Reject
              </button>
              {active.decided_by && (
                <span style={{ fontSize: 12, color: C.textMuted }}>Last decision by {active.decided_by}</span>
              )}
              <div role="tablist" style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                {[["diff", "Unified diff"], ["side", "Side by side"]].map(([key, label]) => (
                  <button key={key} type="button" role="tab" aria-selected={view === key}
                    onClick={() => setView(key)}
                    style={{ ...button(view === key ? C.info : C.textMuted, false), padding: "4px 10px" }}>
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {view === "diff" ? (
              <>
                <DiffLegend />
                <CodeSurface maxHeight={520}>
                  <DiffBlock rows={rows} emptyMessage="SCTVA did not change this file." />
                </CodeSurface>
              </>
            ) : (
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <div style={{ flex: "1 1 320px", minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.textSub, marginBottom: 4 }}>
                    Original (commit {active.head_sha?.slice(0, 7)})
                  </div>
                  <CodeSurface maxHeight={520}><CodeBlock code={active.original} /></CodeSurface>
                </div>
                <div style={{ flex: "1 1 320px", minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.textSub, marginBottom: 4 }}>
                    Refactored (proposed)
                  </div>
                  <CodeSurface maxHeight={520}>
                    <CodeBlock code={active.refactored} emptyMessage="No change was produced." />
                  </CodeSurface>
                </div>
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
