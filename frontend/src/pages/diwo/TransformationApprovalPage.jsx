/**
 * TransformationApprovalPage.jsx
 * ==============================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * Stage 3 of the DIWO workflow. The approved refactoring plan is executed by
 * the Safe Code Transformation & Validation Agent
 * (POST http://localhost:8002/sctva/execute) and the code it returns is what
 * this page renders — the refactored source, the diff against the original,
 * the confidence score and the four validation stages behind it.
 *
 * The DIWO backend also returns a transformation result when the plan is
 * approved, but that one is a simulation. It is kept only as the fallback the
 * developer can fall through to when SCTVA is not running, and it is labelled
 * as such rather than shown as if the agent had produced it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, C, Card, Pill } from "./diwoTheme.jsx";
import { CodeBlock, CodeSurface, DiffBlock, DiffLegend } from "./diffView.jsx";
import { SCTVA_BASE, buildDiffRows, executeTransformation } from "./sctvaApi";

const STAGE_LABELS = {
  mapping: {
    label: "Preparing Transformation Request",
    detail: "Mapping approved plan steps onto SCTVA actions...",
    progress: 12,
  },
  sources: {
    label: "Importing Source Files",
    detail: "Reading original file contents from the CUQA workspace...",
    progress: 32,
  },
  executing: {
    label: "Applying & Validating Transformation",
    detail: "AST transformation, syntax, structural, behavioral and invariant checks...",
    progress: 70,
  },
  complete: {
    label: "Transformation Complete",
    detail: "Safety report and confidence score received.",
    progress: 100,
  },
};

const VALIDATION_ORDER = [
  ["syntax", "Syntax"],
  ["structural", "Structural"],
  ["behavioral", "Behavioral"],
  ["invariant", "Invariant"],
];

const BLANK_RUN = { stage: "mapping", progress: 0, run: null, error: null };

const pct = (value) =>
  typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";

/** Accept / Reject pair for one changed file. */
function DecisionButtons({ decision, onDecide, size = "sm" }) {
  const pad = size === "sm" ? "4px 10px" : "7px 16px";
  const font = size === "sm" ? 10 : 12;

  const button = (value, label, color) => {
    const active = decision === value;
    return (
      <button
        onClick={(e) => {
          e.stopPropagation();
          onDecide(active ? null : value);
        }}
        style={{
          padding: pad,
          borderRadius: 6,
          fontSize: font,
          fontWeight: 700,
          cursor: "pointer",
          border: `1px solid ${active ? color : C.border}`,
          background: active ? color : "transparent",
          color: active ? "#000" : color,
        }}
      >
        {label}
      </button>
    );
  };

  return (
    <div style={{ display: "flex", gap: 6 }}>
      {button("accept", "✓ Accept", C.accent)}
      {button("reject", "✕ Reject", C.danger)}
    </div>
  );
}

/**
 * Accept / Reject applied to every changed file at once.
 *
 * Accept All keeps the whole transformation; Reject All falls the entire set
 * back to the original sources. Both are just a shortcut for setting the same
 * per-file verdict on every file, so a single file can still be flipped
 * afterwards without losing the rest.
 */
