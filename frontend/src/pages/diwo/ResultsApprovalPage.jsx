/**
 * ResultsApprovalPage.jsx
 * =======================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Stage 4 of the DIWO workflow: review what the Safe Code Transformation &
 * Validation Agent produced, then commit it.
 *
 * The per-file verdict taken in the Transformation stage is honoured here.
 * Each file entry arrives carrying `decision`, its original text (`before`)
 * and the agent's output (`after`), and this page derives one `finalCode` from
 * them:
 *
 *     accepted -> finalCode = after   (the refactored source)
 *     rejected -> finalCode = before  (reverted to the original source)
 *
 * `finalCode` is what the page displays, what the download writes, and what is
 * POSTed to /api/diwo/apply-and-push — so rejecting a file genuinely reverts
 * it instead of only hiding it from the view.
 *
 * Every number on this page comes from the agent's own response (`sctva`):
 * the confidence score and its components, the four validation stages, the
 * transformation log and the mined invariants.
 */

import { useMemo, useState } from "react";
import { Badge, C, Card, Pill } from "./diwoTheme.jsx";
import { CodeBlock, CodeSurface, DiffBlock, DiffLegend } from "./diffView.jsx";
import { buildDiffRows } from "./sctvaApi";

const VALIDATION_LABELS = {
  syntax: ["Syntax Validation", "Compilation / parse check of the transformed source."],
  structural: ["Structural Analysis", "AST shape compared against the original."],
  behavioral: ["Behavioral Probes", "Runtime or static behavioural fingerprint comparison."],
  invariant: ["Invariant Mining", "Program invariants mined before and after."],
};

const COMPONENT_LABELS = [
  ["syntax_component", "Syntax"],
  ["structural_component", "Structural"],
  ["behavioral_component", "Behavioral"],
  ["invariant_component", "Invariant"],
];

