/**
 * OverrideConfirmDialog.jsx
 * =========================
 * R26-SE-008 | Bandara S M Y M | IT22277886
 *
 * The one speed bump in Stage 2: approving a step DIWO marked Not Recommended,
 * or forcing a manual-only step through as an automatic transformation.
 *
 *     DIWO marked this step Not Recommended.
 *
 *     Why:
 *       ⚠ High transformation risk
 *       ⚠ Required SCTVA parameters are incomplete
 *
 *     Approve it anyway?          [Cancel]  [Approve anyway]
 *
 * It CONFIRMS, it does not block. The developer keeps final authority over the
 * plan, and a system that refuses an override is a system that has quietly
 * promoted its recommendation into a decision. What the dialog buys is that the
 * override is deliberate: on a page of twelve cards, a red step's Approve
 * button is two pixels from a green step's, and the difference between them is
 * the whole point of the stage.
 *
 * The reason chips are optional and nothing waits on them. A disagreement with
 * DIWO is the most informative feedback the system can collect — it is the only
 * signal that says the recommendation was wrong rather than unseen — and a
 * free-text box collects it least often, so the common answers are one click.
 */

import { useEffect, useState } from "react";
import { OVERRIDE_REASONS, categoryStyle, MANUAL_ONLY } from "../utils/planningDecisionSupport";
import { C } from "../diwoTheme.jsx";

export default function OverrideConfirmDialog({ step, support, reason, onReason, onConfirm, onCancel }) {
  const [picked, setPicked] = useState(reason || "");

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onCancel?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  if (!step) return null;

  const category = support?.category || null;
  const style = categoryStyle(category);
  const isManual = category === MANUAL_ONLY;

  // The dialog quotes the backend's own warnings rather than composing a new
  // justification: a reason invented here could contradict the score beside it.
  const warnings = (support?.warnings || []).slice(0, 4);
  const missing = support?.capability?.missing_requirements || [];

  const confirm = () => {
    onReason?.(step.step_id, picked);
    onConfirm?.(step.step_id);
  };

  return (
    <div
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-label={`Approve step ${step.step_id} against DIWO's recommendation`}
      style={{
        position: "fixed", inset: 0, zIndex: 1200,
        background: "rgba(4,6,10,0.74)", backdropFilter: "blur(2px)",
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(560px, 100%)", maxHeight: "86vh", overflowY: "auto",
          background: C.bg, border: `1px solid ${style.color}55`, borderRadius: 14,
          boxShadow: "0 24px 60px rgba(0,0,0,0.55)", padding: "20px 22px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 10 }}>
          <span aria-hidden="true" style={{ fontSize: 16 }}>{style.icon}</span>
          <span style={{ fontSize: 14, fontWeight: 800, color: style.color }}>
            DIWO marked this step {style.label}
          </span>
        </div>

        <div style={{ fontSize: 12.5, color: C.text, lineHeight: 1.6, marginBottom: 12 }}>
          <b>{step.smell_type ? `${step.smell_type} → ` : ""}{step.refactoring}</b>
          <span style={{ color: C.textMuted, fontFamily: "monospace", marginLeft: 8, fontSize: 11 }}>
            {step.target?.file || "(module level)"}
          </span>
        </div>

        {isManual ? (
          <div style={{
            fontSize: 12, color: C.textSub, lineHeight: 1.65, marginBottom: 14,
            padding: "10px 13px", borderRadius: 8,
            background: `${C.info}0c`, border: `1px solid ${C.info}40`,
          }}>
            <b style={{ color: C.info }}>SCTVA cannot execute this refactoring.</b>{" "}
            Approving it forwards a step the Transformation Agent has no automatic
            form for, so it is likely to produce no code change. To keep it on
            your list without sending it to SCTVA, cancel and choose{" "}
            <b style={{ color: C.textSub }}>Add to manual work</b> instead.
          </div>
        ) : (
          <div style={{
            fontSize: 12, color: C.textSub, lineHeight: 1.65, marginBottom: 14,
            padding: "10px 13px", borderRadius: 8,
            background: `${C.danger}0c`, border: `1px solid ${C.danger}40`,
          }}>
            {support?.summary || "The expected benefit does not cover the risk for this step."}
          </div>
        )}

        {(warnings.length > 0 || missing.length > 0) && (
          <div style={{ marginBottom: 14 }}>
            <div style={{
              fontSize: 9.5, color: C.textMuted, textTransform: "uppercase",
              letterSpacing: 1, fontWeight: 700, marginBottom: 6,
            }}>
              Why DIWO says so
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {warnings.map((warning) => (
                <div key={warning} style={{ fontSize: 11.5, color: C.textSub, display: "flex", gap: 7 }}>
                  <span aria-hidden="true" style={{ color: C.warn, fontWeight: 700 }}>⚠</span>
                  <span>{warning}</span>
                </div>
              ))}
              {missing.length > 0 && (
                <div style={{ fontSize: 11.5, color: C.textSub, display: "flex", gap: 7 }}>
                  <span aria-hidden="true" style={{ color: C.warn, fontWeight: 700 }}>⚠</span>
                  <span>Missing transformation input: {missing.join("; ")}</span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Optional. The workflow is never blocked on a reason. */}
        <div style={{ marginBottom: 16 }}>
          <div style={{
            fontSize: 9.5, color: C.textMuted, textTransform: "uppercase",
            letterSpacing: 1, fontWeight: 700, marginBottom: 7,
          }}>
            Why are you overriding? <span style={{ textTransform: "none", letterSpacing: 0 }}>(optional)</span>
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {OVERRIDE_REASONS.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setPicked(picked === option ? "" : option)}
                aria-pressed={picked === option}
                style={{
                  padding: "4px 11px", borderRadius: 20, fontSize: 10.5, fontWeight: 600,
                  cursor: "pointer",
                  background: picked === option ? `${C.info}25` : C.panel,
                  color: picked === option ? C.info : C.textMuted,
                  border: `1px solid ${picked === option ? C.info : C.border}`,
                }}
              >
                {option}
              </button>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={onCancel}
            style={{
              padding: "9px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700,
              cursor: "pointer", background: C.panel, color: C.textSub,
              border: `1px solid ${C.border}`,
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={confirm}
            style={{
              padding: "9px 18px", borderRadius: 8, fontSize: 12, fontWeight: 700,
              cursor: "pointer", border: "none",
              background: `${style.color}22`, color: style.color,
              boxShadow: `inset 0 0 0 1px ${style.color}66`,
            }}
          >
            {isManual ? "Force automatic anyway" : "Approve anyway"}
          </button>
        </div>

        <div style={{ marginTop: 11, fontSize: 10.5, color: C.textMuted, lineHeight: 1.5 }}>
          You keep the final say — DIWO advises, you decide. Nothing is
          transformed until you forward the plan.
        </div>
      </div>
    </div>
  );
}