function BulkDecisionBar({ total, accepted, rejected, pending, onAcceptAll, onRejectAll, onClear }) {
  const allAccepted = total > 0 && accepted === total;
  const allRejected = total > 0 && rejected === total;

  const railColor = allAccepted ? C.accent : allRejected ? C.danger : pending > 0 ? C.warn : C.info;

  const message = allAccepted
    ? `All ${total} refactored file(s) accepted — every change is carried forward.`
    : allRejected
      ? `All ${total} refactored file(s) rejected — every file falls back to its original source.`
      : pending === 0
        ? `Mixed decision — ${accepted} file(s) kept refactored, ${rejected} reverted to original.`
        : `${pending} of ${total} refactored file(s) still need a decision.`;

  const bulkButton = (label, color, active, onClick) => (
    <button
      onClick={onClick}
      style={{
        padding: "8px 18px",
        borderRadius: 8,
        fontSize: 12,
        fontWeight: 700,
        cursor: "pointer",
        border: `1px solid ${active ? color : C.border}`,
        background: active ? color : "transparent",
        color: active ? "#000" : color,
      }}
    >
      {label}
    </button>
  );

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 14,
        flexWrap: "wrap",
        marginBottom: 16,
        padding: "12px 16px",
        borderRadius: 10,
        background: C.panel,
        border: `1px solid ${C.border}`,
        borderLeft: `3px solid ${railColor}`,
      }}
    >
      <div>
        <div style={{ fontSize: 12, fontWeight: 700, color: C.text, marginBottom: 3 }}>
          Decide on all files at once
        </div>
        <div style={{ fontSize: 12, color: railColor }}>{message}</div>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {bulkButton("✓ Accept All", C.accent, allAccepted, onAcceptAll)}
        {bulkButton("✕ Reject All", C.danger, allRejected, onRejectAll)}
        {accepted + rejected > 0 && (
          <button
            onClick={onClear}
            style={{
              padding: "8px 14px",
              borderRadius: 8,
              fontSize: 12,
              fontWeight: 700,
              cursor: "pointer",
              border: `1px solid ${C.border}`,
              background: "transparent",
              color: C.textMuted,
            }}
          >
            Clear
          </button>
        )}
      </div>
    </div>
  );
}