const pct = (value, dp = 1) =>
  typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(dp)}%` : "—";

export default function ResultsApprovalPage({
  onRestart,
  onRollback,
  onAccept,
  refactoredCode,
  diffRows = [],
  files = [],
  sctva = null,
  repositoryPath = "",
}) {
  const [tab, setTab] = useState("summary");
  const [selectedFileIndex, setSelectedFileIndex] = useState(0);
  const [view, setView] = useState("final");
  const [showActionChoice, setShowActionChoice] = useState(false);
  const [branchName, setBranchName] = useState("refactoring/diwo-changes");
  const [repoPath, setRepoPath] = useState(repositoryPath || "");
  const [isProcessing, setIsProcessing] = useState(false);

  // ── Resolve each file to the code that will actually be written ───────────
  const fileEntries = useMemo(() => {
    const source =
      files.length > 0
        ? files
        : refactoredCode
          ? [
              {
                path: `refactored_code.${String(sctva?.language || "txt").toLowerCase()}`,
                before: "",
                after: refactoredCode,
                diff_rows: diffRows,
                decision: "accept",
              },
            ]
          : [];

    return source.map((f, idx) => {
      const path = f.path || f.file || `file-${idx}`;
      const before = f.before ?? "";
      const after = f.after ?? f.refactored_code ?? "";
      const rejected = f.decision === "reject";
      // A reject can only be honoured when the original text came through.
      const revertable = Boolean(before);

      return {
        ...f,
        path,
        before,
        after,
        rejected,
        revertable,
        finalCode: rejected && revertable ? before : after,
        changed: f.changed ?? (Boolean(after) && after !== before),
        diff_rows: f.diff_rows || buildDiffRows(before, after),
      };
    });
  }, [files, refactoredCode, diffRows, sctva]);

  // Files the developer accepted in the Transformation stage are pre-selected
  // for the commit; rejected ones are reverted and have nothing to write.
  const [stagedFiles, setStagedFiles] = useState(() => {
    const initial = new Set();
    (files || []).forEach((f, idx) => {
      if (f.decision === "accept") initial.add(f.path || f.file || `file-${idx}`);
    });
    if (initial.size === 0 && files.length === 0 && refactoredCode) {
      initial.add(`refactored_code.${String(sctva?.language || "txt").toLowerCase()}`);
    }
    return initial;
  });

  const activeFile = fileEntries[selectedFileIndex] || fileEntries[0] || null;
  const acceptedEntries = fileEntries.filter((f) => !f.rejected && f.changed);
  const revertedEntries = fileEntries.filter((f) => f.rejected);
  const stagedEntries = fileEntries.filter((f) => stagedFiles.has(f.path));

  const toggleStaged = (path) => {
    setStagedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  // ── Validation, taken from the agent's own report ─────────────────────────
  const validation = activeFile?.validation || sctva?.validation || null;
  const components = activeFile?.confidence_components || sctva?.confidence_components || null;
  const safetyReport = activeFile?.safety_report || sctva?.safety_report || null;
  const confidence = sctva?.confidence_score ?? activeFile?.confidence_score ?? null;
  const invariantDetails = validation?.invariant?.details || null;
  const invariantRecords = invariantDetails?.invariants || [];

  const appliedReplacements = acceptedEntries.reduce(
    (sum, f) => sum + (Number(f.total_replacements) || 0),
    0
  );

  // ── Commit actions — always write finalCode, never the rejected output ────
  const saveTextFile = (filename, content) => {
    try {
      const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1500);
    } catch (e) {
      console.error("Failed to save file", e);
    }
  };

  const decisionPayload = () => ({
    acceptedFiles: stagedEntries.map((f) => ({ ...f, after: f.finalCode })),
    revertedFiles: revertedEntries.map((f) => f.path),
  });

  const handleAcceptAndDownload = () => {
    if (stagedEntries.length === 0) {
      alert("Select at least one file to write before downloading.");
      return;
    }

    stagedEntries.forEach((f, i) => {
      const filename = f.path ? f.path.split("/").pop() : `refactored_${i + 1}.txt`;
      saveTextFile(filename, f.finalCode);
    });

    saveTextFile(
      "diwo_refactored_metadata.json",
      JSON.stringify(
        {
          savedAt: new Date().toISOString(),
          request_id: sctva?.request_id || null,
          confidence_score: sctva?.confidence_score ?? null,
          written: stagedEntries.map((f) => ({
            path: f.path,
            state: f.rejected ? "reverted_to_original" : "refactored",
          })),
          reverted: revertedEntries.map((f) => f.path),
        },
        null,
        2
      )
    );

    setShowActionChoice(false);
    if (onAccept) onAccept(decisionPayload());
  };

  const handlePushToGitHub = async () => {
    if (!branchName.trim()) {
      alert("Please enter a branch name");
      return;
    }
    if (!repoPath || repoPath.trim() === "") {
      alert("Repository path is required. Please provide the path to your Git repository.");
      return;
    }
    if (stagedEntries.length === 0) {
      alert("Select at least one file to write before sharing to GitHub Desktop.");
      return;
    }

    setIsProcessing(true);
    try {
      const response = await fetch("/api/diwo/apply-and-push", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          // `after` is what the backend writes to disk, so hand it finalCode:
          // a rejected file is written back as its original source.
          files: stagedEntries.map((f) => ({ path: f.path, after: f.finalCode })),
          branch_name: branchName,
          repository_path: repoPath.trim(),
        }),
      });

      if (!response.ok) {
        let errorMessage = `Backend error: ${response.statusText}`;
        try {
          const errorData = await response.json();
          errorMessage = errorData.error || errorMessage;
        } catch {
          errorMessage = `HTTP ${response.status}: ${response.statusText}`;
        }
        throw new Error(errorMessage);
      }

      const result = await response.json();
      const staged = result.staged_files || [];
      const repoOpened = result.github_desktop_opened ? "\n\nGitHub Desktop should be opening now." : "";
      let message = `✓ Branch '${branchName}' created and files applied to repository:\n${result.repository}${repoOpened}\n\n`;
      message +=
        staged.length > 0
          ? `Staged files (${staged.length}):\n${staged.map((s) => ` - ${s}`).join("\n")}` +
            "\n\nPlease review and commit in GitHub Desktop."
          : "No staged files were detected. Check repository path and file paths.";
      alert(message);

      setShowActionChoice(false);
      if (onAccept) onAccept({ ...decisionPayload(), githubResult: result });
    } catch (error) {
      console.error("Failed to push to GitHub:", error);
      alert(`Error: ${error.message}`);
    } finally {
      setIsProcessing(false);
    }
  };

  const statusColor = sctva?.rollback_occurred ? C.danger : sctva?.success ? C.accent : C.warn;

  return (
    <div>
      {/* ── Headline ──────────────────────────────────────────────────────── */}
      <Card glow={`${statusColor}20`} style={{ marginBottom: 20, textAlign: "center", padding: "24px" }}>
        <div style={{ fontSize: 48, fontWeight: 900, fontFamily: "monospace", color: statusColor }}>
          {typeof confidence === "number" ? pct(confidence, 0) : "N/A"}
        </div>
        <div style={{ fontSize: 13, color: C.textSub, marginTop: 2, marginBottom: 14 }}>
          Transformation Confidence Score
        </div>
        <div style={{ display: "flex", justifyContent: "center", gap: 10, flexWrap: "wrap" }}>
          <Pill
            label={sctva?.success ? "✓ Transformation Successful" : "⚠ Transformation Incomplete"}
            color={sctva?.success ? C.accent : C.warn}
          />
          <Pill
            label={sctva?.rollback_occurred ? "↩ Rolled Back by Agent" : "✓ No Agent Rollback"}
            color={sctva?.rollback_occurred ? C.danger : C.accent}
          />
          <Pill label={`${acceptedEntries.length} file(s) kept refactored`} color={C.accent} />
          <Pill
            label={`${revertedEntries.length} file(s) reverted to original`}
            color={revertedEntries.length ? C.danger : C.textMuted}
          />
        </div>
        {safetyReport?.summary && (
          <div style={{ marginTop: 14, fontSize: 12, color: C.textMuted }}>{safetyReport.summary}</div>
        )}
        {!sctva && (
          <div style={{ marginTop: 14, fontSize: 12, color: C.warn }}>
            No agent report attached — these results came from the DIWO backend's simulated
            transformation.
          </div>
        )}
      </Card>

      {/* ── Tabs ──────────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: `1px solid ${C.border}` }}>
        {[
          ["summary", "Summary"],
          ["validation", "Validation Report"],
          ["code", "Refactored Code"],
          ["invariants", "Invariants"],
        ].map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            style={{
              padding: "8px 16px",
              borderRadius: "8px 8px 0 0",
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
              border: "none",
              background: tab === key ? C.accent : "transparent",
              color: tab === key ? "#000" : C.textMuted,
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── Summary ───────────────────────────────────────────────────────── */}
      {tab === "summary" && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10 }}>
          {[
            ["Files Transformed", fileEntries.length, ""],
            ["Kept Refactored", acceptedEntries.length, "files"],
            ["Reverted to Original", revertedEntries.length, "files"],
            ["Replacements Applied", appliedReplacements, ""],
            ["Validation Score", pct(sctva?.validation_score), ""],
            ["Confidence Score", typeof confidence === "number" ? pct(confidence) : "N/A", ""],
          ].map(([label, value, unit]) => (
            <div key={label} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px" }}>
              <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 8 }}>
                {label}
              </div>
              <div style={{ fontSize: 22, fontWeight: 800, color: C.accent, fontFamily: "monospace" }}>
                {value}
                {unit ? ` ${unit}` : ""}
              </div>
            </div>
          ))}

          {components && (
            <div style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "14px 16px", gridColumn: "span 2" }}>
              <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 10 }}>
                Confidence Formula
              </div>
              <div style={{ fontSize: 11, color: C.textSub, fontFamily: "monospace", marginBottom: 10, wordBreak: "break-word" }}>
                {components.formula}
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                {COMPONENT_LABELS.filter(([key]) => key in components).map(([key, label]) => {
                  const weight = components.weights?.[label.toLowerCase()];
                  return (
                    <div key={key} style={{ background: C.bg, borderRadius: 6, padding: "8px 12px", textAlign: "center" }}>
                      <div style={{ fontSize: 16, fontWeight: 800, color: C.accent, fontFamily: "monospace" }}>
                        {pct(components[key], 2)}
                      </div>
                      <div style={{ fontSize: 10, color: C.textMuted }}>
                        {label}
                        {typeof weight === "number" ? ` (${(weight * 100).toFixed(0)}%)` : ""}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {revertedEntries.length > 0 && (
            <div style={{ background: C.panel, border: `1px solid ${C.danger}40`, borderRadius: 8, padding: "14px 16px", gridColumn: "span 2" }}>
              <div style={{ fontSize: 11, color: C.danger, textTransform: "uppercase", letterSpacing: 0.8, marginBottom: 8, fontWeight: 700 }}>
                Reverted files
              </div>
              <div style={{ fontSize: 12, color: C.textSub, marginBottom: 8 }}>
                Rejected in the Transformation stage. The original source is what this page shows
                and what any download or commit will write.
              </div>
              {revertedEntries.map((f) => (
                <div key={f.path} style={{ fontSize: 12, fontFamily: "monospace", color: C.textSub, padding: "2px 0" }}>
                  <span style={{ color: C.danger, marginRight: 8 }}>↩</span>
                  {f.path}
                  {!f.revertable && (
                    <span style={{ color: C.warn, marginLeft: 8 }}>
                      (original text unavailable — cannot revert)
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Validation report ─────────────────────────────────────────────── */}
      {tab === "validation" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {fileEntries.length > 1 && (
            <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 2 }}>
              Showing the report for <span style={{ color: C.text, fontFamily: "monospace" }}>{activeFile?.path}</span>.
              Pick another file in the Refactored Code tab to see its report.
            </div>
          )}

          {!validation && (
            <Card>
              <div style={{ fontSize: 12, color: C.textMuted }}>
                No validation report is attached to this result.
              </div>
            </Card>
          )}

          {validation &&
            Object.entries(VALIDATION_LABELS).map(([key, [label, blurb]]) => {
              const step = validation[key];
              if (!step) return null;
              const color = step.passed ? C.accent : C.danger;
              return (
                <div key={key} style={{ background: C.panel, border: `1px solid ${color}30`, borderRadius: 8, padding: "14px 18px", display: "flex", alignItems: "center", gap: 16 }}>
                  <div style={{ width: 36, height: 36, borderRadius: "50%", background: `${color}20`, border: `2px solid ${color}`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                    <span style={{ color, fontWeight: 900 }}>{step.passed ? "✓" : "✕"}</span>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 700, color: C.text, fontSize: 13 }}>{label}</div>
                    <div style={{ fontSize: 12, color: C.textSub, marginTop: 2 }}>{step.message || blurb}</div>
                    {typeof step.duration_ms === "number" && (
                      <div style={{ fontSize: 10, color: C.textMuted, marginTop: 4 }}>
                        completed in {step.duration_ms} ms
                      </div>
                    )}
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontSize: 18, fontWeight: 800, color, fontFamily: "monospace" }}>
                      {pct(step.score, 2)}
                    </div>
                    <Pill label={step.passed ? "PASSED" : "FAILED"} color={color} />
                  </div>
                </div>
              );
            })}

          {safetyReport?.transformation_log?.length > 0 && (
            <Card style={{ marginTop: 4 }}>
              <div style={{ fontSize: 11, color: C.textMuted, marginBottom: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
                Transformation Log
              </div>
              {safetyReport.transformation_log.map((entry) => (
                <div key={`log-${entry.action_index}`} style={{ padding: "6px 0", borderBottom: `1px solid ${C.border}` }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ fontSize: 11, color: C.textMuted, fontFamily: "monospace" }}>
                      #{entry.action_index}
                    </span>
                    <span style={{ fontSize: 12, color: C.text, fontFamily: "monospace" }}>
                      {entry.action_type}
                    </span>
                    <Badge
                      label={`${entry.replacements_count} replacement(s)`}
                      color={entry.replacements_count > 0 ? C.accent : C.textMuted}
                    />
                  </div>
                  {(entry.warnings || []).map((w, i) => (
                    <div key={`w-${i}`} style={{ fontSize: 11, color: C.warn, marginTop: 3, paddingLeft: 24 }}>
                      ⚑ {w}
                    </div>
                  ))}
                </div>
              ))}
            </Card>
          )}

          {safetyReport && (
            <Card style={{ marginTop: 4 }}>
              <div style={{ fontSize: 11, color: C.textMuted, marginBottom: 8, fontWeight: 700, textTransform: "uppercase", letterSpacing: 1 }}>
                Safety Messages
              </div>
              {safetyReport.rollback_reason && (
                <div style={{ fontSize: 12, color: C.danger, padding: "4px 0" }}>
                  ↩ {safetyReport.rollback_reason}
                </div>
              )}
              {(safetyReport.risk_flags || []).map((flag) => (
                <div key={flag} style={{ fontSize: 12, color: C.warn, padding: "4px 0" }}>
                  ⚑ {flag}
                </div>
              ))}
              {(safetyReport.human_messages || []).map((msg, i) => (
                <div key={`m-${i}`} style={{ fontSize: 12, color: C.textSub, padding: "4px 0" }}>
                  <span style={{ color: C.accent, marginRight: 8 }}>→</span>
                  {msg}
                </div>
              ))}
            </Card>
          )}
        </div>
      )}

      {/* ── Refactored code ───────────────────────────────────────────────── */}
      {tab === "code" && (
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <div style={{ width: 270, flexShrink: 0, maxHeight: 460, overflow: "auto", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 8 }}>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8, padding: "0 4px" }}>
              Files ({fileEntries.length})
            </div>
            {fileEntries.length === 0 && (
              <div style={{ color: C.textMuted, fontSize: 12, padding: 8 }}>No files available.</div>
            )}
            {fileEntries.map((f, idx) => {
              const isStaged = stagedFiles.has(f.path);
              const rail = f.rejected ? C.danger : C.accent;
              return (
                <div
                  key={f.path}
                  onClick={() => setSelectedFileIndex(idx)}
                  style={{
                    padding: 10,
                    borderRadius: 6,
                    cursor: "pointer",
                    marginBottom: 6,
                    background: selectedFileIndex === idx ? `${C.accent}12` : "transparent",
                    border: `1px solid ${selectedFileIndex === idx ? C.accent : C.border}`,
                    borderLeft: `3px solid ${rail}`,
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 700, color: C.text }}>
                    {f.path.split("/").pop()}
                  </div>
                  <div style={{ fontSize: 10, color: C.textMuted, marginTop: 3, wordBreak: "break-all" }}>
                    {f.path}
                  </div>
                  <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <Badge
                      label={f.rejected ? "reverted" : f.changed ? "refactored" : "unchanged"}
                      color={f.rejected ? C.danger : f.changed ? C.accent : C.textMuted}
                    />
                  </div>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleStaged(f.path);
                    }}
                    style={{
                      marginTop: 8,
                      width: "100%",
                      padding: "5px 10px",
                      borderRadius: 6,
                      cursor: "pointer",
                      border: `1px solid ${isStaged ? C.accent : C.border}`,
                      background: isStaged ? C.accent : "transparent",
                      color: isStaged ? "#000" : C.textMuted,
                      fontSize: 11,
                      fontWeight: 700,
                    }}
                  >
                    {isStaged ? "✓ Will be written" : "Write this file"}
                  </button>
                </div>
              );
            })}
          </div>

          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, gap: 12, flexWrap: "wrap" }}>
              <div style={{ fontSize: 12, color: C.textSub, fontFamily: "monospace", wordBreak: "break-all" }}>
                {activeFile?.path || "No file"}
                {activeFile?.language ? ` · ${String(activeFile.language).toUpperCase()}` : ""}
              </div>
              <div style={{ display: "flex", gap: 4 }}>
                {[
                  ["final", activeFile?.rejected ? "Final Code (original)" : "Final Code"],
                  ["diff", "Diff"],
                  ["before", "Original"],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    onClick={() => setView(key)}
                    style={{
                      padding: "6px 14px",
                      borderRadius: 6,
                      fontSize: 11,
                      fontWeight: 700,
                      cursor: "pointer",
                      border: `1px solid ${view === key ? C.accent : C.border}`,
                      background: view === key ? C.accent : "transparent",
                      color: view === key ? "#000" : C.textMuted,
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>

            {activeFile?.rejected && (
              <div style={{ marginBottom: 10, padding: "10px 14px", borderRadius: 8, background: `${C.danger}10`, border: `1px solid ${C.danger}40`, fontSize: 12, color: C.textSub }}>
                <span style={{ color: C.danger, fontWeight: 700 }}>↩ Reverted. </span>
                {activeFile.revertable
                  ? "This refactoring was rejected, so the original source is shown and is what will be written. The Diff tab still shows the change that was declined."
                  : "This refactoring was rejected, but the original source did not reach this page, so it cannot be reverted here."}
              </div>
            )}

            {view === "diff" && <DiffLegend />}
            {view === "diff" && activeFile?.rejected && (
              <div style={{ fontSize: 11, color: C.warn, marginBottom: 8 }}>
                Declined change — shown for reference only; it will not be written.
              </div>
            )}

            <CodeSurface>
              {view === "diff" ? (
                <DiffBlock rows={activeFile?.diff_rows} />
              ) : (
                <CodeBlock
                  code={view === "before" ? activeFile?.before : activeFile?.finalCode}
                  emptyMessage={
                    view === "before"
                      ? "Original source is not available for this file."
                      : "No code available for this file."
                  }
                />
              )}
            </CodeSurface>
          </div>
        </div>
      )}

      {/* ── Invariants ────────────────────────────────────────────────────── */}
      {tab === "invariants" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 12, color: C.textSub, marginBottom: 4 }}>
            {invariantDetails?.summary ||
              "Program invariants mined from the original and the transformed source."}
          </div>

          {invariantRecords.length === 0 && (
            <Card>
              <div style={{ fontSize: 12, color: C.textMuted }}>
                {invariantDetails
                  ? `No individual invariants were recorded (status: ${invariantDetails.status || "unknown"}).`
                  : "No invariant report is attached to this result."}
              </div>
            </Card>
          )}

          {invariantRecords.map((inv, idx) => {
            const preserved = inv.status === "preserved" || inv.preserved;
            const skipped = inv.status === "skipped";
            const color = skipped ? C.textMuted : preserved ? C.accent : C.danger;
            return (
              <div key={`${inv.name}-${idx}`} style={{ background: C.panel, border: `1px solid ${color}25`, borderRadius: 8, padding: "12px 16px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ color }}>{skipped ? "–" : preserved ? "✓" : "✕"}</span>
                  <span style={{ fontSize: 12, fontFamily: "monospace", color: C.textSub }}>{inv.name}</span>
                  <Pill
                    label={skipped ? "SKIPPED" : preserved ? "PRESERVED" : "VIOLATED"}
                    color={color}
                  />
                  {inv.critical && !preserved && <Badge label="critical" color={C.danger} />}
                </div>
                {inv.reason && (
                  <div style={{ fontSize: 11, color: C.textMuted, marginTop: 6 }}>{inv.reason}</div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── Actions ───────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 12, marginTop: 24, justifyContent: "space-between", alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontSize: 12, color: C.textSub }}>
          Files to write: <span style={{ color: C.accent, fontWeight: 700 }}>{stagedEntries.length}</span> / {fileEntries.length}
          {revertedEntries.length > 0 && (
            <span style={{ color: C.danger, marginLeft: 10 }}>
              ({revertedEntries.length} reverted to original)
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <button onClick={onRollback} style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.danger}15`, color: C.danger, border: `1px solid ${C.danger}30` }}>
            ↩ Request Rollback
          </button>
          <button onClick={onRestart} style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.info}15`, color: C.info, border: `1px solid ${C.info}30` }}>
            ↺ New Refactoring Session
          </button>
          <button onClick={() => setShowActionChoice(true)} style={{ padding: "10px 24px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: C.accent, color: "#000", border: "none", boxShadow: `0 0 20px ${C.accentGlow}` }}>
            ✓ Accept &amp; Commit Changes
          </button>
        </div>
      </div>

      {showActionChoice && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: 12, padding: 24, maxWidth: 480, boxShadow: "0 20px 60px rgba(0,0,0,0.3)" }}>
            <h2 style={{ marginTop: 0, color: C.text, marginBottom: 12 }}>How would you like to proceed?</h2>
            <p style={{ color: C.textSub, marginBottom: 8 }}>
              {stagedEntries.length} file(s) will be written.
            </p>
            {revertedEntries.some((f) => stagedFiles.has(f.path)) && (
              <p style={{ color: C.warn, fontSize: 12, marginBottom: 16 }}>
                Rejected files in this set are written back as their original source.
              </p>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 16 }}>
              <div
                onClick={handleAcceptAndDownload}
                style={{ background: C.panel, border: `2px solid ${C.border}`, borderRadius: 8, padding: 16, cursor: "pointer", transition: "all 0.2s" }}
                onMouseEnter={(e) => (e.currentTarget.style.borderColor = C.accent)}
                onMouseLeave={(e) => (e.currentTarget.style.borderColor = C.border)}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{ fontSize: 24 }}>💾</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, color: C.text, marginBottom: 4 }}>Download to Local Device</div>
                    <div style={{ fontSize: 12, color: C.textMuted }}>
                      Save the final files to your downloads folder. You manage the repository yourself.
                    </div>
                  </div>
                </div>
              </div>

              <div style={{ background: C.panel, border: `2px solid ${C.border}`, borderRadius: 8, padding: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                  <div style={{ fontSize: 24 }}>🚀</div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 700, color: C.text }}>Push to GitHub</div>
                    <div style={{ fontSize: 12, color: C.textMuted }}>
                      Create a branch and open GitHub Desktop for review &amp; commit.
                    </div>
                  </div>
                </div>
                <div style={{ marginBottom: 10 }}>
                  <label style={{ fontSize: 12, color: C.textMuted, display: "block", marginBottom: 4 }}>Repository Path</label>
                  <input
                    type="text"
                    value={repoPath}
                    onChange={(e) => setRepoPath(e.target.value)}
                    placeholder="C:\\Users\\YourUser\\path\\to\\repo"
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.bg, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, marginBottom: 10, boxSizing: "border-box" }}
                  />
                </div>
                <div style={{ marginBottom: 10 }}>
                  <label style={{ fontSize: 12, color: C.textMuted, display: "block", marginBottom: 4 }}>Branch Name</label>
                  <input
                    type="text"
                    value={branchName}
                    onChange={(e) => setBranchName(e.target.value)}
                    placeholder="e.g., refactoring/diwo-changes"
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.bg, border: `1px solid ${C.border}`, color: C.text, fontSize: 12, marginBottom: 10, boxSizing: "border-box" }}
                  />
                </div>
                <button
                  onClick={handlePushToGitHub}
                  disabled={isProcessing}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.accent, color: "#000", border: "none", fontWeight: 700, fontSize: 12, cursor: isProcessing ? "not-allowed" : "pointer", opacity: isProcessing ? 0.6 : 1 }}
                >
                  {isProcessing ? "Processing..." : "✓ Create Branch & Open GitHub Desktop"}
                </button>
              </div>

              <button
                onClick={() => setShowActionChoice(false)}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: C.border, color: C.text, border: "none", fontWeight: 700, fontSize: 12, cursor: "pointer" }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