export default function TransformationApprovalPage({
  onComplete,
  transformationData,
  plan,
  language = "java",
}) {
  // One object so a new run replaces the previous one atomically, from inside
  // the agent's own progress callback — no reset pass in the effect body.
  const [runState, setRunState] = useState(BLANK_RUN);
  const [attempt, setAttempt] = useState(0);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [view, setView] = useState("after");
  // "accept" | "reject" per changed file path; every changed file needs one
  // before the workflow can move on.
  const [decisions, setDecisions] = useState({});

  // Set when the developer chooses to continue on the DIWO backend's simulated
  // result after SCTVA failed, so the page can say which one is on screen.
  const [usingFallback, setUsingFallback] = useState(false);

  const { stage, progress, run, error } = runState;

  // ── Run the agent ─────────────────────────────────────────────────────────
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;

    (async () => {
      try {
        const outcome = await executeTransformation({
          plan,
          language,
          onStage: (next) => {
            if (!alive) return;
            // "mapping" opens every run, so it doubles as the point where the
            // previous run's result and error are cleared.
            if (next === "mapping") {
              setRunState({ ...BLANK_RUN, stage: next });
              setSelectedIndex(0);
              setUsingFallback(false);
              setDecisions({});
            } else {
              setRunState((prev) => ({ ...prev, stage: next }));
            }
          },
          signal: controller.signal,
        });
        if (!alive) return;
        setRunState((prev) => ({ ...prev, run: outcome, progress: 100 }));
      } catch (e) {
        if (!alive || e.name === "AbortError") return;
        setRunState((prev) => ({ ...prev, error: e }));
      }
    })();

    return () => {
      alive = false;
      controller.abort();
    };
  }, [plan, language, attempt]);

  // Creep the ring towards the current stage's ceiling so a slow execute still
  // looks alive; the real stage transitions set the ceiling.
  useEffect(() => {
    if (run || error) return undefined;
    const ceiling = STAGE_LABELS[stage]?.progress ?? 90;
    const timer = setInterval(() => {
      setRunState((prev) =>
        prev.progress >= ceiling ? prev : { ...prev, progress: Math.min(prev.progress + 1, ceiling) }
      );
    }, 40);
    return () => clearInterval(timer);
  }, [stage, run, error]);

  const done = Boolean(run);
  const result = run?.result || null;

  const files = useMemo(() => {
    if (result) return result.files || [];
    if (!usingFallback) return [];

    // Fallback: whatever the DIWO backend simulated at plan-approval time.
    const backendFiles = transformationData?.files || [];
    if (backendFiles.length > 0) {
      return backendFiles.map((f) => {
        const before = f.before || "";
        const after = f.after || f.refactored_code || "";
        return {
          ...f,
          path: f.path || f.file || "source",
          before,
          after,
          diff_rows: f.diff_rows || buildDiffRows(before, after),
          changed: Boolean(after) && after !== before,
        };
      });
    }
    if (!transformationData?.refactored_code) return [];
    return [
      {
        path: "refactored_code",
        before: "",
        after: transformationData.refactored_code,
        diff_rows: transformationData.diff_rows || [],
        changed: true,
      },
    ];
  }, [result, usingFallback, transformationData]);

  const activeFile = files[selectedIndex] || files[0] || null;

  const validation = useMemo(() => {
    const source = activeFile?.validation || result?.validation || null;
    return VALIDATION_ORDER.map(([key, label]) => ({
      key,
      label,
      step: source?.[key] || null,
    }));
  }, [activeFile, result]);

  const safetyReport = activeFile?.safety_report || result?.safetyReport || null;

  const confidence = activeFile?.confidence_score ?? result?.confidenceScore ?? null;

  // ── Per-file accept / reject ──────────────────────────────────────────────
  const changedFiles = useMemo(() => files.filter((f) => f.changed), [files]);

  const decide = useCallback((path, value) => {
    setDecisions((prev) => {
      const next = { ...prev };
      if (value === null) delete next[path];
      else next[path] = value;
      return next;
    });
  }, []);

  // Bulk verdicts are the same per-file verdict written across every changed
  // file, so an individual file can still be flipped afterwards.
  const decideAll = useCallback(
    (value) => {
      setDecisions(
        value === null
          ? {}
          : Object.fromEntries(changedFiles.map((f) => [f.path, value]))
      );
    },
    [changedFiles]
  );

  const acceptedPaths = changedFiles.filter((f) => decisions[f.path] === "accept").map((f) => f.path);
  const rejectedPaths = changedFiles.filter((f) => decisions[f.path] === "reject").map((f) => f.path);
  const pendingCount = changedFiles.length - acceptedPaths.length - rejectedPaths.length;

  const decoratedFiles = useMemo(
    () => files.map((f) => ({ ...f, decision: f.changed ? decisions[f.path] || null : null })),
    [files, decisions]
  );

  // ── Hand the result to the workflow ───────────────────────────────────────
  const handleContinue = useCallback(() => {
    if (result) {
      // Lead with an accepted file so the Results stage opens on code the
      // developer actually kept.
      const lead =
        decoratedFiles.find((f) => f.decision === "accept") ||
        decoratedFiles.find((f) => f.changed) ||
        decoratedFiles[0] ||
        null;

      // With everything rejected there is no accepted lead, so the top-level
      // code must be the original — otherwise "Reject All" would still hand
      // the refactored source downstream.
      const leadReverted = lead?.decision === "reject" && Boolean(lead?.before);
      const leadCode = leadReverted ? lead.before : lead?.after || result.refactored_code;

      onComplete({
        refactored_code: leadCode,
        diff_rows: leadReverted ? [] : lead?.diff_rows || result.diff_rows,
        files: decoratedFiles,
        accepted_files: acceptedPaths,
        rejected_files: rejectedPaths,
        // Extra SCTVA context so the Results and Comparison stages can report
        // what the agent actually did rather than re-deriving it.
        sctva: {
          request_id: result.requestId,
          language: result.language,
          success: result.success,
          rollback_occurred: result.rollbackOccurred,
          transformation_applied: result.transformationApplied,
          confidence_score: result.confidenceScore,
          confidence_components: result.confidenceComponents,
          validation_score: result.validationScore,
          total_replacements: result.totalReplacements,
          file_summary: result.fileSummary,
          validation: result.validation,
          safety_report: result.safetyReport,
          plan_mapping: run?.mapping || null,
          missing_sources: run?.sources?.missing || [],
          executed_at: run?.executedAt || null,
          agent_url: run?.sctvaUrl || SCTVA_BASE,
        },
      });
      return;
    }

    onComplete({
      refactored_code: transformationData?.refactored_code || "",
      diff_rows: transformationData?.diff_rows || [],
      files: decoratedFiles.length > 0 ? decoratedFiles : transformationData?.files || [],
      accepted_files: acceptedPaths,
      rejected_files: rejectedPaths,
      sctva: null,
    });
  }, [result, run, onComplete, transformationData, decoratedFiles, acceptedPaths, rejectedPaths]);

  // ── Error state ───────────────────────────────────────────────────────────
  if (error && !usingFallback) {
    const hasBackendFallback = Boolean(
      transformationData?.refactored_code || (transformationData?.files || []).length
    );

    return (
      <div>
        <Card glow={C.dangerGlow} style={{ borderColor: `${C.danger}40` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <span style={{ fontSize: 20 }}>⚠</span>
            <div style={{ fontSize: 15, fontWeight: 800, color: C.danger }}>
              Transformation could not be executed
            </div>
          </div>
          <div style={{ fontSize: 13, color: C.textSub, lineHeight: 1.6 }}>{error.message}</div>

          {error.details?.missing?.length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 6 }}>
                Files SCTVA could not read ({error.details.missing.length})
              </div>
              <div style={{ maxHeight: 140, overflow: "auto", background: C.bg, border: `1px solid ${C.border}`, borderRadius: 8, padding: 10 }}>
                {error.details.missing.map((path) => (
                  <div key={path} style={{ fontSize: 11, fontFamily: "monospace", color: C.textSub, padding: "2px 0" }}>
                    {path}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div style={{ marginTop: 16, fontSize: 11, color: C.textMuted }}>
            Agent endpoint: <span style={{ fontFamily: "monospace" }}>{SCTVA_BASE}/sctva/execute</span>
          </div>

          <div style={{ display: "flex", gap: 12, marginTop: 20, flexWrap: "wrap" }}>
            <button
              onClick={() => setAttempt((n) => n + 1)}
              style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: C.accent, color: "#000", border: "none" }}
            >
              ↻ Retry Transformation
            </button>
            {hasBackendFallback && (
              <button
                onClick={() => setUsingFallback(true)}
                style={{ padding: "10px 20px", borderRadius: 8, fontWeight: 700, fontSize: 13, cursor: "pointer", background: `${C.warn}15`, color: C.warn, border: `1px solid ${C.warn}30` }}
              >
                Continue with the DIWO backend's simulated result
              </button>
            )}
          </div>
        </Card>
      </div>
    );
  }

  // ── Running state ─────────────────────────────────────────────────────────
  if (!done && !usingFallback) {
    const current = STAGE_LABELS[stage] || STAGE_LABELS.mapping;

    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "40px 0" }}>
        <div style={{ position: "relative", width: 160, height: 160, marginBottom: 32 }}>
          <svg width="160" height="160" style={{ transform: "rotate(-90deg)" }}>
            <circle cx="80" cy="80" r="68" fill="none" stroke={C.border} strokeWidth="8" />
            <circle
              cx="80"
              cy="80"
              r="68"
              fill="none"
              stroke={C.accent}
              strokeWidth="8"
              strokeDasharray={`${2 * Math.PI * 68}`}
              strokeDashoffset={`${2 * Math.PI * 68 * (1 - progress / 100)}`}
              strokeLinecap="round"
              style={{ transition: "stroke-dashoffset 0.15s" }}
            />
          </svg>
          <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
            <div style={{ fontSize: 32, fontWeight: 900, color: C.text, fontFamily: "monospace" }}>{progress}%</div>
            <div style={{ fontSize: 10, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1 }}>Running</div>
          </div>
        </div>

        <div style={{ fontSize: 15, fontWeight: 700, color: C.text, marginBottom: 6, textAlign: "center" }}>
          {current.label}
        </div>
        <div style={{ fontSize: 12, color: C.textMuted, marginBottom: 32, textAlign: "center" }}>
          {current.detail}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 10, width: "100%", maxWidth: 480 }}>
          {VALIDATION_ORDER.map(([key, label]) => (
            <div key={key} style={{ background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: "12px 16px", display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ width: 28, height: 28, borderRadius: "50%", background: C.bg, border: `2px solid ${C.border}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <span style={{ color: C.border, fontSize: 14 }}>…</span>
              </div>
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: C.textMuted }}>{label}</div>
                <div style={{ fontSize: 11, color: C.textMuted }}>Pending</div>
              </div>
            </div>
          ))}
        </div>

        <div style={{ marginTop: 24, fontSize: 11, color: C.textMuted, textAlign: "center" }}>
          Safe Code Transformation & Validation Agent · {SCTVA_BASE}/sctva/execute
        </div>
      </div>
    );
  }

  // ── Result state ──────────────────────────────────────────────────────────
  const statusColor = usingFallback
    ? C.warn
    : result?.rollbackOccurred
      ? C.danger
      : result?.success
        ? C.accent
        : C.warn;

  return (
    <div>
      {usingFallback && (
        <Card style={{ marginBottom: 16, borderColor: `${C.warn}40`, padding: "14px 18px" }}>
          <div style={{ fontSize: 12, color: C.warn, fontWeight: 700 }}>
            ⚠ Simulated result — the Safe Transformation Agent did not run
          </div>
          <div style={{ fontSize: 12, color: C.textSub, marginTop: 4 }}>
            The code below comes from the DIWO backend's placeholder transformation, not from
            SCTVA. Start the agent and retry to get a validated transformation.
          </div>
          <button
            onClick={() => { setUsingFallback(false); setAttempt((n) => n + 1); }}
            style={{ marginTop: 12, padding: "8px 16px", borderRadius: 8, fontWeight: 700, fontSize: 12, cursor: "pointer", background: `${C.accent}15`, color: C.accent, border: `1px solid ${C.accent}30` }}
          >
            ↻ Retry with SCTVA
          </button>
        </Card>
      )}

      {result && (
        <Card glow={`${statusColor}20`} style={{ marginBottom: 20, textAlign: "center", padding: "24px" }}>
          <div style={{ fontSize: 48, fontWeight: 900, fontFamily: "monospace", color: statusColor }}>
            {result.confidenceApplicable && typeof confidence === "number"
              ? `${(confidence * 100).toFixed(0)}%`
              : "N/A"}
          </div>
          <div style={{ fontSize: 13, color: C.textSub, marginTop: 2, marginBottom: 14 }}>
            Transformation Confidence Score
          </div>
          <div style={{ display: "flex", justifyContent: "center", gap: 10, flexWrap: "wrap" }}>
            <Pill
              label={result.success ? "✓ Transformation Successful" : "⚠ Transformation Incomplete"}
              color={result.success ? C.accent : C.warn}
            />
            <Pill
              label={result.rollbackOccurred ? "↩ Rollback Performed" : "✓ No Rollback Required"}
              color={result.rollbackOccurred ? C.danger : C.accent}
            />
            <Pill label={`${result.totalReplacements} replacement(s)`} color={C.info} />
            <Pill
              label={`${result.fileSummary?.applied ?? 0}/${result.fileSummary?.total ?? files.length} file(s) changed`}
              color={C.info}
            />
          </div>
          {safetyReport?.summary && (
            <div style={{ marginTop: 14, fontSize: 12, color: C.textMuted }}>{safetyReport.summary}</div>
          )}
        </Card>
      )}

      {result && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 10, marginBottom: 20 }}>
          {validation.map(({ key, label, step }) => {
            const passed = step?.passed;
            const color = step ? (passed ? C.accent : C.danger) : C.textMuted;
            return (
              <div key={key} style={{ background: C.panel, border: `1px solid ${color}40`, borderRadius: 8, padding: "12px 14px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <span style={{ color, fontWeight: 900 }}>{step ? (passed ? "✓" : "✕") : "—"}</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: C.text }}>{label}</span>
                </div>
                <div style={{ fontSize: 16, fontWeight: 800, color, fontFamily: "monospace" }}>
                  {pct(step?.score)}
                </div>
                {step?.message && (
                  <div style={{ fontSize: 10, color: C.textMuted, marginTop: 6, lineHeight: 1.45 }}>
                    {step.message}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ── Accept / reject every file at once ──────────────────────────── */}
      {changedFiles.length > 1 && (
        <BulkDecisionBar
          total={changedFiles.length}
          accepted={acceptedPaths.length}
          rejected={rejectedPaths.length}
          pending={pendingCount}
          onAcceptAll={() => decideAll("accept")}
          onRejectAll={() => decideAll("reject")}
          onClear={() => decideAll(null)}
        />
      )}

      {/* ── Refactored code ─────────────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        {files.length > 1 && (
          <div style={{ width: 240, flexShrink: 0, maxHeight: 460, overflow: "auto", background: C.panel, border: `1px solid ${C.border}`, borderRadius: 8, padding: 8 }}>
            <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, marginBottom: 8, padding: "0 4px" }}>
              Files ({files.length})
            </div>
            {files.map((f, idx) => {
              const decision = decisions[f.path] || null;
              const railColor =
                decision === "accept" ? C.accent : decision === "reject" ? C.danger : C.border;
              return (
                <div
                  key={f.path || idx}
                  onClick={() => setSelectedIndex(idx)}
                  style={{
                    padding: 10,
                    borderRadius: 6,
                    cursor: "pointer",
                    marginBottom: 6,
                    background: selectedIndex === idx ? `${C.accent}12` : "transparent",
                    color: C.text,
                    border: `1px solid ${selectedIndex === idx ? C.accent : C.border}`,
                    borderLeft: `3px solid ${railColor}`,
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 700 }}>{String(f.path).split("/").pop()}</div>
                  <div style={{ fontSize: 10, marginTop: 3, wordBreak: "break-all", color: C.textMuted }}>
                    {f.path}
                  </div>
                  <div style={{ marginTop: 6, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                    <Badge
                      label={f.changed ? "changed" : "unchanged"}
                      color={f.changed ? C.accent : C.textMuted}
                    />
                    {decision && (
                      <Badge
                        label={decision === "accept" ? "accepted" : "rejected"}
                        color={decision === "accept" ? C.accent : C.danger}
                      />
                    )}
                  </div>
                  {f.changed && (
                    <div style={{ marginTop: 8 }}>
                      <DecisionButtons decision={decision} onDecide={(v) => decide(f.path, v)} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10, gap: 12, flexWrap: "wrap" }}>
            <div style={{ fontSize: 12, color: C.textSub, fontFamily: "monospace", wordBreak: "break-all" }}>
              {activeFile?.path || "No file"}
              {activeFile?.language ? ` · ${String(activeFile.language).toUpperCase()}` : ""}
            </div>
            <div style={{ display: "flex", gap: 4 }}>
              {[["after", "Refactored Code"], ["diff", "Diff"], ["before", "Original"]].map(([key, label]) => (
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

          {activeFile?.changed && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12,
                flexWrap: "wrap",
                marginBottom: 10,
                padding: "10px 14px",
                borderRadius: 8,
                background: C.panel,
                border: `1px solid ${
                  decisions[activeFile.path] === "accept"
                    ? `${C.accent}55`
                    : decisions[activeFile.path] === "reject"
                      ? `${C.danger}55`
                      : C.border
                }`,
              }}
            >
              <div style={{ fontSize: 12, color: C.textSub }}>
                {decisions[activeFile.path] === "accept" ? (
                  <span style={{ color: C.accent, fontWeight: 700 }}>✓ Accepted — this file will be carried forward</span>
                ) : decisions[activeFile.path] === "reject" ? (
                  <span style={{ color: C.danger, fontWeight: 700 }}>✕ Rejected — this file is marked as not adopted</span>
                ) : (
                  <span>Accept or reject the refactoring of this file.</span>
                )}
              </div>
              <DecisionButtons
                size="md"
                decision={decisions[activeFile.path] || null}
                onDecide={(v) => decide(activeFile.path, v)}
              />
            </div>
          )}

          {result && activeFile && !activeFile.changed && view === "after" && (
            <div style={{ fontSize: 11, color: C.warn, marginBottom: 8 }}>
              {result.rollbackOccurred
                ? "Rolled back — validation failed, so SCTVA restored the original source for this file."
                : "SCTVA returned this file unchanged — no approved action produced a replacement in it."}
            </div>
          )}

          {view === "diff" && <DiffLegend />}

          <CodeSurface>
            {view === "diff" ? (
              <DiffBlock rows={activeFile?.diff_rows} />
            ) : (
              <CodeBlock
                code={view === "before" ? activeFile?.before : activeFile?.after}
                emptyMessage={
                  view === "before"
                    ? "Original source is not available for this file."
                    : "The agent returned no refactored code for this file."
                }
              />
            )}
          </CodeSurface>
        </div>
      </div>

      {/* ── Safety report ───────────────────────────────────────────────── */}
      {safetyReport && (safetyReport.human_messages?.length > 0 || safetyReport.risk_flags?.length > 0) && (
        <Card style={{ marginTop: 20 }}>
          <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700, marginBottom: 10 }}>
            Safety Report
          </div>
          {(safetyReport.risk_flags || []).map((flag) => (
            <div key={flag} style={{ fontSize: 12, color: C.warn, padding: "3px 0" }}>
              <span style={{ marginRight: 8 }}>⚑</span>
              {flag}
            </div>
          ))}
          {(safetyReport.human_messages || []).map((msg, i) => (
            <div key={`msg-${i}`} style={{ fontSize: 12, color: C.textSub, padding: "3px 0" }}>
              <span style={{ color: C.accent, marginRight: 8 }}>→</span>
              {msg}
            </div>
          ))}
        </Card>
      )}

      {run?.mapping?.warnings?.length > 0 && (
        <Card style={{ marginTop: 12 }}>
          <div style={{ fontSize: 11, color: C.textMuted, textTransform: "uppercase", letterSpacing: 1, fontWeight: 700, marginBottom: 4 }}>
            Plan mapping ({run.mapping.executableCount} executable, {run.mapping.noopCount} noop)
          </div>
          <div style={{ fontSize: 11, color: C.textMuted, marginBottom: 10 }}>
            Steps SCTVA could not execute as approved. They were sent as noops so the step count is preserved.
          </div>
          <div style={{ maxHeight: 150, overflow: "auto" }}>
            {run.mapping.warnings.map((w, i) => (
              <div key={`warn-${i}`} style={{ fontSize: 11, color: C.textSub, padding: "3px 0" }}>
                <span style={{ color: C.warn, marginRight: 8 }}>•</span>
                {w}
              </div>
            ))}
          </div>
        </Card>
      )}

      {run?.sources?.missing?.length > 0 && (
        <div style={{ marginTop: 12, fontSize: 11, color: C.warn }}>
          {run.sources.missing.length} planned file(s) had no readable source in the CUQA workspace and were skipped.
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 24, gap: 12, flexWrap: "wrap" }}>
        <div>
          {changedFiles.length > 0 && (
            <div style={{ fontSize: 12, color: C.textSub, marginBottom: 4 }}>
              <span style={{ color: C.accent, fontWeight: 700 }}>{acceptedPaths.length} accepted</span>
              {" · "}
              <span style={{ color: C.danger, fontWeight: 700 }}>{rejectedPaths.length} rejected</span>
              {" · "}
              <span style={{ color: pendingCount > 0 ? C.warn : C.textMuted, fontWeight: 700 }}>
                {pendingCount} pending
              </span>
              <span style={{ color: C.textMuted }}> of {changedFiles.length} changed file(s)</span>
            </div>
          )}
          <div style={{ fontSize: 11, color: C.textMuted }}>
            {result
              ? `Request ${result.requestId} · executed by SCTVA at ${run?.sctvaUrl || SCTVA_BASE}`
              : "Simulated by the DIWO backend"}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <button
            onClick={handleContinue}
            disabled={pendingCount > 0}
            style={{
              padding: "10px 24px",
              borderRadius: 8,
              fontWeight: 700,
              fontSize: 13,
              cursor: pendingCount > 0 ? "not-allowed" : "pointer",
              background: pendingCount > 0 ? C.border : C.accent,
              color: pendingCount > 0 ? C.textMuted : "#000",
              border: "none",
              boxShadow: pendingCount > 0 ? "none" : `0 0 20px ${C.accentGlow}`,
            }}
          >
            Continue to Results Review →
          </button>
          {pendingCount > 0 && (
            <div style={{ fontSize: 11, color: C.warn, marginTop: 6 }}>
              Accept or reject {pendingCount} more file(s) to continue.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
